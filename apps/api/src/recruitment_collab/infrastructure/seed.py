from sqlalchemy import select

from recruitment_collab.domain.normalization import JobNameNormalizer

from .database import SessionLocal
from .models import Company, JobAlias, RecruitmentJob, RecruitmentSetting


def seed() -> None:
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.code == "QXY-TEST"))
        if not company:
            company = Company(name="趣新养招聘测试公司", code="QXY-TEST")
            db.add(company)
            db.flush()
        jobs = [
            ("VIDEO_EDITOR", "短视频编导", "视频内容"),
            ("LIVE_OPERATOR", "直播运营", "直播运营"),
            ("VIDEO_CUTTER", "视频剪辑", "视频内容"),
        ]
        job_rows: dict[str, RecruitmentJob] = {}
        for code, canonical, category in jobs:
            job_row = db.scalar(select(RecruitmentJob).where(RecruitmentJob.company_id == company.id, RecruitmentJob.code == code))
            if not job_row:
                job_row = RecruitmentJob(company_id=company.id, code=code, canonical_name=canonical, category=category)
                db.add(job_row)
                db.flush()
            job_rows[code] = job_row
        aliases = [
            ("短视频编导", "VIDEO_EDITOR"),
            ("内容编导", "VIDEO_EDITOR"),
            ("抖音编导", "VIDEO_EDITOR"),
            ("视频编导", "VIDEO_EDITOR"),
            ("直播运营", "LIVE_OPERATOR"),
            ("视频剪辑", "VIDEO_CUTTER"),
        ]
        normalizer = JobNameNormalizer()
        for alias, code in aliases:
            normalized = normalizer.normalize(alias)
            if not db.scalar(select(JobAlias).where(JobAlias.company_id == company.id, JobAlias.platform == "mock", JobAlias.normalized_alias == normalized)):
                db.add(JobAlias(company_id=company.id, job_id=job_rows[code].id, platform="mock", raw_alias=alias, normalized_alias=normalized))
        if not db.scalar(select(RecruitmentSetting).where(RecruitmentSetting.company_id == company.id)):
            db.add(RecruitmentSetting(company_id=company.id))
        db.commit()


if __name__ == "__main__":
    seed()
