import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from .enums import EngagementStage, MatchLevel, PlatformIdScope
from .normalization import CandidateNameNormalizer, JobNameNormalizer


class DomainError(ValueError):
    pass


@dataclass(frozen=True)
class MatchCandidate:
    source_id: str
    recruiter_id: str
    normalized_name: str
    job_id: Optional[str]
    job_category: Optional[str]
    platform_candidate_id: Optional[str]
    platform_id_scope: PlatformIdScope
    stage: Optional[str] = None


@dataclass(frozen=True)
class MatchEvidence:
    match_level: MatchLevel
    matched_candidate_source_id: str
    matched_recruiter_id: str
    matched_job_id: Optional[str]
    matched_stage: Optional[str]
    match_reason: str
    confidence_type: str


class CandidateSourceIdentityFactory:
    def create(
        self,
        platform: str,
        account_id: str,
        candidate_name: str,
        raw_job_name: str,
        page_url_hash: str,
        platform_candidate_id: Optional[str] = None,
    ) -> str:
        if platform_candidate_id:
            material = f"{platform}|{account_id}|platform|{platform_candidate_id}"
        elif page_url_hash:
            material = f"{platform}|{account_id}|page|{page_url_hash}"
        else:
            name = CandidateNameNormalizer().normalize(candidate_name)
            job = JobNameNormalizer().normalize(raw_job_name)
            material = f"{platform}|{account_id}|fallback|{name}|{job}"
        return hashlib.sha256(material.encode()).hexdigest()


class ConflictKeyFactory:
    def create(
        self, company_id: str, job_id: Optional[str], identity_key: str, recruiter_a: str, recruiter_b: str
    ) -> str:
        left, right = sorted((recruiter_a, recruiter_b))
        material = "|".join((company_id, job_id or "none", identity_key, left, right))
        return hashlib.sha256(material.encode()).hexdigest()


class CandidateDuplicateMatcher:
    VERIFIED_SCOPES = {PlatformIdScope.COMPANY, PlatformIdScope.GLOBAL}

    def match(
        self,
        current: MatchCandidate,
        candidates: Sequence[MatchCandidate],
        excluded_source_pairs: Sequence[tuple[str, str]] = (),
    ) -> list[MatchEvidence]:
        exclusions = {tuple(sorted(pair)) for pair in excluded_source_pairs}
        evidence: list[MatchEvidence] = []
        for other in candidates:
            if other.source_id == current.source_id or other.recruiter_id == current.recruiter_id:
                continue
            if tuple(sorted((current.source_id, other.source_id))) in exclusions:
                continue
            level: Optional[MatchLevel] = None
            reason = ""
            if (
                current.platform_candidate_id
                and current.platform_candidate_id == other.platform_candidate_id
                and current.platform_id_scope in self.VERIFIED_SCOPES
                and other.platform_id_scope in self.VERIFIED_SCOPES
            ):
                level, reason = MatchLevel.CONFIRMED_PLATFORM_ID, "已验证的平台候选人 ID 一致"
            elif current.normalized_name == other.normalized_name and current.job_id and current.job_id == other.job_id:
                level, reason = MatchLevel.SUSPECTED_SAME_NAME_JOB, "标准化姓名和内部岗位一致"
            elif (
                current.normalized_name == other.normalized_name
                and current.job_category
                and current.job_category == other.job_category
            ):
                level, reason = MatchLevel.POSSIBLE_SAME_NAME_CATEGORY, "标准化姓名和岗位类别一致"
            elif current.normalized_name == other.normalized_name:
                level, reason = MatchLevel.HISTORICAL_SAME_NAME, "仅标准化姓名一致"
            if level:
                evidence.append(
                    MatchEvidence(level, other.source_id, other.recruiter_id, other.job_id, other.stage, reason, level.value)
                )
        order = {item: index for index, item in enumerate(MatchLevel)}
        return sorted(evidence, key=lambda item: order[item.match_level])


class EngagementStateMachine:
    ALLOWED = {
        EngagementStage.CLAIMED: {EngagementStage.CONTACTED, EngagementStage.CLOSED},
        EngagementStage.CONTACTED: {EngagementStage.RESUME_REQUESTED, EngagementStage.INTERVIEW_INVITED, EngagementStage.REJECTED, EngagementStage.ON_HOLD, EngagementStage.CLOSED},
        EngagementStage.RESUME_REQUESTED: {EngagementStage.RESUME_RECEIVED, EngagementStage.REJECTED, EngagementStage.CLOSED},
        EngagementStage.RESUME_RECEIVED: {EngagementStage.INTERVIEW_INVITED, EngagementStage.REJECTED, EngagementStage.CLOSED},
        EngagementStage.INTERVIEW_INVITED: {EngagementStage.INTERVIEW_COMPLETED, EngagementStage.REJECTED, EngagementStage.CLOSED},
        EngagementStage.INTERVIEW_COMPLETED: {EngagementStage.HIRED, EngagementStage.REJECTED, EngagementStage.ON_HOLD, EngagementStage.CLOSED},
        EngagementStage.ON_HOLD: {EngagementStage.CONTACTED, EngagementStage.REJECTED, EngagementStage.CLOSED},
        EngagementStage.REJECTED: {EngagementStage.CONTACTED, EngagementStage.CLOSED},
        EngagementStage.HIRED: {EngagementStage.CLOSED},
        EngagementStage.CLOSED: set(),
    }

    def ensure_transition(self, current: EngagementStage, target: EngagementStage) -> None:
        if target != current and target not in self.ALLOWED[current]:
            raise DomainError(f"不允许从 {current.value} 变更为 {target.value}")


class ConflictNotificationPolicy:
    def should_notify(self, notify_on_contact: bool, conflict_created: bool, version: int, last_notified: int) -> bool:
        return notify_on_contact and (conflict_created or version > last_notified)
