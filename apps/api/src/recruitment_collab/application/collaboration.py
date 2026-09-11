from __future__ import annotations

import hashlib
import logging
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


def _accepted_status(
    current: str,
    incoming: str | None,
    evidence: str | None,
    current_rule_version: str = "boss-status-v1",
    incoming_rule_version: str | None = None,
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
        return (incoming, evidence) if incoming in TERMINAL_STATUSES and incoming != current else None
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
        matches = self._merge_native_matches(payload.get("native_communications") or [], payload["account_display_name"], matches, feishu_available)
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
        native_rows: list[dict[str, Any]], current_recruiter: str, feishu_matches: list[dict[str, Any]], feishu_available: bool
    ) -> list[dict[str, Any]]:
        normalize_job = JobNameNormalizer().normalize
        normalize_name = CandidateNameNormalizer().normalize
        current = normalize_name(current_recruiter)
        by_key = {(normalize_name(item["recruiter_name"]), normalize_job(item.get("job_name") or "")): item for item in feishu_matches}
        merged: list[dict[str, Any]] = []
        consumed: set[int] = set()
        for row in native_rows:
            recruiter = str(row.get("recruiter_name") or "").strip()
            job_name = str(row.get("job_name") or "").strip()
            if not recruiter or not job_name or normalize_name(recruiter) == current:
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
        merged.extend(item for item in feishu_matches if id(item) not in consumed)
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
        self, company_id: str, signature: str | None, candidate_name: str, job_name: str, canonical_job_name: str, current_recruiter: str
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
        rows = client.find_system_candidates(signature, current_recruiter, candidate_name, job_name, canonical_job_name)
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
        normalized_job = JobNameNormalizer().normalize(payload["job_display_name"])
        queued_count = 0
        current_time = now()
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
            target_key = matched.id if matched else CandidateNameNormalizer().normalize(matched_name)
            alert_key = hashlib.sha256(f"{company_id}|{viewer.id}|{target_key}|{identity}|{normalized_job}".encode()).hexdigest()
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
            )
            if not alert:
                continue
            alert.notification_version += 1
            viewer_label = self._recruiter_label(viewer, viewer.display_name)
            payload_json = {
                "type": "DUPLICATE_LOOKUP",
                "lookup_alert_id": alert.id,
                "candidate_name": payload["candidate_display_name"],
                "job_name": payload["job_display_name"],
                "viewer_name": viewer_label,
                "matched_recruiter_name": matched_name,
                "first_contact_at": _iso_time(match.get("first_contact_at")),
                "last_activity_at": _iso_time(match.get("last_activity_at") or match.get("updated_at")),
                "current_action": "你正在查看该候选人，尚未确认发送消息",
                "match_level": alert.match_level,
                "match_reason": alert.match_reason,
            }
            # Browsing a candidate is not a follow-up action, so only the person
            # who is looking at it is warned. The recruiter who already
            # contacted the candidate is deliberately NOT pinged here: the
            # previous behaviour alerted them every time a colleague merely
            # opened the profile, which is pure noise. They are notified only
            # once a real message is sent and a conflict is recorded.
            recipients = {viewer.id}
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
                alert.last_notified_at = current_time
                queued_count += created
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

    def _activate_lookup_alert(self, draft: DuplicateLookupAlert, detected_at: datetime) -> DuplicateLookupAlert | None:
        alert = self.session.scalar(select(DuplicateLookupAlert).where(DuplicateLookupAlert.alert_key == draft.alert_key))
        if alert:
            alert.hit_count += 1
            alert.last_detected_at = detected_at
            last_notified = alert.last_notified_at
            if last_notified and not last_notified.tzinfo:
                last_notified = last_notified.replace(tzinfo=timezone.utc)
            cooldown_elapsed = not last_notified or detected_at - last_notified >= timedelta(hours=24)
            if draft.evidence_rank <= alert.evidence_rank and not cooldown_elapsed:
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
        context = MessageContext(page, job, normalized_job, payload)
        source, started_at, updated_at, invite_confirmed = self._upsert_candidate_source(context, confirmed_send=True)
        self._upsert_engagement(page, source, started_at, updated_at)
        if invite_confirmed:
            self._store_invited_interview(source, page, job, payload)
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
        matches = self._find_matches(source, page.actor_id, company_id, job.category if job else None)
        # Re-run the notification path after a confirmed outbound message.
        # The pre-send context check is best-effort; the send event is the
        # authoritative point at which the candidate is synced and any
        # cross-recruiter match must be surfaced to the user.
        lookup_notifications_queued = self._queue_lookup_alerts(company_id, current_recruiter, payload, matches)
        self._create_conflicts(source, page.actor_id, company_id)
        result = self._commit_candidate_response(source, account, job, current_recruiter, matches)
        result["snapshot_needed"] = invite_confirmed
        result["lookup_notifications_queued"] = lookup_notifications_queued
        return result

    def sync_candidate_observation(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a candidate opened in BOSS without claiming a message was sent.

        Candidate selection is the synchronization trigger for every recruiter.
        A real outbound message still goes through ``record_message_sent`` so
        engagements and MESSAGE_SENT events retain their business meaning.
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
            if JobNameNormalizer().normalize(row.raw_job_name) == normalized_job
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
        if prior_updated_at and _epoch(incoming_updated_at) <= _epoch(prior_updated_at):
            result = self._response(source, account, job, current_recruiter, matches)
            self.session.commit()
            result["feishu_sync_status"] = "UNCHANGED"
            result["snapshot_needed"] = False
            result["lookup_notifications_queued"] = 0
            result["sync_trigger"] = "CANDIDATE_OPENED"
            return result
        result = self._commit_candidate_response(source, account, job, current_recruiter, matches)
        # Opening a conversation must never request the historical chat
        # capture: it scrolls the recruiter's own pane. Screenshots are asked
        # for only by a confirmed interview invitation (record_message_sent).
        result["snapshot_needed"] = False
        # The read-only context check owns click-time notifications. Keeping
        # this write path notification-free allows lookup and persistence to
        # run independently without duplicate-alert races.
        result["lookup_notifications_queued"] = 0
        result["sync_trigger"] = "CANDIDATE_OPENED"
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
        # A catch-up scan reuses its deterministic idempotency key. If the
        # original attempt carried an interview invitation but the screenshot
        # upload failed, the idempotent response must still ask the extension
        # to capture again; otherwise a transient capture error becomes
        # permanent and the source remains FAILED forever. A non-invite retry
        # must stay silent even when an old snapshot is missing.
        invite_message = (
            payload.get("recruitment_status") == INVITE_STATUS
            or payload.get("status_evidence") in INVITE_EVIDENCE
        )
        result["snapshot_needed"] = invite_message and (
            source.snapshot_status != "READY" or not source.snapshot_tokens_json
        )
        return result

    def _resolve_job(self, company_id: str, payload: dict[str, Any], *, track_unmapped: bool = False) -> tuple[RecruitmentJob | None, str]:
        normalized_job = JobNameNormalizer().normalize(payload["job_display_name"])
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
            payload["job_display_name"],
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
        # A screenshot is an evidence artifact for the interview invitation,
        # not for browsing. Passive observations therefore never request one;
        # a confirmed send asks for a capture only when it actually hands the
        # candidate an invitation.
        invite_confirmed = confirmed_send and (
            payload.get("recruitment_status") == INVITE_STATUS
            or payload.get("status_evidence") in INVITE_EVIDENCE
        )
        observation = {
            "candidate_display_name": payload["candidate_display_name"],
            "candidate_normalized_name": normalized_name,
            "candidate_identity_signature": signature,
            "conversation_job_key": conversation_job_key,
            "raw_job_name": payload["job_display_name"],
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
            )
            if payload.get("status_evidence") == "RECRUITER_RECONTACT_INTENT":
                # Only a newer confirmed outbound event can reopen an old
                # conversation. Passive observations and delayed retries
                # must not replace its current state.
                accepted_status = None
                if (confirmed_send and payload.get("recruitment_status") in {"沟通中", "待约面"}
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
        # A brand-new source is only persisted here; whether it also needs a
        # screenshot still depends on why it was created (see invite_confirmed).
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
        if not legacy_conditions:
            return None
        rows = self.session.scalars(
            select(CandidateSource).where(
                CandidateSource.company_id == page.company_id,
                CandidateSource.platform == payload["platform"],
                CandidateSource.platform_account_id == page.account.id,
                or_(*legacy_conditions),
            )
        ).all()
        source = next((row for row in rows if JobNameNormalizer().normalize(row.raw_job_name) == context.normalized_job), None)
        if source:
            source.source_identity_key = identity
            return source
        # BOSS can change the exposed identity details (and therefore the
        # derived signature) after a resume/profile refresh. For the same
        # recruiter, reuse the existing name+job row instead of creating a
        # second row. Recruiter ownership remains part of the source scope,
        # so a different recruiter still creates a separate row.
        candidates = self.session.scalars(
            select(CandidateSource).where(
                CandidateSource.company_id == page.company_id,
                CandidateSource.platform == payload["platform"],
                CandidateSource.platform_account_id == page.account.id,
                CandidateSource.candidate_normalized_name == normalized_name,
            )
        ).all()
        source = next(
            (
                row
                for row in candidates
                if JobNameNormalizer().normalize(row.raw_job_name) == context.normalized_job
                and (not signature or not row.candidate_identity_signature)
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

    def _response(
        self,
        source: CandidateSource | None,
        account: RecruitmentAccount | None,
        job: RecruitmentJob | None,
        current_recruiter: Any,
        matches: list[dict[str, Any]],
        idempotent: bool = False,
    ) -> dict[str, Any]:
        if not matches:
            result_type, severity, title, message = "NO_HISTORY", "success", "暂无其他同事记录", "候选人已同步"
        else:
            first = matches[0]
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
            "matches": matches,
            "history_summary": {
                "other_recruiter_count": len({item["recruiter_id"] for item in matches}),
                "contacted_by_others": any(item["history"]["contacted"] for item in matches),
                "interviewed_by_others": any(item["history"]["interviewed"] for item in matches),
                "rejected_by_others": any(item["history"]["rejected"] for item in matches),
                "multiple_recruiters": bool(matches),
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
                Recruiter.id != actor_id,
                CandidateSource.id.not_in(excluded_ids or {""}),
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
                level, reason = "EXACT_IDENTITY", "姓名、年龄、工作年限、学历完全一致"
            elif (source.job_id is not None and source.job_id == other.job_id) or (
                source.job_id is None
                and other.job_id is None
                and JobNameNormalizer().normalize(source.raw_job_name) == JobNameNormalizer().normalize(other.raw_job_name)
            ):
                level, reason = MatchLevel.SUSPECTED_SAME_NAME_JOB.value, "标准化姓名和内部岗位一致"
            elif other_job and other_job.category == category:
                level, reason = MatchLevel.POSSIBLE_SAME_NAME_CATEGORY.value, "标准化姓名和岗位类别一致"
            else:
                level, reason = MatchLevel.HISTORICAL_SAME_NAME.value, "仅标准化姓名一致"
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
        payload = {
            "type": "DUPLICATE_CANDIDATE",
            "conflict_id": conflict.id,
            "candidate_name": candidate_name,
            "job_id": conflict.job_id,
            "match_reason": match_reason,
        }
        self._queue_notifications(
            conflict.company_id,
            "CONFLICT_CREATED",
            "conflict",
            conflict.id,
            payload,
            recruiter_ids,
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
