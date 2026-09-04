import hashlib
from typing import Optional

from .normalization import CandidateNameNormalizer, JobNameNormalizer


def candidate_source_identity(
    platform: str,
    account_id: str,
    candidate_name: str,
    raw_job_name: str,
    page_url_hash: str,
    platform_candidate_id: Optional[str] = None,
) -> str:
    if platform_candidate_id:
        material = f"{platform}|{account_id}|platform|{platform_candidate_id}|job|{JobNameNormalizer().normalize(raw_job_name)}"
    elif page_url_hash:
        material = f"{platform}|{account_id}|page|{page_url_hash}"
    else:
        name = CandidateNameNormalizer().normalize(candidate_name)
        job = JobNameNormalizer().normalize(raw_job_name)
        material = f"{platform}|{account_id}|fallback|{name}|{job}"
    return hashlib.sha256(material.encode()).hexdigest()


def conflict_key(company_id: str, job_id: Optional[str], identity_key: str, recruiter_a: str, recruiter_b: str) -> str:
    left, right = sorted((recruiter_a, recruiter_b))
    return hashlib.sha256("|".join((company_id, job_id or "none", identity_key, left, right)).encode()).hexdigest()
