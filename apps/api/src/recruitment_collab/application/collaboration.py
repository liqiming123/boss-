from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recruitment_collab.config.settings import get_settings
from recruitment_collab.domain.enums import MatchLevel
from recruitment_collab.domain.normalization import (
    CandidateNameNormalizer,
    JobNameNormalizer,
    candidate_identity_signature,
    normalize_education,
    normalize_experience,
)
from recruitment_collab.domain.services import candidate_source_identity, conflict_key
from recruitment_collab.infrastructure.bitable import BitableSyncClient
from recruitment_collab.infrastructure.models import (
    AuditLog,
    CandidateSource,
    CandidateSyncOutbox,
    Conflict,
    ConflictExclusion,
    DuplicateLookupAlert,
    Engagement,
    FeishuBitableConfig,
    Interview,
    JobAlias,
    NotificationOutbox,
    Recruiter,
    RecruitmentAccount,
    RecruitmentEvent,
    RecruitmentJob,
    RecruitmentSetting,
    UnmappedJob,
    now,
)
from recruitment_collab.infrastructure.realtime import realtime_bus

logger = logging.getLogger(__name__)


class ApplicationError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _epoch(value: datetime) -> float:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()


def _iso_time(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).isoformat()
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
    return str(value)


# BOSS renders the first chat line in the same text run as the job label, so a
# scan reads back values such as `AI短视频内容生成师 BOSS您好,我具备岗位所需技能,…`
# or `直播助播 您好,我想和您沟通下…`. Left in place, that text is part of the
# candidate's identity key and of the "same job" comparison, so one candidate
# whose conversation keeps moving gains one row per message (张雨庭 ×4). Cut at
# the first boundary that starts a conversation rather than a title.
_JOB_CHAT_TAIL = re.compile(
    r"(?:"
    # A greeting, with or without the BOSS prefix and any spacing between.
    r"\s*(?:BOSS|boss)\s*(?=您好|你好|请问|在吗)"
    r"|[\s　]+(?:您好|你好|请问|期待|方便|我们|目前|我是|有意向|在吗|看到)"
    r"|[\s　]+(?:已读|未读|送达|求简历|换电话|换微信|最近关注|最近登录|最近沟通)"
    r"|[\s　]+\d{1,2}:\d{2}"
    r"|[，,。；;！!？?]"
    r")"
)


def _canonical_job_name(value: str) -> str:
    """Remove BOSS page metadata accidentally appended to the job title.

    A list row's text can leak into the extracted job, producing values such as
    ``短视频美妆达人 最近关注: 无锡 · 主播 6-11K 17:58 9月11日 沟通的职位-短视频美妆达人 送达``.
    That extra text must never become part of the candidate's identity.
    """
    text = str(value or "")
    for marker in (" 最近关注", "最近关注", " 沟通的职位", "沟通的职位", " 送达", "送达"):
        text = text.split(marker, 1)[0]
    # A leaked date/time tail means the list row was captured with the title.
    text = re.split(r"\s+\d{1,2}月\d{1,2}日", text, maxsplit=1)[0]
    chat = _JOB_CHAT_TAIL.search(text)
    # Never cut the whole value away: a title that is nothing but punctuation is
    # still more useful than an empty identity.
    if chat and chat.start() > 0:
        text = text[: chat.start()]
    return text.strip()


def _job_label_extends(stored: str, incoming: str) -> bool:
    """True when one job label is the other plus a chat/metadata tail.

    A label this code has never seen can still be the same job with extra text
    glued on. The leftover must itself look like that glue, so a plainly longer
    title (``运营`` vs ``运营专员``) stays a different job with its own row.
    """
    if not stored or not incoming or stored == incoming:
        return False
    shorter, longer = (stored, incoming) if len(stored) < len(incoming) else (incoming, stored)
    if len(shorter) < 2 or not longer.startswith(shorter):
        return False
    return bool(_JOB_CHAT_TAIL.search(longer[len(shorter) :]))


STATUS_RANK = {"沟通中": 0, "已获取简历": 1, "已交换联系方式": 2, "待约面": 3, "已约面": 4, "已拒绝": 5, "已入职": 6}
TERMINAL_STATUSES = {"已拒绝", "已入职"}
# The historical chat snapshot scrolls the recruiter's own conversation pane.
# Requesting it on every conversation open made the plugin fight the user for
# the scroll position, so only the confirmed interview invitation -- the action
# that actually advances the candidate to 已约面 -- may ask for a capture.
INVITE_STATUS = "已约面"
INVITE_EVIDENCE = {"BOSS_INTERVIEW_MARKER", "BOSS_INTERVIEW_INVITE"}


@dataclass(frozen=True)
class PageActorContext:
    company_id: str
    account: RecruitmentAccount
    recruiter: Recruiter

    @property
    def actor_id(self) -> str:
        return self.recruiter.id


@dataclass(frozen=True)
class MessageContext:
    page: PageActorContext
    job: RecruitmentJob | None
    normalized_job: str
    payload: dict[str, Any]


# Evidence names the extension uses when the recruiter actually handed out an
# interview invitation, as opposed to merely expressing intent.
INTERVIEW_INVITE_EVIDENCE = frozenset({"BOSS_INTERVIEW_MARKER", "BOSS_INTERVIEW_INVITE"})
# Rejection read from the conversation text rather than from a confirmed
# outbound bubble. That reading can be wrong (BOSS renders a “不合适” action
# button inside the conversation region), so a later invitation is allowed to
# replace it. A rejection confirmed as the recruiter's own outbound message
# keeps the old terminal behaviour.
PASSIVE_REJECTION_EVIDENCE = "EXPLICIT_REJECTION"


def _sort_epoch(value: Any) -> float:
    """Comparable timestamp for values that may be a datetime, an ISO string or
    a millisecond epoch, as returned by the local index and the Feishu table."""
    if value is None:
        return float("inf")
    if isinstance(value, datetime):
        return _epoch(value)
    if isinstance(value, (int, float)):
        # Bitable timestamps arrive in milliseconds.
        number = float(value)
        return number / 1000 if number > 1e11 else number
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("inf")


def _latest_epoch(value: Any) -> float:
    """Comparable activity timestamp where missing data sorts oldest."""
    parsed = _sort_epoch(value)
    return float("-inf") if parsed == float("inf") else parsed


def _newest_time(*values: Any) -> Any:
    """The newest of several optional timestamps, or None when none is usable.

    Used to rank recruiters by the moment they really talked to the candidate.
    ``None``/unparseable values lose against every real timestamp instead of
    silently falling back to an older field, which is what previously made a
    confirmed outbound message rank below a colleague's stored row time.
    """
    best: Any = None
    best_epoch = float("-inf")
    for value in values:
        epoch = _latest_epoch(value)
        if epoch > best_epoch:
            best, best_epoch = value, epoch
    return best


def _card_detail(name: str, job_name: str, match_reason: str, first_contact_at: Any, last_activity_at: Any) -> dict[str, Any]:
    """One "other recruiter" line of the duplicate-lookup card."""
    return {
        "recruiter_name": name,
        "job_name": job_name,
        "match_reason": match_reason,
        "first_contact_at": _iso_time(first_contact_at),
        "last_activity_at": _iso_time(last_activity_at),
    }


def _profile_conflicts(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """True when two observations cannot be the same person.

    Only ``age`` is treated as a durable fact. Experience and education are
    read from whichever resume section happens to be rendered and drift between
    scans on the very same person (``26届`` vs ``26年``, ``3年`` vs a degree
    line), so they are supporting evidence for a match, never grounds to split
    it into two rows.
    """
    first = left.get("age")
    second = right.get("age")
    return bool(first and second and str(first) != str(second))


def _accepted_status(
    current: str,
    incoming: str | None,
    evidence: str | None,
    current_rule_version: str = "boss-status-v1",
    incoming_rule_version: str | None = None,
    current_evidence: str | None = None,
) -> tuple[str, str | None] | None:
    if not incoming or not evidence:
        return None
    # v1 could incorrectly persist interview intent found in candidate text.
    # A later observation with recruiter outbound evidence must repair that
    # stale non-terminal value back to the neutral communication state.
    if evidence == "RECRUITER_OUTBOUND":
        stale_false_positive = current in {"待约面", "已拒绝"} and current_rule_version in {
            "boss-status-v1",
            "boss-status-v2",
        } and incoming_rule_version == "boss-status-v3"
        return ("沟通中", evidence) if stale_false_positive else None
    if current in TERMINAL_STATUSES:
        if incoming in TERMINAL_STATUSES and incoming != current:
            return incoming, evidence
        # The candidate was marked 已拒绝 from the conversation text, but the
        # recruiter has since sent an interview invitation. The invitation is
        # the newer business fact; without this the candidate stayed 已拒绝
        # forever and never reached the table as 已约面.
        if incoming == "已约面" and evidence in INTERVIEW_INVITE_EVIDENCE and current_evidence == PASSIVE_REJECTION_EVIDENCE:
            return incoming, evidence
        return None
    if incoming in TERMINAL_STATUSES or STATUS_RANK.get(incoming, -1) >= STATUS_RANK.get(current, 0):
        return incoming, evidence
    return None


class RecruitmentCollaborationService:
    def __init__(self, session: Session):
        self.session = session

    def check_context(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        account, current_recruiter = self._page_identity(company_id, payload, create=False)
        actor_id = current_recruiter.id if current_recruiter else f"unregistered:{self._page_identity_hash(company_id, payload)}"
        recruiter_label = self._recruiter_label(current_recruiter, payload["account_display_name"])
        job, normalized_job = self._resolve_job(company_id, payload)
        probe = CandidateSource(
            company_id=company_id,
            platform=payload["platform"],
            platform_account_id=account.id if account else None,
            source_identity_key="probe",
            source_identity_type="PROBE",
            platform_candidate_id=payload.get("platform_candidate_id"),
            platform_id_scope=payload.get("platform_id_scope", "UNKNOWN"),
            page_url_hash="",
            candidate_display_name=payload["candidate_display_name"],
            candidate_normalized_name=CandidateNameNormalizer().normalize(payload["candidate_display_name"]),
            raw_job_name=payload["job_display_name"],
            job_id=job.id if job else None,
            extractor_version=payload["extractor_version"],
        )
        signature = self._identity_signature(payload)
        # Duplicate evidence is kept in PostgreSQL.  Feishu is the durable
        # business table, but it intentionally no longer receives opaque
        # identifiers/signatures, so the API must not depend on those columns
        # being present for a lookup to work.
        probe.candidate_age = payload.get("candidate_age")
        probe.candidate_experience = normalize_experience(payload.get("candidate_experience") or "")
        probe.candidate_education = normalize_education(payload.get("candidate_education") or "")
        probe.candidate_identity_signature = signature
        feishu_available = True
        matches = self._find_matches(probe, actor_id, company_id, job.category if job else None)
        if get_settings().feishu_mode == "real":
            try:
                legacy_matches = self._feishu_matches(
                    company_id,
                    signature,
                    payload["candidate_display_name"],
                    payload["job_display_name"],
                    job.canonical_name if job else "",
                    recruiter_label,
                    payload.get("candidate_age"),
                    payload.get("candidate_experience") or "",
                    payload.get("candidate_education") or "",
                    payload.get("account_display_name") or "",
                )
                # Keep compatibility with rows written before the field
                # projection was reduced. New rows are matched from the
                # backend and therefore do not require technical Feishu
                # columns. Avoid returning the same backend row twice.
                seen = {str(item.get("candidate_source_id")) for item in matches}
                matches.extend(item for item in legacy_matches if str(item.get("candidate_source_id")) not in seen)
            except Exception as exc:
                if not payload.get("native_communications") and not matches:
                    raise ApplicationError("FEISHU_LOOKUP_UNAVAILABLE", "飞书查重暂不可用，请稍后重试", 503) from exc
                feishu_available = False
        else:
            pass
        current_aliases = {
            str(payload.get("account_display_name") or ""),
            recruiter_label,
            current_recruiter.display_name if current_recruiter else "",
            current_recruiter.feishu_display_name if current_recruiter and current_recruiter.feishu_display_name else "",
        }
        current_jobs = {payload["job_display_name"], job.canonical_name if job else ""}
        matches = self._merge_native_matches(payload.get("native_communications") or [], current_aliases, current_jobs, matches, feishu_available)
        queued = self._queue_lookup_alerts(company_id, current_recruiter, payload, matches)
        if queued:
            self.session.commit()
        result = self._response(None, account, job, current_recruiter, matches)
        result["lookup_notifications_queued"] = queued
        result["feishu_lookup_status"] = "AVAILABLE" if feishu_available else "UNAVAILABLE"
        result["result_type"] = result["result_type"] if matches else "CHECK_ONLY_NO_HISTORY"
        result["ui"] = result["ui"] if matches else {"severity": "success", "title": "检查完成", "message": "未发现其他招聘账号的同步记录"}
        return result

    @staticmethod
    def _merge_native_matches(
        native_rows: list[dict[str, Any]], current_recruiter_aliases: set[str], current_job_names: set[str],
        feishu_matches: list[dict[str, Any]], feishu_available: bool
    ) -> list[dict[str, Any]]:
        normalize_job = JobNameNormalizer().normalize
        normalize_name = CandidateNameNormalizer().normalize
        current = {normalize_name(value) for value in current_recruiter_aliases if value.strip()}
        current_jobs = {normalize_job(value) for value in current_job_names if value.strip()}
        by_key = {(normalize_name(item["recruiter_name"]), normalize_job(item.get("job_name") or "")): item for item in feishu_matches}
        merged: list[dict[str, Any]] = []
        consumed: set[int] = set()
        for row in native_rows:
            recruiter = str(row.get("recruiter_name") or "").strip()
            job_name = str(row.get("job_name") or "").strip()
            same_owner_and_job = normalize_name(recruiter) in current and normalize_job(job_name) in current_jobs
            if not recruiter or not job_name or same_owner_and_job:
                continue
            key = (normalize_name(recruiter), normalize_job(job_name))
            feishu = by_key.get(key)
            if feishu:
                consumed.add(id(feishu))
                item = {
                    **feishu,
                    "match_level": "CONFIRMED_BOSS_HISTORY",
                    "evidence_source": "BOSS_AND_FEISHU",
                    "feishu_synced": True,
                    "match_reason": "BOSS 原生同事沟通记录已确认，且飞书存在系统记录",
                }
                if not item.get("updated_at"):
                    item["updated_at"] = row.get("contacted_at")
            else:
                digest = hashlib.sha256(f"{recruiter}|{job_name}|{row.get('contacted_at')}".encode()).hexdigest()[:32]
                item = {
                    "match_level": "CONFIRMED_BOSS_HISTORY",
                    "candidate_source_id": f"boss-native:{digest}",
                    "recruiter_id": f"boss-native:{normalize_name(recruiter)}",
                    "recruiter_name": recruiter,
                    "job_id": None,
                    "job_name": job_name,
                    "stage": "飞书未同步" if feishu_available else "飞书状态暂不可核验",
                    "updated_at": row.get("contacted_at"),
                    "match_reason": "BOSS 原生同事沟通记录已确认；飞书未找到对应系统行"
                    if feishu_available
                    else "BOSS 原生同事沟通记录已确认；飞书查询暂不可用",
                    "evidence_source": "BOSS_NATIVE",
                    "feishu_synced": False,
                    "history": {"contacted": True, "interviewed": False, "rejected": False, "event_types": []},
                }
            merged.append(item)
        merged.extend(
            item for item in feishu_matches
            if id(item) not in consumed and not (
                normalize_name(str(item.get("recruiter_name") or "")) in current
                and normalize_job(str(item.get("job_name") or "")) in current_jobs
            )
        )
        return sorted(merged, key=lambda item: (item.get("match_level") == "CONFIRMED_BOSS_HISTORY", str(item.get("updated_at") or "")), reverse=True)

    @staticmethod
    def _identity_signature(payload: dict[str, Any]) -> str | None:
        age, experience, education = payload.get("candidate_age"), payload.get("candidate_experience"), payload.get("candidate_education")
        if age is None or not str(experience or "").strip() or not str(education or "").strip():
            return None
        return candidate_identity_signature(payload["candidate_display_name"], int(age), str(experience), str(education))

    @staticmethod
    def _recruiter_label(recruiter: Recruiter | None, fallback: str = "") -> str:
        """Use the bound Feishu identity in user-facing records.

        The BOSS page display name is retained only as a technical account
        mapping key; it must not leak into the candidate table when a Feishu
        identity is bound.
        """
        return (recruiter.feishu_display_name if recruiter and recruiter.feishu_display_name else (recruiter.display_name if recruiter else fallback)).strip()

    def _feishu_matches(
        self, company_id: str, signature: str | None, candidate_name: str, job_name: str, canonical_job_name: str,
        current_recruiter: str, candidate_age: int | None = None, candidate_experience: str = "", candidate_education: str = "",
        current_boss_account: str = ""
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        config = self.session.scalar(
            select(FeishuBitableConfig).where(FeishuBitableConfig.company_id == company_id, FeishuBitableConfig.status == "ACTIVE")
        )
        client = BitableSyncClient(
            settings,
            app_token=config.app_token if config else None,
            candidate_table_id=config.candidate_table_id if config else None,
        )
        rows = client.find_system_candidates(
            signature, current_recruiter, candidate_name, job_name, canonical_job_name,
            candidate_age, candidate_experience, candidate_education, current_boss_account,
        )
        matches = []
        for row in rows:
            updated_at = row.get("更新时间")
            status = client.plain_value(row.get("状态")) or "沟通中"
            matches.append(
                {
                    "match_level": row.get("match_level", "EXACT_IDENTITY"),
                    "candidate_source_id": str(row.get("record_id") or client.plain_value(row.get("系统记录标识"))),
                    # Bitable stores the human recruiter label, not the
                    # database UUID. Resolve it inside the company before
                    # queuing notifications instead of passing a name to a
                    # UUID lookup.
                    "recruiter_id": None,
                    "recruiter_name": client.plain_value(row.get("飞书账号") or row.get("当前招聘者")),
                    "job_id": None,
                    "job_name": client.plain_value(row.get("BOSS岗位") or row.get("标准岗位")),
                    "stage": status,
                    "updated_at": updated_at,
                    "first_contact_at": _iso_time(row.get("开始聊天时间")),
                    "last_activity_at": _iso_time(updated_at),
                    "match_reason": "姓名、年龄、工作年限、学历完全一致（飞书系统记录）"
                    if row.get("match_level") != "SUSPECTED_SAME_NAME_JOB"
                    else "标准化姓名和岗位一致（飞书系统记录）",
                    "history": {"contacted": True, "interviewed": status == "已约面", "rejected": status == "已拒绝", "event_types": []},
                }
            )
        return sorted(matches, key=lambda item: (item["match_level"] == "EXACT_IDENTITY", item.get("updated_at") or 0), reverse=True)

    def _queue_lookup_alerts(self, company_id: str, viewer: Any, payload: dict[str, Any], matches: list[dict[str, Any]]) -> int:
        if not viewer or not getattr(viewer, "id", None):
            return 0
        ranks = {"SUSPECTED_SAME_NAME_JOB": 1, "EXACT_IDENTITY": 2, "CONFIRMED_BOSS_HISTORY": 3}
        identity = (
            self._identity_signature(payload) or hashlib.sha256(CandidateNameNormalizer().normalize(payload["candidate_display_name"]).encode()).hexdigest()
        )
        normalized_job = JobNameNormalizer().normalize(_canonical_job_name(payload["job_display_name"]))
        queued_count = 0
        current_time = now()
        viewer_label = self._recruiter_label(viewer, viewer.display_name)
        # One candidate, one listener, one card: every colleague who already
        # holds the candidate is listed in the same message. Alert rows are
        # still per colleague (they carry the cooldown and the audit trail),
        # but the notification is queued once per lookup instead of once per
        # colleague — a three-colleague hit used to send three identical cards.
        group: list[dict[str, Any]] = []
        for match in matches:
            rank = ranks.get(str(match.get("match_level")))
            if not rank:
                continue
            matched_name = str(match.get("recruiter_name") or "").strip()
            if not matched_name or CandidateNameNormalizer().normalize(matched_name) == CandidateNameNormalizer().normalize(viewer.display_name):
                continue
            matched = self.session.get(Recruiter, match.get("recruiter_id")) if match.get("recruiter_id") else None
            if not matched:
                matched = self._recruiter_by_label(company_id, matched_name)
            # BOSS may show the account nickname while its native history shows
            # the bound Feishu real name. Resolve both labels before deciding
            # that there are two recruiters; otherwise 李先生/李启明 becomes a
            # false self-duplicate. A same-job self row is pure noise and is
            # suppressed; a cross-job self row is kept — it is a useful "you
            # already followed this person under another job" reminder, and
            # the card/UI copy marks it as the viewer's own history.
            same_job = JobNameNormalizer().normalize(_canonical_job_name(str(match.get("job_name") or ""))) == normalized_job
            if matched and matched.id == viewer.id and same_job:
                continue
            viewer_aliases = {
                CandidateNameNormalizer().normalize(value)
                for value in (viewer.display_name, viewer.feishu_display_name or "", viewer_label)
                if value
            }
            if CandidateNameNormalizer().normalize(matched_name) in viewer_aliases and same_job:
                continue
            target_key = matched.id if matched else CandidateNameNormalizer().normalize(matched_name)
            # A candidate's identity is independent of the job they were viewed under.
            # Omitting the job prevents补扫 across multiple岗位 from producing
            # duplicate alerts for the same viewer/colleague pair.
            alert_key = hashlib.sha256(f"{company_id}|{viewer.id}|{target_key}|{identity}".encode()).hexdigest()
            alert = self._activate_lookup_alert(
                DuplicateLookupAlert(
                    company_id=company_id,
                    viewer_recruiter_id=viewer.id,
                    matched_recruiter_id=matched.id if matched else None,
                    matched_recruiter_name=matched_name,
                    candidate_identity_hash=identity,
                    normalized_job_name=normalized_job,
                    alert_key=alert_key,
                    match_level=str(match["match_level"]),
                    evidence_rank=rank,
                    match_reason=str(match.get("match_reason") or "发现重复候选人证据"),
                ),
                current_time,
                # Only a genuine unread→read transition (a real, time-sensitive
                # signal) bypasses the alert cooldown. A reconciliation pass
                # merely observed a newer list time; treating that as "unread"
                # pinged colleagues on every poll.
                force_notify=payload.get("sync_reason") == "UNREAD_CANDIDATE_OPENED",
            )
            if not alert:
                # Cooldown or a racing request: this colleague is already known
                # to the viewer, so the card has nothing new to say.
                continue
            alert.notification_version += 1
            first_contact_at = match.get("first_contact_at")
            last_activity_at = match.get("last_activity_at") or match.get("updated_at")
            group.append(
                {
                    "alert": alert,
                    "matched": matched,
                    "name": matched_name,
                    "rank": rank,
                    "match_level": str(match["match_level"]),
                    "match_reason": str(match.get("match_reason") or "发现重复候选人证据"),
                    "first_contact_at": first_contact_at,
                    "last_activity_at": last_activity_at,
                    "job_name": str(match.get("job_name") or ""),
                    "stage": match.get("stage") or "沟通中",
                }
            )
        if not group:
            return 0
        # The same colleague can be observed twice — once in the local index and
        # once in the Feishu table — and those are one person, one card line.
        deduped_group: dict[str, dict[str, Any]] = {}
        for item in group:
            key = item["matched"].id if item["matched"] else f"unresolved:{CandidateNameNormalizer().normalize(item['name'])}"
            existing = deduped_group.get(key)
            if existing is None or (item["rank"], -_sort_epoch(item["first_contact_at"])) > (existing["rank"], -_sort_epoch(existing["first_contact_at"])):
                deduped_group[key] = item
        group = list(deduped_group.values())
        # The card lists every colleague; the strongest evidence leads, and ties
        # go to whoever spoke to the candidate first.
        group.sort(key=lambda item: (-item["rank"], _sort_epoch(item["first_contact_at"])))
        primary = group[0]
        alert = primary["alert"]
        # Notify exactly one person: the recruiter whose *real* conversation with
        # this candidate is the newest. A confirmed outbound message is a real
        # conversation and therefore always counts — the send instant used to be
        # ignored, so a colleague whose stored row was merely re-read later
        # looked "newer" than the recruiter writing to the candidate right now,
        # and the reminder was delivered to the wrong person.
        participants: dict[str, dict[str, Any]] = {
            viewer.id: {
                "recruiter": viewer,
                "name": viewer_label,
                "is_viewer": True,
                "last_activity_at": _newest_time(
                    payload.get("sent_at"),
                    payload.get("conversation_updated_at"),
                    payload.get("conversation_started_at"),
                ),
                "first_contact_at": payload.get("conversation_started_at"),
                "job_name": payload["job_display_name"],
                "match_reason": "该同事刚刚沟通了同一候选人" if payload.get("sent_at") else "该同事正在查看该候选人",
            }
        }
        for item in group:
            recruiter = item["matched"]
            candidate = {
                "recruiter": recruiter,
                "name": item["name"],
                "is_viewer": False,
                "last_activity_at": item["last_activity_at"] or item["first_contact_at"],
                "first_contact_at": item["first_contact_at"],
                "job_name": item["job_name"] or payload["job_display_name"],
                "match_reason": item["match_reason"],
            }
            participant_key = recruiter.id if recruiter else f"unresolved:{CandidateNameNormalizer().normalize(item['name'])}"
            previous = participants.get(participant_key)
            if previous is None or _latest_epoch(candidate["last_activity_at"]) > _latest_epoch(previous["last_activity_at"]):
                participants[participant_key] = candidate
        cross_job = any(
            JobNameNormalizer().normalize(_canonical_job_name(str(item.get("job_name") or ""))) != normalized_job
            for item in matches
            if item.get("job_name")
        )
        if len(participants) < 2 and not cross_job:
            return 0
        # On an exact timestamp tie, prefer the current page owner: this is
        # deterministic and avoids pinging a colleague based on indistinguishable
        # data. Otherwise the greatest real conversation timestamp wins.
        latest = max(
            participants.values(),
            key=lambda item: (_latest_epoch(item["last_activity_at"]), bool(item["is_viewer"])),
        )
        # A native BOSS label that has not been bound to a Feishu recruiter has
        # no safe direct-message target. Keep the in-page warning, but never
        # redirect their alert to a different person merely because that person
        # is the only resolvable account.
        if latest["recruiter"] is None:
            return 0
        # A cross-job row that belongs to the viewer themself is a useful
        # "you already followed this person elsewhere" reminder, not a
        # colleague conflict — mark it so the card never reads like one.
        viewer_account_aliases = {
            CandidateNameNormalizer().normalize(value)
            for value in (viewer.display_name, viewer.feishu_display_name or "", viewer_label, payload.get("account_display_name") or "")
            if value
        }
        own_group = [
            item for item in group
            if (item["matched"] is not None and item["matched"].id == viewer.id)
            or CandidateNameNormalizer().normalize(item["name"]) in viewer_account_aliases
        ]
        own_history = bool(own_group) and len(own_group) == len(group) and latest["is_viewer"]
        # The card always describes somebody other than its recipient. When the
        # newest real conversation belongs to a colleague, that colleague is the
        # one being warned, and the recruiter on this page is who they are warned
        # about: the recipient's own name must never be echoed back as the
        # colleague who "already" holds the candidate.
        if latest["is_viewer"]:
            others = [
                _card_detail(item["name"], item["job_name"] or payload["job_display_name"], item["match_reason"], item["first_contact_at"], item["last_activity_at"])
                for item in (group if own_history else [item for item in group if item not in own_group])
            ]
            lead_recruiter_id = primary["matched"].id if primary["matched"] else None
        else:
            others = [
                _card_detail(entry["name"], entry["job_name"], entry["match_reason"], entry["first_contact_at"], entry["last_activity_at"])
                for entry in participants.values()
                if entry is not latest
            ]
            lead_recruiter_id = viewer.id
        if not others:
            return 0
        lead = others[0]
        payload_json = {
            "type": "DUPLICATE_LOOKUP",
            "lookup_alert_id": alert.id,
            "candidate_name": payload["candidate_display_name"],
            "job_name": payload["job_display_name"],
            "viewer_name": viewer_label,
            "recipient_is_viewer": bool(latest["is_viewer"]),
            "trigger": "SEND" if payload.get("sent_at") else "BROWSE",
            "matched_recruiter_name": lead["recruiter_name"],
            "matched_recruiter_names": [item["recruiter_name"] for item in others],
            "matched_recruiter_details": others,
            "matched_colleague_count": len(others),
            "first_contact_at": lead["first_contact_at"],
            "last_activity_at": lead["last_activity_at"],
            "current_recruiter_status": payload.get("recruitment_status") or "待建立跟进记录",
            "candidate_status": "沟通中" if primary.get("stage") == "FOLLOWING" else (primary.get("stage") or "沟通中"),
            "match_level": alert.match_level,
            "match_reason": lead["match_reason"],
            "own_history": own_history,
        }
        # One card per lookup, always to the recruiter whose real conversation is
        # the newest. A colleague is only chosen when that colleague really spoke
        # to the candidate more recently than the person on the page.
        recipients = {latest["recruiter"].id}
        created = self._queue_notifications(
            company_id,
            "DUPLICATE_LOOKUP",
            "lookup_alert",
            alert.id,
            payload_json,
            recipients,
            f"lookup:{alert.id}:v{alert.notification_version}",
        )
        if created:
            # Every alert in the group shares this notification, so each keeps
            # its own cooldown in step and will not re-fire on the next poll.
            for item in group:
                item["alert"].last_notified_at = current_time
            queued_count += created
            realtime_bus.publish(
                company_id,
                {
                    "event": "duplicate_lookup",
                    "recipient_recruiter_id": latest["recruiter"].id,
                    "lookup_alert_id": alert.id,
                    "candidate_name": payload["candidate_display_name"],
                    "candidate_identity_hash": identity,
                    "job_name": payload["job_display_name"],
                    "viewer_recruiter_id": viewer.id,
                    "viewer_name": viewer_label,
                    "matched_recruiter_id": lead_recruiter_id,
                    "matched_recruiter_name": lead["recruiter_name"],
                    "match_level": alert.match_level,
                    "match_reason": alert.match_reason,
                    "first_contact_at": lead["first_contact_at"],
                    "last_activity_at": lead["last_activity_at"],
                    "notification_version": alert.notification_version,
                    "own_history": own_history,
                },
            )
        return queued_count

    def _recruiter_by_label(self, company_id: str, label: str) -> Recruiter | None:
        """Resolve a table-facing recruiter label to the active identity.

        Prefer a bound Feishu display name because candidate-table rows use
        that label. Fall back to the BOSS/page display name for legacy rows.
        """
        normalized = label.strip()
        if not normalized:
            return None
        bound = self.session.scalar(
            select(Recruiter).where(
                Recruiter.company_id == company_id,
                Recruiter.status == "ACTIVE",
                Recruiter.feishu_display_name == normalized,
            )
        )
        if bound:
            return bound
        return self.session.scalar(
            select(Recruiter).where(
                Recruiter.company_id == company_id,
                Recruiter.status == "ACTIVE",
                Recruiter.display_name == normalized,
            )
        )

    def _activate_lookup_alert(self, draft: DuplicateLookupAlert, detected_at: datetime, force_notify: bool = False) -> DuplicateLookupAlert | None:
        alert = self.session.scalar(select(DuplicateLookupAlert).where(DuplicateLookupAlert.alert_key == draft.alert_key))
        if alert:
            alert.hit_count += 1
            alert.last_detected_at = detected_at
            last_notified = alert.last_notified_at
            if last_notified and not last_notified.tzinfo:
                last_notified = last_notified.replace(tzinfo=timezone.utc)
            # Cross-account duplicate evidence is actionable immediately: the
            # viewer is sitting on the candidate right now. Re-notify as soon
            # as new evidence appears, or after the short configurable window
            # that only exists to stop the same person re-opening the same
            # candidate from generating a stream of identical messages.
            cooldown = timedelta(minutes=max(0, get_settings().lookup_alert_cooldown_minutes))
            cooldown_elapsed = not last_notified or not cooldown or detected_at - last_notified >= cooldown
            if draft.evidence_rank <= alert.evidence_rank and not cooldown_elapsed and not force_notify:
                return None
            alert.match_level = draft.match_level
            alert.evidence_rank = draft.evidence_rank
            alert.match_reason = draft.match_reason
            alert.matched_recruiter_id = draft.matched_recruiter_id
            return alert
        try:
            with self.session.begin_nested():
                self.session.add(draft)
                self.session.flush()
            return draft
        except IntegrityError:
            # The concurrent request that won the unique key owns notification
            # creation; this request only records another observation.
            alert = self.session.scalar(select(DuplicateLookupAlert).where(DuplicateLookupAlert.alert_key == draft.alert_key))
            if not alert:
                raise
            alert.hit_count += 1
            alert.last_detected_at = detected_at
            return None

    def _queue_notifications(
        self,
        company_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        recruiter_ids: set[str] | tuple[str, str],
        key_prefix: str,
    ) -> int:
        created = 0
        for recruiter_id in set(recruiter_ids):
            recipient = self.session.get(Recruiter, recruiter_id)
            idempotency_key = f"{key_prefix}:{recruiter_id}"
            exists = self.session.scalar(select(NotificationOutbox).where(NotificationOutbox.idempotency_key == idempotency_key))
            if not recipient or not recipient.feishu_open_id or exists:
                continue
            self.session.add(
                NotificationOutbox(
                    company_id=company_id,
                    event_type=event_type,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    recipient_recruiter_id=recruiter_id,
                    payload_json=payload,
                    idempotency_key=idempotency_key,
                )
            )
            created += 1
        return created

    @staticmethod
    def _page_identity_hash(company_id: str, payload: dict[str, Any]) -> str:
        name = (payload.get("account_display_name") or "").strip()
        return hashlib.sha256(f"{company_id}|{payload['platform']}|{name}".encode()).hexdigest()

    def _page_identity(self, company_id: str, payload: dict[str, Any], create: bool) -> tuple[RecruitmentAccount | None, Recruiter | None]:
        display_name = (payload.get("account_display_name") or "").strip()
        if not display_name:
            raise ApplicationError("PAGE_RECRUITER_NOT_FOUND", "未识别 BOSS 右上角招聘人员姓名")
        account = self.session.scalar(
            select(RecruitmentAccount).where(
                RecruitmentAccount.company_id == company_id,
                RecruitmentAccount.platform == payload["platform"],
                RecruitmentAccount.account_display_name == display_name,
                RecruitmentAccount.status == "ACTIVE",
            )
        )
        if account:
            return account, self.session.get(Recruiter, account.recruiter_id)
        same_name_recruiters = self.session.scalars(
            select(Recruiter)
            .where(
                Recruiter.company_id == company_id,
                Recruiter.display_name == display_name,
                Recruiter.status == "ACTIVE",
            )
            .limit(2)
        ).all()
        if len(same_name_recruiters) > 1:
            raise ApplicationError("PAGE_RECRUITER_AMBIGUOUS", "同名招聘账号存在多条身份记录，请管理员先合并", 409)
        recruiter = same_name_recruiters[0] if same_name_recruiters else None
        if not create:
            return None, recruiter
        identity_hash = self._page_identity_hash(company_id, payload)
        if not recruiter:
            recruiter = Recruiter(
                company_id=company_id,
                display_name=display_name,
                email=f"boss-page-{identity_hash[:24]}@identity.invalid",
                role="BOSS_RECRUITER",
                password_hash="PAGE_IDENTITY_CANNOT_LOGIN",
            )
            self.session.add(recruiter)
            self.session.flush()
        account = RecruitmentAccount(
            company_id=company_id,
            recruiter_id=recruiter.id,
            platform=payload["platform"],
            platform_account_key=f"page-name:{identity_hash}",
            account_display_name=display_name,
        )
        self.session.add(account)
        self.session.flush()
        return account, recruiter

    def page_recruiter(self, company_id: str, display_name: str, create: bool = False) -> Recruiter | None:
        """Resolve a BOSS page identity without coupling OAuth routes to persistence details."""
        return self._page_identity(company_id, {"platform": "boss", "account_display_name": display_name}, create)[1]

    def _store_invited_interview(
        self,
        source: CandidateSource,
        page: "PageActorContext",
        job: RecruitmentJob | None,
        payload: dict[str, Any],
    ) -> None:
        """Persist the optional interview schedule without risking the status.

        The 已约面 status is the business fact that must always synchronize; the
        calendar entry is a bonus read from BOSS's dialog. A savepoint keeps a
        failure here from aborting or rolling back the invitation itself.
        """
        try:
            with self.session.begin_nested():
                self._upsert_invited_interview(source, page, job, payload)
        except Exception as exc:  # defensive isolation, never silent to logs
            logger.warning(
                "interview schedule was not stored for candidate source %s: %s",
                source.id,
                type(exc).__name__,
            )

    def _upsert_invited_interview(
        self,
        source: CandidateSource,
        page: "PageActorContext",
        job: RecruitmentJob | None,
        payload: dict[str, Any],
    ) -> None:
        """Persist the interview the recruiter scheduled in BOSS's dialog.

        Only a fully resolved date and time is stored: an invitation whose
        schedule could not be read must never fabricate a calendar entry. The
        write is idempotent per candidate source so a repeated send updates the
        same row instead of stacking duplicates.
        """
        details = payload.get("interview") or {}
        scheduled_at = details.get("scheduled_at")
        if not scheduled_at:
            return
        row = self.session.scalar(
            select(Interview).where(
                Interview.company_id == page.company_id,
                Interview.candidate_source_id == source.id,
            )
        )
        if row is None:
            row = Interview(
                company_id=page.company_id,
                candidate_source_id=source.id,
                recruiter_id=page.actor_id,
                scheduled_at=scheduled_at,
            )
            self.session.add(row)
        row.job_id = source.job_id or (job.id if job else None)
        row.recruiter_id = page.actor_id
        row.scheduled_at = scheduled_at
        row.location_type = details.get("interview_type") or row.location_type or "ONLINE"
        location = str(details.get("location") or "").strip()
        if location:
            row.location_text = location
        row.status = "SCHEDULED"
        if not row.notes:
            row.notes = "BOSS 面试邀约自动识别"
        self.session.flush()

    def record_message_sent(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        account, current_recruiter = self._page_identity(company_id, payload, create=True)
        if not account or not current_recruiter:  # create=True guarantees both; keeps the invariant explicit to type checkers.
            raise ApplicationError("PAGE_RECRUITER_NOT_FOUND", "无法建立 BOSS 招聘人员身份", 500)
        page = PageActorContext(company_id, account, current_recruiter)
        existing = self._existing_message_response(page, payload)
        if existing:
            return existing

        job, normalized_job = self._resolve_job(company_id, payload, track_unmapped=True)
        # A confirmed send is real-time duplicate evidence, not a table write.
        # Rows, conversation times, follow-up status and the chat long image
        # belong to the click-sync and the scheduled history pass. The send
        # path used to upsert its own row here; BOSS renders the profile card
        # inconsistently between sends, so the derived identity differed and
        # one candidate accumulated one row per send (张雨庭 ×3). Match with a
        # transient probe — the same shape the read-only context check uses —
        # and leave the shared table to the sync paths.
        probe = CandidateSource(
            company_id=company_id,
            platform=payload["platform"],
            platform_account_id=account.id,
            source_identity_key="probe",
            source_identity_type="PROBE",
            platform_candidate_id=payload.get("platform_candidate_id"),
            platform_id_scope=payload.get("platform_id_scope", "UNKNOWN"),
            page_url_hash="",
            candidate_display_name=payload["candidate_display_name"],
            candidate_normalized_name=CandidateNameNormalizer().normalize(payload["candidate_display_name"]),
            raw_job_name=_canonical_job_name(payload["job_display_name"]),
            job_id=job.id if job else None,
            extractor_version=payload["extractor_version"],
        )
        signature = self._identity_signature(payload)
        probe.candidate_age = payload.get("candidate_age")
        probe.candidate_experience = normalize_experience(payload.get("candidate_experience") or "")
        probe.candidate_education = normalize_education(payload.get("candidate_education") or "")
        probe.candidate_identity_signature = signature
        matches = self._find_matches(probe, page.actor_id, company_id, job.category if job else None)
        # Re-run the notification path after a confirmed outbound message.
        # The pre-send context check is best-effort; the send event is the
        # authoritative point at which any cross-recruiter match must be
        # surfaced to the user.
        lookup_notifications_queued = self._queue_lookup_alerts(company_id, current_recruiter, payload, matches)
        # The audit event attaches to the row this candidate already has, and
        # the send may refresh that row's conversation time. A send never
        # creates a row, never advances status and never asks for a screenshot:
        # rows belong to the click-sync and the history pass, and a candidate
        # not yet synced simply has nothing to update — the next sync or
        # history pass records it.
        context = MessageContext(page, job, normalized_job, payload)
        url_hash = hashlib.sha256(payload["page_url"].encode()).hexdigest()
        source_scope = page.actor_id if payload["platform"] == "boss" else page.account.id
        identity = candidate_source_identity(
            payload["platform"],
            source_scope,
            signature or payload["candidate_display_name"],
            _canonical_job_name(payload["job_display_name"]),
            "" if payload["platform"] == "boss" else url_hash,
            payload.get("platform_candidate_id"),
        )
        source = self._find_candidate_source(context, identity, probe.candidate_normalized_name, signature)
        if source:
            source.conversation_updated_at = max(
                (value for value in (source.conversation_updated_at, payload["sent_at"]) if value),
                key=_epoch,
            )
            self.session.add(
                RecruitmentEvent(
                    company_id=company_id,
                    candidate_source_id=source.id,
                    job_id=source.job_id,
                    recruiter_id=page.actor_id,
                    event_type="MESSAGE_SENT",
                    event_time=payload["sent_at"],
                    source="PLUGIN",
                    idempotency_key=payload["client_event_id"],
                    metadata_json={},
                )
            )
            self.session.flush()
        result = self._response(source, account, job, current_recruiter, matches) if source else self._response(None, account, job, current_recruiter, matches)
        # A send never asks for a screenshot and never creates a row: the
        # history pass captures every read conversation and owns the table.
        result["snapshot_needed"] = False
        result["lookup_notifications_queued"] = lookup_notifications_queued
        # The route's session is not committed anywhere else: without this the
        # audit event, the refreshed conversation time and any queued alert
        # card would be rolled back when the request closes the session.
        self.session.commit()
        return result

    def sync_candidate_observation(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Click-to-sync: persist a candidate opened in BOSS without claiming a message was sent.

        The shared table follows the conversation: an unknown candidate gets its
        row, and a newer conversation time refreshes that row's 更新时间. The
        observation still creates no engagement, no ``MESSAGE_SENT`` event and no
        screenshot; real outbound messages go through ``record_message_sent``.
        """
        account, current_recruiter = self._page_identity(company_id, payload, create=True)
        if not account or not current_recruiter:
            raise ApplicationError("PAGE_RECRUITER_NOT_FOUND", "无法建立 BOSS 招聘人员身份", 500)
        page = PageActorContext(company_id, account, current_recruiter)
        job, normalized_job = self._resolve_job(company_id, payload, track_unmapped=True)
        context = MessageContext(page, job, normalized_job, payload)
        normalized_name = CandidateNameNormalizer().normalize(payload["candidate_display_name"])
        prior_candidates = [
            row
            for row in self.session.scalars(
                select(CandidateSource).where(
                    CandidateSource.company_id == company_id,
                    CandidateSource.platform == payload["platform"],
                    CandidateSource.platform_account_id == account.id,
                    CandidateSource.candidate_normalized_name == normalized_name,
                )
            ).all()
            if JobNameNormalizer().normalize(_canonical_job_name(row.raw_job_name)) == normalized_job
        ]
        prior = max(
            prior_candidates,
            key=lambda row: _epoch(row.conversation_updated_at) if row.conversation_updated_at else float("-inf"),
            default=None,
        )
        prior_updated_at = prior.conversation_updated_at if prior else None
        source, _started_at, _updated_at, _invite_confirmed = self._upsert_candidate_source(context)
        matches = self._find_matches(source, page.actor_id, company_id, job.category if job else None)
        incoming_updated_at = payload.get("conversation_updated_at") or payload["sent_at"]
        sync_reason = str(payload.get("sync_reason") or "CANDIDATE_OPENED")
        if prior_updated_at and _epoch(incoming_updated_at) <= _epoch(prior_updated_at):
            # Nothing moved. Re-opening a conversation that the table already
            # knows must not create another Feishu write.
            result = self._response(source, account, job, current_recruiter, matches)
            self.session.commit()
            result["feishu_sync_status"] = "UNCHANGED"
            result["snapshot_needed"] = sync_reason == "HISTORY_SNAPSHOT"
            result["lookup_notifications_queued"] = 0
            result["sync_trigger"] = sync_reason
            return result
        # 点击即同步: the candidate is new to this account, or this observation
        # really carries a newer conversation time, so the shared table gets its
        # row now (created, or refreshed with the newest message time). A passive
        # "just looked at it" never invents a follow-up relationship, a
        # MESSAGE_SENT event or a screenshot — only the row and its times.
        result = self._commit_candidate_response(source, account, job, current_recruiter, matches)
        # The persistent conflict record follows the row now that sends no
        # longer write it. Only a conversation this account really took part
        # in (has_recruiter_outbound) may register a cross-recruiter conflict;
        # a passive browse or a candidate-only reply never invents one, which
        # keeps the background reconciliation pass silent.
        if payload.get("has_recruiter_outbound") and self._create_conflicts(source, page.actor_id, company_id):
            self.session.commit()
        # Screenshots belong to the scheduled/manual history pass, which covers
        # the current day's conversations. Nothing else captures, so a recruiter
        # is never photographed mid-typing.
        result["snapshot_needed"] = sync_reason == "HISTORY_SNAPSHOT"
        # The read-only context check owns click-time notifications. Keeping
        # this write path notification-free allows lookup and persistence to
        # run independently without duplicate-alert races.
        result["lookup_notifications_queued"] = 0
        result["sync_trigger"] = sync_reason
        return result

    def _existing_message_response(
        self,
        page: PageActorContext,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        existing_event = self.session.scalar(select(RecruitmentEvent).where(RecruitmentEvent.idempotency_key == payload["client_event_id"]))
        if not existing_event:
            return None
        if existing_event.company_id != page.company_id or existing_event.recruiter_id != page.actor_id or existing_event.event_type != "MESSAGE_SENT":
            raise ApplicationError("IDEMPOTENCY_KEY_CONFLICT", "发送事件幂等键已被占用", 409)
        source = self.session.get(CandidateSource, existing_event.candidate_source_id)
        if not source:
            raise ApplicationError("CANDIDATE_NOT_FOUND", "发送事件对应的候选人不存在", 404)
        job = self.session.get(RecruitmentJob, source.job_id) if source.job_id else None
        matches = self._find_matches(source, page.actor_id, page.company_id, job.category if job else None)
        result = self._commit_candidate_response(
            source,
            page.account,
            job,
            page.recruiter,
            matches,
            idempotent=True,
        )
        # A send never asks for a screenshot — not even on an idempotent retry:
        # the history pass captures every read conversation.
        result["snapshot_needed"] = False
        return result

    def _resolve_job(self, company_id: str, payload: dict[str, Any], *, track_unmapped: bool = False) -> tuple[RecruitmentJob | None, str]:
        normalized_job = JobNameNormalizer().normalize(_canonical_job_name(payload["job_display_name"]))
        alias = self.session.scalar(
            select(JobAlias).where(
                JobAlias.company_id == company_id,
                JobAlias.platform == payload["platform"],
                JobAlias.normalized_alias == normalized_job,
            )
        )
        job = self.session.get(RecruitmentJob, alias.job_id) if alias else None
        if job or not track_unmapped:
            return job, normalized_job
        pending = self.session.scalar(
            select(UnmappedJob).where(
                UnmappedJob.company_id == company_id,
                UnmappedJob.platform == payload["platform"],
                UnmappedJob.normalized_job_name == normalized_job,
            )
        )
        if pending:
            pending.occurrence_count += 1
            pending.last_seen_at = now()
        else:
            self.session.add(
                UnmappedJob(
                    company_id=company_id,
                    platform=payload["platform"],
                    raw_job_name=payload["job_display_name"],
                    normalized_job_name=normalized_job,
                )
            )
        return None, normalized_job

    def _upsert_candidate_source(self, context: MessageContext, *, confirmed_send: bool = False) -> tuple[CandidateSource, datetime, datetime, bool]:
        page, payload = context.page, context.payload
        url_hash = hashlib.sha256(payload["page_url"].encode()).hexdigest()
        source_scope = page.actor_id if payload["platform"] == "boss" else page.account.id
        identity_page_hash = "" if payload["platform"] == "boss" else url_hash
        signature = self._identity_signature(payload)
        identity = candidate_source_identity(
            payload["platform"],
            source_scope,
            signature or payload["candidate_display_name"],
            _canonical_job_name(payload["job_display_name"]),
            identity_page_hash,
            payload.get("platform_candidate_id"),
        )
        sent_at = payload["sent_at"]
        supplied_start = payload.get("conversation_started_at")
        supplied_update = payload.get("conversation_updated_at")
        conversation_started_at = min((value for value in (supplied_start, sent_at) if value), key=_epoch)
        conversation_updated_at = max((value for value in (supplied_update, sent_at) if value), key=_epoch)
        normalized_name = CandidateNameNormalizer().normalize(payload["candidate_display_name"])
        conversation_job_key = hashlib.sha256(f"{page.actor_id}|{signature or normalized_name}|{context.normalized_job}".encode()).hexdigest()
        source = self._find_candidate_source(context, identity, normalized_name, signature)
        # Interview confirmation controls only interview persistence and the
        # one send that also asks for a fresh screenshot. The scheduled history
        # pass captures every read row independently of any status.
        invite_confirmed = confirmed_send and (
            payload.get("recruitment_status") == INVITE_STATUS
            or payload.get("status_evidence") in INVITE_EVIDENCE
        )
        observation = {
            "candidate_display_name": payload["candidate_display_name"],
            "candidate_normalized_name": normalized_name,
            "candidate_identity_signature": signature,
            "conversation_job_key": conversation_job_key,
            "raw_job_name": _canonical_job_name(payload["job_display_name"]),
            "page_url_hash": url_hash,
            "platform_candidate_id": payload.get("platform_candidate_id"),
        }
        # A profile card can be temporarily incomplete while BOSS is
        # re-rendering. Preserve an already-known value instead of replacing
        # it with a transient null/empty observation. Explicit data removal
        # is handled by a separate administrative flow, not by extraction.
        candidate_age = payload.get("candidate_age")
        candidate_experience = normalize_experience(payload.get("candidate_experience") or "") or None
        candidate_education = normalize_education(payload.get("candidate_education") or "") or None
        if candidate_age is not None:
            observation["candidate_age"] = candidate_age
        if candidate_experience:
            observation["candidate_experience"] = candidate_experience
        if candidate_education:
            observation["candidate_education"] = candidate_education
        if source:
            for field, value in observation.items():
                setattr(source, field, value)
            source.last_seen_at = now()
            source.job_id = context.job.id if context.job else source.job_id
            source.data_minimized_at = None
            accepted_status = _accepted_status(
                source.recruitment_status,
                payload.get("recruitment_status"),
                payload.get("status_evidence"),
                source.status_rule_version,
                payload.get("status_rule_version"),
                source.status_evidence,
            )
            if payload.get("status_evidence") == "RECRUITER_RECONTACT_INTENT":
                # Only a genuinely newer conversation may reopen an old one.
                # Passive re-reads and delayed retries carry an older or equal
                # time and must not replace the current state.
                accepted_status = None
                if (payload.get("recruitment_status") in {"沟通中", "待约面"}
                        and (source.conversation_updated_at is None
                             or _epoch(sent_at) > _epoch(source.conversation_updated_at))):
                    accepted_status = (payload["recruitment_status"], "RECRUITER_RECONTACT_INTENT")
            if accepted_status:
                source.recruitment_status, source.status_evidence = accepted_status
                source.status_rule_version = payload.get("status_rule_version") or source.status_rule_version
            if source.resume_status != "READY":
                source.resume_status = payload.get("resume_status") or source.resume_status
            if source.conversation_started_at is None or _epoch(conversation_started_at) < _epoch(source.conversation_started_at):
                source.conversation_started_at = conversation_started_at
            if source.conversation_updated_at is None or _epoch(conversation_updated_at) > _epoch(source.conversation_updated_at):
                source.conversation_updated_at = conversation_updated_at
            return source, conversation_started_at, conversation_updated_at, invite_confirmed
        source = CandidateSource(
            company_id=page.company_id,
            platform=payload["platform"],
            platform_account_id=page.account.id,
            source_identity_key=identity,
            source_identity_type="PLATFORM_ID" if payload.get("platform_candidate_id") else "PAGE_HASH",
            platform_id_scope=payload.get("platform_id_scope", "UNKNOWN"),
            job_id=context.job.id if context.job else None,
            conversation_started_at=conversation_started_at,
            conversation_updated_at=conversation_updated_at,
            extractor_version=payload["extractor_version"],
            recruitment_status=payload.get("recruitment_status") or "沟通中",
            status_evidence=payload.get("status_evidence"),
            status_rule_version=payload.get("status_rule_version") or "boss-status-v1",
            resume_status=payload.get("resume_status") or "NONE",
            **observation,
        )
        self.session.add(source)
        self.session.flush()
        # A brand-new source is persisted here; the caller decides whether the
        # current workflow also captures its conversation.
        return source, conversation_started_at, conversation_updated_at, invite_confirmed

    def _find_candidate_source(
        self,
        context: MessageContext,
        identity: str,
        normalized_name: str,
        signature: str | None,
    ) -> CandidateSource | None:
        page, payload = context.page, context.payload
        source = self.session.scalar(
            select(CandidateSource).where(CandidateSource.company_id == page.company_id, CandidateSource.source_identity_key == identity)
        )
        if source:
            return source
        legacy_conditions = []
        if payload.get("platform_candidate_id"):
            legacy_conditions.append(CandidateSource.platform_candidate_id == payload["platform_candidate_id"])
        if signature:
            legacy_conditions.append(and_(CandidateSource.candidate_normalized_name == normalized_name, CandidateSource.candidate_identity_signature.is_(None)))
        canonical_job = JobNameNormalizer().normalize(_canonical_job_name(payload["job_display_name"]))
        # Legacy rows written before the signature columns existed are matched
        # by platform id, or by name while the stored signature is still NULL.
        if legacy_conditions:
            rows = self.session.scalars(
                select(CandidateSource).where(
                    CandidateSource.company_id == page.company_id,
                    CandidateSource.platform == payload["platform"],
                    CandidateSource.platform_account_id == page.account.id,
                    or_(*legacy_conditions),
                )
            ).all()
            source = next((row for row in rows if JobNameNormalizer().normalize(_canonical_job_name(row.raw_job_name)) == canonical_job), None)
            if source:
                source.source_identity_key = identity
                return source
        # BOSS renders the profile card inconsistently between scans, so the
        # derived identity signature changes for the same person (e.g. the card
        # is still hydrating and age/education are missing). Reuse the existing
        # name+job row for this recruiter instead of creating a second row —
        # clicking an unread candidate and then messaging them must stay one
        # row, not two.
        #
        # Reuse is limited to neighbours that cannot be a different person:
        # an exact signature match, or one side with no signature at all
        # (incomplete card). Two different complete signatures stay separate so
        # the same-name cohort test keeps holding.
        candidates = self.session.scalars(
            select(CandidateSource).where(
                CandidateSource.company_id == page.company_id,
                CandidateSource.platform == payload["platform"],
                CandidateSource.platform_account_id == page.account.id,
                CandidateSource.candidate_normalized_name == normalized_name,
            )
        ).all()
        same_job = [
            row
            for row in candidates
            if JobNameNormalizer().normalize(_canonical_job_name(row.raw_job_name)) == canonical_job
        ]
        if not same_job:
            # The job label can still carry a tail no rule has seen yet. When
            # the stored title is the one just read plus chat/metadata glue, it
            # is the same job rendered with extra text: reuse that row instead
            # of giving one candidate a second row per distinct chat line.
            same_job = [
                row
                for row in candidates
                if _job_label_extends(
                    JobNameNormalizer().normalize(_canonical_job_name(row.raw_job_name)),
                    canonical_job,
                )
            ]
        source = next(
            (
                row
                for row in same_job
                # This scan could not read a full identity (card still
                # hydrating): the name+job row is the best available match.
                if not signature
                # This scan did read one: only an identical signature is safe.
                or row.candidate_identity_signature == signature
            ),
            None,
        )
        if source is None and signature:
            # Both sides are complete but differ. Merge only when the known
            # demographics do not contradict each other, including the common
            # case where one card lost a field while another gained it.
            incoming = {
                "age": payload.get("candidate_age"),
                "experience": payload.get("candidate_experience"),
                "education": payload.get("candidate_education"),
            }
            source = next(
                (
                    row
                    for row in same_job
                    if not _profile_conflicts(
                        incoming,
                        {
                            "age": row.candidate_age,
                            "experience": row.candidate_experience,
                            "education": row.candidate_education,
                        },
                    )
                ),
                None,
            )
        if source:
            source.source_identity_key = identity
        return source

    def _upsert_engagement(
        self,
        page: PageActorContext,
        source: CandidateSource,
        started_at: datetime,
        updated_at: datetime,
    ) -> None:
        engagement = self.session.scalar(select(Engagement).where(Engagement.candidate_source_id == source.id, Engagement.recruiter_id == page.actor_id))
        if engagement:
            engagement.job_id = source.job_id
            if engagement.first_contact_at is None or _epoch(started_at) < _epoch(engagement.first_contact_at):
                engagement.first_contact_at = started_at
            if _epoch(updated_at) > _epoch(engagement.last_activity_at):
                engagement.last_activity_at = updated_at
            return
        self.session.add(
            Engagement(
                company_id=page.company_id,
                candidate_source_id=source.id,
                job_id=source.job_id,
                recruiter_id=page.actor_id,
                recruitment_account_id=source.platform_account_id,
                owner_recruiter_id=page.actor_id,
                stage="FOLLOWING",
                first_contact_at=started_at,
                last_activity_at=updated_at,
            )
        )

    def _commit_candidate_response(
        self,
        source: CandidateSource,
        account: RecruitmentAccount,
        job: RecruitmentJob | None,
        recruiter: Recruiter,
        matches: list[dict[str, Any]],
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        result = self._response(source, account, job, recruiter, matches, idempotent=idempotent)
        self._queue_candidate_sync(source, result["feishu_candidate_fields"])
        self.session.commit()
        result["feishu_sync_status"] = "QUEUED"
        return result

    def backfill_job_mapping(self, company_id: str, platform: str, normalized_job_name: str, job: RecruitmentJob) -> int:
        candidates = self.session.scalars(select(CandidateSource).where(CandidateSource.company_id == company_id, CandidateSource.platform == platform)).all()
        matched = [source for source in candidates if JobNameNormalizer().normalize(source.raw_job_name) == normalized_job_name]
        source_ids = [source.id for source in matched]
        if source_ids:
            self.session.execute(update(Engagement).where(Engagement.candidate_source_id.in_(source_ids)).values(job_id=job.id))
            self.session.execute(update(RecruitmentEvent).where(RecruitmentEvent.candidate_source_id.in_(source_ids)).values(job_id=job.id))
        for source in matched:
            source.job_id = job.id
            self._queue_source_sync(source, job=job)
        return len(matched)

    def requeue_account_sources(self, account_id: str) -> int:
        sources = self.session.scalars(select(CandidateSource).where(CandidateSource.platform_account_id == account_id)).all()
        for source in sources:
            self._queue_source_sync(source)
        return len(sources)

    def _queue_candidate_sync(self, source: CandidateSource, fields: dict[str, Any]) -> None:
        """Coalesce candidate changes into one durable, retryable sync row."""
        queued = self.session.scalar(select(CandidateSyncOutbox).where(CandidateSyncOutbox.candidate_source_id == source.id))
        if queued:
            # A later outbound message supersedes an older pending payload. The
            # version lets the worker detect a concurrent update while sending.
            queued.payload_json = fields
            queued.payload_version += 1
            queued.status = "PENDING"
            queued.retry_count = 0
            queued.next_retry_at = now()
            queued.last_error = None
            return
        self.session.add(
            CandidateSyncOutbox(
                company_id=source.company_id,
                candidate_source_id=source.id,
                payload_json=fields,
                status="PENDING",
                next_retry_at=now(),
            )
        )

    def attach_snapshot(self, company_id: str, source_id: str, tokens: list[str], content_hash: str) -> dict[str, Any]:
        source = self.session.get(CandidateSource, source_id)
        if not source or source.company_id != company_id:
            raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人记录不存在", 404)
        source.snapshot_tokens_json = tokens
        source.snapshot_hash = content_hash
        source.snapshot_status = "READY"
        self._queue_source_sync(source)
        self.session.commit()
        return {"candidate_source_id": source.id, "snapshot_status": source.snapshot_status, "part_count": len(tokens)}

    def attach_resume(self, company_id: str, source_id: str, token: str, content_hash: str, file_name: str) -> dict[str, Any]:
        source = self.session.get(CandidateSource, source_id)
        if not source or source.company_id != company_id:
            raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人记录不存在", 404)
        source.resume_tokens_json = [token]
        source.resume_hash = content_hash
        source.resume_status = "READY"
        source.resume_file_name = file_name
        self._queue_source_sync(source)
        self.session.commit()
        return {"candidate_source_id": source.id, "resume_status": source.resume_status, "file_name": file_name}

    def _queue_source_sync(self, source: CandidateSource, *, job: RecruitmentJob | None = None) -> None:
        account = self.session.get(RecruitmentAccount, source.platform_account_id) if source.platform_account_id else None
        recruiter = self.session.get(Recruiter, account.recruiter_id) if account else None
        mapped_job = job or (self.session.get(RecruitmentJob, source.job_id) if source.job_id else None)
        fields = self._response(source, account, mapped_job, recruiter, [])["feishu_candidate_fields"]
        self._queue_candidate_sync(source, fields)

    @staticmethod
    def _mark_own_match(item: dict[str, Any], current_recruiter: Any, account: Any) -> dict[str, Any]:
        """Tag matches that belong to the viewer themself.

        A cross-job own row is kept in the matches (it is a useful "you
        already followed this person elsewhere" reminder) but must never be
        worded as a colleague conflict, so the UI layers can tell them apart.
        """
        own = current_recruiter is not None and item.get("recruiter_id") == current_recruiter.id
        if not own:
            names = {
                CandidateNameNormalizer().normalize(value)
                for value in (
                    current_recruiter.display_name if current_recruiter else "",
                    current_recruiter.feishu_display_name if current_recruiter and current_recruiter.feishu_display_name else "",
                    account.account_display_name if account else "",
                )
                if value
            }
            own = CandidateNameNormalizer().normalize(str(item.get("recruiter_name") or "")) in names
        return {**item, "is_own_history": True} if own else item

    def _response(
        self,
        source: CandidateSource | None,
        account: RecruitmentAccount | None,
        job: RecruitmentJob | None,
        current_recruiter: Any,
        matches: list[dict[str, Any]],
        idempotent: bool = False,
    ) -> dict[str, Any]:
        annotated = [self._mark_own_match(item, current_recruiter, account) for item in matches]
        colleagues = [item for item in annotated if not item.get("is_own_history")]
        if not annotated:
            result_type, severity, title, message = "NO_HISTORY", "success", "暂无其他同事记录", "候选人已同步"
        elif not colleagues:
            first = annotated[0]
            result_type = {
                "CONFIRMED_BOSS_HISTORY": "CONFIRMED_DUPLICATE",
                "EXACT_IDENTITY": "CONFIRMED_DUPLICATE",
                "CONFIRMED_PLATFORM_ID": "CONFIRMED_DUPLICATE",
                "SUSPECTED_SAME_NAME_JOB": "SUSPECTED_DUPLICATE",
                "POSSIBLE_SAME_NAME_CATEGORY": "POSSIBLE_DUPLICATE",
                "HISTORICAL_SAME_NAME": "HISTORICAL_RECORD",
            }[first["match_level"]]
            severity = "warning"
            title = "你本人跟进过的候选人"
            message = (
                f"你本人曾在其他岗位「{first.get('job_name') or '未知岗位'}」跟进过该候选人，"
                "与本次岗位不同，注意区分，不会影响当前跟进"
            )
        else:
            first = colleagues[0]
            result_type = {
                "CONFIRMED_BOSS_HISTORY": "CONFIRMED_DUPLICATE",
                "EXACT_IDENTITY": "CONFIRMED_DUPLICATE",
                "CONFIRMED_PLATFORM_ID": "CONFIRMED_DUPLICATE",
                "SUSPECTED_SAME_NAME_JOB": "SUSPECTED_DUPLICATE",
                "POSSIBLE_SAME_NAME_CATEGORY": "POSSIBLE_DUPLICATE",
                "HISTORICAL_SAME_NAME": "HISTORICAL_RECORD",
            }[first["match_level"]]
            severity = "danger" if result_type == "CONFIRMED_DUPLICATE" else "warning"
            title, message = "发现候选人历史记录", f"{first['recruiter_name']} 已在跟进该候选人"
        result: dict[str, Any] = {
            "candidate_source_id": source.id if source else None,
            "account_mapping": {"status": "MAPPED", "account_id": account.id} if account else {"status": "NOT_REQUIRED"},
            # A raw BOSS job is already a valid business value.  The optional
            # standard-job relation is only for normalized reporting and must
            # never look like a failed candidate synchronization.
            "job_mapping": (
                {"status": "MAPPED", "job_id": job.id, "canonical_name": job.canonical_name}
                if job
                else {
                    "status": "RAW",
                    "raw_name": source.raw_job_name if source else (matches[0].get("job_name") if matches else None),
                    "normalization": "OPTIONAL",
                }
            ),
            "result_type": result_type,
            "ui": {"severity": severity, "title": title, "message": message},
            "matches": annotated,
            "history_summary": {
                "other_recruiter_count": len({item["recruiter_id"] for item in colleagues}),
                "contacted_by_others": any(item["history"]["contacted"] for item in colleagues),
                "interviewed_by_others": any(item["history"]["interviewed"] for item in colleagues),
                "rejected_by_others": any(item["history"]["rejected"] for item in colleagues),
                "multiple_recruiters": bool(colleagues),
            },
            "idempotent": idempotent,
            "available_actions": ["VIEW_TIMELINE", "NOT_SAME_PERSON"],
        }
        if source:
            result["resume_status"] = source.resume_status
            result["feishu_candidate_fields"] = {
                "候选人标识": source.source_identity_key,
                "候选人": source.candidate_display_name,
                "年龄": source.candidate_age,
                "工作年限": source.candidate_experience or "",
                "学历": source.candidate_education or "",
                "BOSS岗位": source.raw_job_name,
                "标准岗位": job.canonical_name if job else "",
                "BOSS账号": account.account_display_name if account else "",
                "飞书账号": self._recruiter_label(current_recruiter),
                "状态": source.recruitment_status,
                "状态依据": source.status_evidence or "",
                "系统记录标识": source.id,
                "候选人身份签名": source.candidate_identity_signature or "",
                "会话岗位标识": source.conversation_job_key or source.source_identity_key,
                "聊天框截图": [{"file_token": token} for token in (source.snapshot_tokens_json or [])],
                "快照状态": source.snapshot_status,
                "简历附件": [{"file_token": token} for token in (source.resume_tokens_json or [])],
                "简历状态": source.resume_status,
                "提醒": (
                    "；".join(
                        f"{item['recruiter_name']}：{item['stage']}"
                        f"（{item.get('match_reason') or '历史记录'}）"
                        for item in matches
                    )
                    if matches
                    else "暂无其他同事跟进记录"
                ),
                **({"开始聊天时间": int(_epoch(source.conversation_started_at) * 1000)} if source.conversation_started_at else {}),
                **({"更新时间": int(_epoch(source.conversation_updated_at) * 1000)} if source.conversation_updated_at else {}),
            }
        return result

    def _find_matches(self, source: CandidateSource, actor_id: str, company_id: str, category: str | None) -> list[dict[str, Any]]:
        exclusions = self.session.scalars(select(ConflictExclusion).where(ConflictExclusion.company_id == company_id)).all()
        excluded_ids = {
            item.left_candidate_source_id if item.right_candidate_source_id == source.id else item.right_candidate_source_id
            for item in exclusions
            if source.id in {item.left_candidate_source_id, item.right_candidate_source_id}
        }
        rows = self.session.execute(
            select(CandidateSource, Engagement, RecruitmentJob, Recruiter)
            .join(RecruitmentAccount, RecruitmentAccount.id == CandidateSource.platform_account_id)
            .join(Recruiter, Recruiter.id == RecruitmentAccount.recruiter_id)
            .outerjoin(
                Engagement,
                and_(
                    Engagement.candidate_source_id == CandidateSource.id,
                    Engagement.recruiter_id == Recruiter.id,
                ),
            )
            .outerjoin(RecruitmentJob, RecruitmentJob.id == CandidateSource.job_id)
            .where(
                CandidateSource.company_id == company_id,
                CandidateSource.id != source.id,
                CandidateSource.id.not_in(excluded_ids or {""}),
                # First identify the same person by the four profile fields;
                # only cross-account or cross-job observations are actionable.
                or_(
                    CandidateSource.platform_account_id != source.platform_account_id,
                    CandidateSource.job_id != source.job_id,
                ),
                or_(
                    CandidateSource.candidate_normalized_name == source.candidate_normalized_name,
                    and_(CandidateSource.platform_candidate_id.is_not(None), CandidateSource.platform_candidate_id == source.platform_candidate_id),
                ),
            )
        ).all()
        event_types_by_owner: dict[tuple[str, str], list[str]] = defaultdict(list)
        source_ids = {other.id for other, _engagement, _job, _recruiter in rows}
        if source_ids:
            events = self.session.execute(
                select(RecruitmentEvent.candidate_source_id, RecruitmentEvent.recruiter_id, RecruitmentEvent.event_type)
                .where(RecruitmentEvent.candidate_source_id.in_(source_ids))
                .order_by(RecruitmentEvent.event_time.asc())
            ).all()
            for candidate_source_id, recruiter_id, event_type in events:
                event_types_by_owner[(candidate_source_id, recruiter_id)].append(event_type)
        result = []
        for other, engagement, other_job, recruiter in rows:
            if (
                source.platform_candidate_id
                and source.platform_candidate_id == other.platform_candidate_id
                and source.platform_id_scope in {"COMPANY", "GLOBAL"}
                and other.platform_id_scope in {"COMPANY", "GLOBAL"}
            ):
                level, reason = MatchLevel.CONFIRMED_PLATFORM_ID.value, "已验证的平台候选人 ID 一致"
            elif source.candidate_identity_signature and source.candidate_identity_signature == other.candidate_identity_signature:
                level = "EXACT_IDENTITY"
                account_diff = source.platform_account_id != other.platform_account_id
                job_diff = source.job_id != other.job_id or (
                    source.job_id is None and other.job_id is None and
                    JobNameNormalizer().normalize(_canonical_job_name(source.raw_job_name)) !=
                    JobNameNormalizer().normalize(_canonical_job_name(other.raw_job_name))
                )
                reason = "姓名、年龄、工作年限、学历一致；BOSS账号不同" if account_diff and not job_diff else (
                    "姓名、年龄、工作年限、学历一致；沟通岗位不同" if job_diff and not account_diff else
                    "姓名、年龄、工作年限、学历一致；BOSS账号和沟通岗位均不同"
                )
            else:
                continue
            event_types = event_types_by_owner[(other.id, recruiter.id)]
            stage = engagement.stage if engagement else other.recruitment_status
            interviewed = any(value in {"INTERVIEW_INVITED", "INTERVIEW_COMPLETED"} for value in event_types) or stage in {
                "INTERVIEW_INVITED",
                "INTERVIEW_COMPLETED",
                "已约面",
            }
            rejected = "REJECTED" in event_types or stage in {"REJECTED", "已拒绝"}
            result.append(
                {
                    "match_level": level,
                    "candidate_source_id": other.id,
                    "recruiter_id": recruiter.id,
                    "recruiter_name": recruiter.display_name,
                    "job_id": other.job_id,
                    "job_name": other.raw_job_name or (other_job.canonical_name if other_job else ""),
                    "stage": stage,
                    "match_reason": reason,
                    "first_contact_at": (
                        engagement.first_contact_at.isoformat()
                        if engagement and engagement.first_contact_at
                        else other.conversation_started_at.isoformat()
                        if other.conversation_started_at
                        else None
                    ),
                    "last_activity_at": (
                        engagement.last_activity_at.isoformat()
                        if engagement and engagement.last_activity_at
                        else other.conversation_updated_at.isoformat()
                        if other.conversation_updated_at
                        else None
                    ),
                    "history": {
                        "contacted": bool(event_types) or bool(engagement and engagement.first_contact_at),
                        "interviewed": interviewed,
                        "rejected": rejected,
                        "event_types": event_types,
                    },
                }
            )
        priority = {"CONFIRMED_PLATFORM_ID": 0, "EXACT_IDENTITY": 1, "SUSPECTED_SAME_NAME_JOB": 2, "POSSIBLE_SAME_NAME_CATEGORY": 3, "HISTORICAL_SAME_NAME": 4}
        return sorted(result, key=lambda item: priority[item["match_level"]])

    def record_event(self, actor_id: str, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = self.session.get(CandidateSource, payload["candidate_source_id"])
        if not source or source.company_id != company_id:
            raise ApplicationError("CANDIDATE_NOT_FOUND", "候选人来源不存在", 404)
        if payload["event_type"] == "CONTINUED_AFTER_WARNING" and not (payload.get("reason") or "").strip():
            raise ApplicationError("REASON_REQUIRED", "继续沟通必须填写原因")
        existing_event = self.session.scalar(select(RecruitmentEvent).where(RecruitmentEvent.idempotency_key == payload["idempotency_key"]))
        if existing_event:
            return {"event_id": existing_event.id, "idempotent": True}
        engagement = self.session.scalar(
            select(Engagement).where(Engagement.candidate_source_id == source.id, Engagement.recruiter_id == actor_id, Engagement.job_id == source.job_id)
        )
        stage = (
            payload["event_type"]
            if payload["event_type"]
            in {
                "CLAIMED",
                "CONTACTED",
                "RESUME_REQUESTED",
                "RESUME_RECEIVED",
                "INTERVIEW_INVITED",
                "INTERVIEW_COMPLETED",
                "ON_HOLD",
                "REJECTED",
                "HIRED",
                "CLOSED",
            }
            else (engagement.stage if engagement else "CONTACTED")
        )
        if not engagement:
            engagement = Engagement(
                company_id=company_id,
                candidate_source_id=source.id,
                job_id=source.job_id,
                recruiter_id=actor_id,
                recruitment_account_id=source.platform_account_id,
                owner_recruiter_id=actor_id,
                stage=stage,
                first_contact_at=now() if stage != "CLAIMED" else None,
            )
            self.session.add(engagement)
        else:
            engagement.stage, engagement.last_activity_at, engagement.version = stage, now(), engagement.version + 1
        event = RecruitmentEvent(
            company_id=company_id,
            candidate_source_id=source.id,
            job_id=source.job_id,
            recruiter_id=actor_id,
            event_type=payload["event_type"],
            idempotency_key=payload["idempotency_key"],
            metadata_json={"reason": payload.get("reason")},
        )
        self.session.add(event)
        self.session.flush()
        conflicts = self._create_conflicts(source, actor_id, company_id)
        self.session.add(
            AuditLog(
                company_id=company_id,
                actor_id=actor_id,
                action=payload["event_type"],
                entity_type="candidate_source",
                entity_id=source.id,
                after_json={"stage": stage},
            )
        )
        self.session.commit()
        return {"event_id": event.id, "engagement_id": engagement.id, "conflict_ids": conflicts, "idempotent": False}

    def _create_conflicts(self, source: CandidateSource, actor_id: str, company_id: str) -> list[str]:
        job = self.session.get(RecruitmentJob, source.job_id) if source.job_id else None
        matches = self._find_matches(source, actor_id, company_id, job.category if job else "")
        settings = self.session.scalar(select(RecruitmentSetting).where(RecruitmentSetting.company_id == company_id))
        if settings and not settings.notify_on_contact:
            return []
        conflict_ids: list[str] = []
        for match in matches:
            if match["match_level"] not in {"CONFIRMED_PLATFORM_ID", "EXACT_IDENTITY", "SUSPECTED_SAME_NAME_JOB"}:
                continue
            identity_key = (
                source.platform_candidate_id
                if match["match_level"] == "CONFIRMED_PLATFORM_ID"
                else source.candidate_identity_signature
                if match["match_level"] == "EXACT_IDENTITY"
                else source.candidate_normalized_name
            )
            key = conflict_key(company_id, source.job_id, identity_key or source.source_identity_key, actor_id, match["recruiter_id"])
            conflict = self.session.scalar(select(Conflict).where(Conflict.conflict_key == key))
            if conflict and conflict.status not in {"CLOSED", "NOT_SAME_PERSON"}:
                conflict.last_detected_at = now()
                self._queue_conflict_notifications(conflict, source.candidate_display_name, match["match_reason"], (actor_id, match["recruiter_id"]))
                conflict_ids.append(conflict.id)
                continue
            if conflict and conflict.status == "NOT_SAME_PERSON":
                continue
            conflict = Conflict(
                company_id=company_id,
                left_candidate_source_id=match["candidate_source_id"],
                right_candidate_source_id=source.id,
                job_id=source.job_id,
                left_recruiter_id=match["recruiter_id"],
                right_recruiter_id=actor_id,
                match_level=match["match_level"],
                match_reason=match["match_reason"],
                conflict_key=key,
            )
            self.session.add(conflict)
            self.session.flush()
            self._queue_conflict_notifications(conflict, source.candidate_display_name, match["match_reason"], (actor_id, match["recruiter_id"]))
            conflict_ids.append(conflict.id)
        return conflict_ids

    def _queue_conflict_notifications(self, conflict: Conflict, candidate_name: str, match_reason: str, recruiter_ids: tuple[str, str]) -> None:
        """Warn both recruiters, each told about the *other* side.

        A conflict card names the counterpart recruiter, their first contact and
        their last activity. One shared payload carries none of those per-side
        facts, so every card rendered them as 未知 — the card is built per
        recipient here instead. ``_queue_notifications`` derives the
        idempotency key from the recipient, so two payloads for one conflict
        stay two independently deduplicated rows.
        """
        sides = {
            conflict.left_recruiter_id: conflict.left_candidate_source_id,
            conflict.right_recruiter_id: conflict.right_candidate_source_id,
        }
        for recipient_id, counterpart_source_id in sides.items():
            counterpart_id = next(
                (recruiter_id for recruiter_id in sides if recruiter_id != recipient_id),
                None,
            )
            counterpart = self.session.get(CandidateSource, counterpart_source_id)
            counterpart_recruiter = self.session.get(Recruiter, counterpart_id) if counterpart_id else None
            payload = {
                "type": "DUPLICATE_CANDIDATE",
                "conflict_id": conflict.id,
                "candidate_name": candidate_name,
                "job_id": conflict.job_id,
                # The counterpart's job and contact times, so the card reads
                # like the duplicate-lookup one instead of a row of 未知.
                "job_name": counterpart.raw_job_name if counterpart else "",
                "matched_recruiter_name": self._recruiter_label(counterpart_recruiter, "其他招聘者"),
                "first_contact_at": _iso_time(counterpart.conversation_started_at) if counterpart else None,
                "last_activity_at": _iso_time(counterpart.conversation_updated_at) if counterpart else None,
                "match_reason": match_reason,
            }
            self._queue_notifications(
                conflict.company_id,
                "CONFLICT_CREATED",
                "conflict",
                conflict.id,
                payload,
                (recipient_id,),
                f"conflict:{conflict.id}:v1",
            )

    def exclude_conflict(self, conflict_id: str, actor_id: str, company_id: str, reason: str) -> None:
        conflict = self.session.get(Conflict, conflict_id)
        if not conflict or conflict.company_id != company_id:
            raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
        left, right = sorted((conflict.left_candidate_source_id, conflict.right_candidate_source_id))
        if not self.session.scalar(
            select(ConflictExclusion).where(
                ConflictExclusion.company_id == company_id,
                ConflictExclusion.left_candidate_source_id == left,
                ConflictExclusion.right_candidate_source_id == right,
            )
        ):
            self.session.add(
                ConflictExclusion(company_id=company_id, left_candidate_source_id=left, right_candidate_source_id=right, reason=reason, created_by=actor_id)
            )
        conflict.status, conflict.resolution, conflict.resolved_at, conflict.resolved_by = "NOT_SAME_PERSON", reason, now(), actor_id
        self.session.add(
            AuditLog(
                company_id=company_id, actor_id=actor_id, action="NOT_SAME_PERSON", entity_type="conflict", entity_id=conflict.id, after_json={"reason": reason}
            )
        )
        self.session.commit()
