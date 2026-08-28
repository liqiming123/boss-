from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from recruitment_collab.domain.enums import MatchLevel
from recruitment_collab.domain.normalization import CandidateNameNormalizer, JobNameNormalizer
from recruitment_collab.domain.services import CandidateSourceIdentityFactory, ConflictKeyFactory
from recruitment_collab.infrastructure.models import (
    AuditLog,
    CandidateSource,
    Conflict,
    ConflictExclusion,
    Engagement,
    JobAlias,
    NotificationOutbox,
    RecruitmentAccount,
    RecruitmentEvent,
    RecruitmentJob,
    RecruitmentSetting,
    UnmappedJob,
    now,
)


class ApplicationError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class RecruitmentCollaborationService:
    def __init__(self, session: Session):
        self.session = session

    def resolve_context(self, actor_id: str, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        account = self.session.scalar(
            select(RecruitmentAccount).where(
                RecruitmentAccount.company_id == company_id,
                RecruitmentAccount.recruiter_id == actor_id,
                RecruitmentAccount.platform == payload["platform"],
                RecruitmentAccount.account_display_name == payload["account_display_name"],
                RecruitmentAccount.status == "ACTIVE",
            )
        )
        if not account:
            return self._unmapped_response("ACCOUNT_UNMAPPED", "招聘账号尚未绑定")

        normalized_job = JobNameNormalizer().normalize(payload["job_display_name"])
        alias = self.session.scalar(
            select(JobAlias).where(
                JobAlias.company_id == company_id,
                JobAlias.platform == payload["platform"],
                JobAlias.normalized_alias == normalized_job,
            )
        )
        job = self.session.get(RecruitmentJob, alias.job_id) if alias else None
        if not job:
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
                self.session.add(UnmappedJob(company_id=company_id, platform=payload["platform"], raw_job_name=payload["job_display_name"], normalized_job_name=normalized_job))
            self.session.commit()
            return self._unmapped_response("JOB_UNMAPPED", "岗位尚未映射，请联系管理员")

        url_hash = hashlib.sha256(payload["page_url"].encode()).hexdigest()
        identity = CandidateSourceIdentityFactory().create(
            payload["platform"], account.id, payload["candidate_display_name"], payload["job_display_name"], url_hash, payload.get("platform_candidate_id")
        )
        source = self.session.scalar(select(CandidateSource).where(CandidateSource.company_id == company_id, CandidateSource.source_identity_key == identity))
        if source:
            source.last_seen_at = now()
            source.candidate_display_name = payload["candidate_display_name"]
        else:
            source = CandidateSource(
                company_id=company_id,
                platform=payload["platform"],
                platform_account_id=account.id,
                source_identity_key=identity,
                source_identity_type="PLATFORM_ID" if payload.get("platform_candidate_id") else "PAGE_HASH",
                platform_candidate_id=payload.get("platform_candidate_id"),
                platform_id_scope=payload.get("platform_id_scope", "UNKNOWN"),
                page_url_hash=url_hash,
                candidate_display_name=payload["candidate_display_name"],
                candidate_normalized_name=CandidateNameNormalizer().normalize(payload["candidate_display_name"]),
                raw_job_name=payload["job_display_name"],
                job_id=job.id,
                extractor_version=payload["extractor_version"],
            )
            self.session.add(source)
            self.session.flush()

        matches = self._find_matches(source, actor_id, company_id, job.category)
        self.session.commit()
        if not matches:
            result_type, severity, title, message = "NO_HISTORY", "success", "暂无其他同事跟进记录", "可设为我跟进或记录沟通"
        else:
            first = matches[0]
            result_type = {"CONFIRMED_PLATFORM_ID": "CONFIRMED_DUPLICATE", "SUSPECTED_SAME_NAME_JOB": "SUSPECTED_DUPLICATE", "POSSIBLE_SAME_NAME_CATEGORY": "POSSIBLE_DUPLICATE", "HISTORICAL_SAME_NAME": "HISTORICAL_RECORD"}[first["match_level"]]
            severity = "danger" if result_type == "CONFIRMED_DUPLICATE" else "warning"
            title, message = "发现候选人历史记录", f'{first["recruiter_name"]} 已在跟进该候选人'
        return {
            "candidate_source_id": source.id,
            "account_mapping": {"status": "MAPPED", "account_id": account.id},
            "job_mapping": {"status": "MAPPED", "job_id": job.id, "canonical_name": job.canonical_name},
            "result_type": result_type,
            "ui": {"severity": severity, "title": title, "message": message},
            "matches": matches,
            "available_actions": ["VIEW_TIMELINE", "CLAIM", "RECORD_CONTACT", "NOT_SAME_PERSON", "REQUEST_TRANSFER", "CONTINUE_WITH_REASON"],
        }

    def _unmapped_response(self, result: str, message: str) -> dict[str, Any]:
        return {"candidate_source_id": None, "account_mapping": {"status": result}, "job_mapping": {"status": result}, "result_type": result, "ui": {"severity": "warning", "title": message, "message": message}, "matches": [], "available_actions": ["RETRY", "VIEW_DIAGNOSTICS"]}

    def _find_matches(self, source: CandidateSource, actor_id: str, company_id: str, category: str) -> list[dict[str, Any]]:
        from recruitment_collab.infrastructure.models import Recruiter

        exclusions = self.session.scalars(select(ConflictExclusion).where(ConflictExclusion.company_id == company_id)).all()
        excluded_ids = {item.left_candidate_source_id if item.right_candidate_source_id == source.id else item.right_candidate_source_id for item in exclusions if source.id in {item.left_candidate_source_id, item.right_candidate_source_id}}
        rows = self.session.execute(
            select(CandidateSource, Engagement, RecruitmentJob, Recruiter)
            .join(Engagement, Engagement.candidate_source_id == CandidateSource.id)
            .outerjoin(RecruitmentJob, RecruitmentJob.id == CandidateSource.job_id)
            .join(Recruiter, Recruiter.id == Engagement.recruiter_id)
            .where(CandidateSource.company_id == company_id, CandidateSource.id != source.id, Engagement.recruiter_id != actor_id, CandidateSource.id.not_in(excluded_ids or {""}), or_(CandidateSource.candidate_normalized_name == source.candidate_normalized_name, and_(CandidateSource.platform_candidate_id.is_not(None), CandidateSource.platform_candidate_id == source.platform_candidate_id)))
        ).all()
        result = []
        for other, engagement, other_job, recruiter in rows:
            if source.platform_candidate_id and source.platform_candidate_id == other.platform_candidate_id and source.platform_id_scope in {"COMPANY", "GLOBAL"} and other.platform_id_scope in {"COMPANY", "GLOBAL"}:
                level, reason = MatchLevel.CONFIRMED_PLATFORM_ID, "已验证的平台候选人 ID 一致"
            elif source.job_id == other.job_id:
                level, reason = MatchLevel.SUSPECTED_SAME_NAME_JOB, "标准化姓名和内部岗位一致"
            elif other_job and other_job.category == category:
                level, reason = MatchLevel.POSSIBLE_SAME_NAME_CATEGORY, "标准化姓名和岗位类别一致"
            else:
                level, reason = MatchLevel.HISTORICAL_SAME_NAME, "仅标准化姓名一致"
            result.append({"match_level": level.value, "candidate_source_id": other.id, "recruiter_id": recruiter.id, "recruiter_name": recruiter.display_name, "job_id": other.job_id, "stage": engagement.stage, "match_reason": reason})
        priority = {level.value: index for index, level in enumerate(MatchLevel)}
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
        engagement = self.session.scalar(select(Engagement).where(Engagement.candidate_source_id == source.id, Engagement.recruiter_id == actor_id, Engagement.job_id == source.job_id))
        stage = payload["event_type"] if payload["event_type"] in {"CLAIMED", "CONTACTED", "RESUME_REQUESTED", "RESUME_RECEIVED", "INTERVIEW_INVITED", "INTERVIEW_COMPLETED", "ON_HOLD", "REJECTED", "HIRED", "CLOSED"} else (engagement.stage if engagement else "CONTACTED")
        if not engagement:
            engagement = Engagement(company_id=company_id, candidate_source_id=source.id, job_id=source.job_id, recruiter_id=actor_id, recruitment_account_id=source.platform_account_id, owner_recruiter_id=actor_id, stage=stage, first_contact_at=now() if stage != "CLAIMED" else None)
            self.session.add(engagement)
        else:
            engagement.stage, engagement.last_activity_at, engagement.version = stage, now(), engagement.version + 1
        event = RecruitmentEvent(company_id=company_id, candidate_source_id=source.id, job_id=source.job_id, recruiter_id=actor_id, event_type=payload["event_type"], idempotency_key=payload["idempotency_key"], metadata_json={"reason": payload.get("reason")})
        self.session.add(event)
        self.session.flush()
        conflicts = self._create_conflicts(source, actor_id, company_id)
        self.session.add(AuditLog(company_id=company_id, actor_id=actor_id, action=payload["event_type"], entity_type="candidate_source", entity_id=source.id, after_json={"stage": stage}))
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
            if match["match_level"] not in {"CONFIRMED_PLATFORM_ID", "SUSPECTED_SAME_NAME_JOB"}:
                continue
            identity_key = source.platform_candidate_id if match["match_level"] == "CONFIRMED_PLATFORM_ID" else source.candidate_normalized_name
            conflict_key = ConflictKeyFactory().create(company_id, source.job_id, identity_key or source.source_identity_key, actor_id, match["recruiter_id"])
            conflict = self.session.scalar(select(Conflict).where(Conflict.conflict_key == conflict_key))
            if conflict and conflict.status not in {"CLOSED", "NOT_SAME_PERSON"}:
                conflict.last_detected_at = now()
                conflict_ids.append(conflict.id)
                continue
            if conflict and conflict.status == "NOT_SAME_PERSON":
                continue
            conflict = Conflict(company_id=company_id, left_candidate_source_id=match["candidate_source_id"], right_candidate_source_id=source.id, job_id=source.job_id, left_recruiter_id=match["recruiter_id"], right_recruiter_id=actor_id, match_level=match["match_level"], match_reason=match["match_reason"], conflict_key=conflict_key)
            self.session.add(conflict)
            self.session.flush()
            payload = {"type": "DUPLICATE_CANDIDATE", "conflict_id": conflict.id, "candidate_name": source.candidate_display_name, "job_id": source.job_id, "match_reason": match["match_reason"]}
            for recruiter_id in (actor_id, match["recruiter_id"]):
                self.session.add(NotificationOutbox(company_id=company_id, event_type="CONFLICT_CREATED", aggregate_type="conflict", aggregate_id=conflict.id, recipient_recruiter_id=recruiter_id, payload_json=payload, idempotency_key=f"conflict:{conflict.id}:v1:{recruiter_id}"))
            conflict_ids.append(conflict.id)
        return conflict_ids

    def exclude_conflict(self, conflict_id: str, actor_id: str, company_id: str, reason: str) -> None:
        conflict = self.session.get(Conflict, conflict_id)
        if not conflict or conflict.company_id != company_id:
            raise ApplicationError("CONFLICT_NOT_FOUND", "冲突不存在", 404)
        left, right = sorted((conflict.left_candidate_source_id, conflict.right_candidate_source_id))
        if not self.session.scalar(select(ConflictExclusion).where(ConflictExclusion.company_id == company_id, ConflictExclusion.left_candidate_source_id == left, ConflictExclusion.right_candidate_source_id == right)):
            self.session.add(ConflictExclusion(company_id=company_id, left_candidate_source_id=left, right_candidate_source_id=right, reason=reason, created_by=actor_id))
        conflict.status, conflict.resolution, conflict.resolved_at, conflict.resolved_by = "NOT_SAME_PERSON", reason, now(), actor_id
        self.session.add(AuditLog(company_id=company_id, actor_id=actor_id, action="NOT_SAME_PERSON", entity_type="conflict", entity_id=conflict.id, after_json={"reason": reason}))
        self.session.commit()
