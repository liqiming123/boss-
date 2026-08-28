from sqlalchemy import select

from recruitment_collab.domain.normalization import JobNameNormalizer

from .database import Base, SessionLocal, engine
from .models import Company, JobAlias, Recruiter, RecruitmentAccount, RecruitmentJob, RecruitmentSetting
from .security import hash_password


def seed() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.code == "QXY-TEST"))
        if not company:
            company = Company(name="趣新养招聘测试公司", code="QXY-TEST")
            db.add(company); db.flush()
        users = [
            ("admin@example.com", "系统管理员", "ADMIN", "dev-admin-2026"),
            ("xie@example.com", "谢女士", "RECRUITER", "dev-recruiter-2026"),
            ("jiali@example.com", "珈莉", "RECRUITER", "dev-recruiter-2026"),
            ("daning@example.com", "大宁", "HR_MANAGER", "dev-recruiter-2026"),
        ]
        recruiter_rows = {}
        for email, name, role, password in users:
            row = db.scalar(select(Recruiter).where(Recruiter.email == email))
            if not row:
                row = Recruiter(company_id=company.id, display_name=name, email=email, role=role, password_hash=hash_password(password))
                db.add(row); db.flush()
            recruiter_rows[name] = row
        for name in ("谢女士", "珈莉", "大宁"):
            if not db.scalar(select(RecruitmentAccount).where(RecruitmentAccount.company_id == company.id, RecruitmentAccount.account_display_name == name)):
                db.add(RecruitmentAccount(company_id=company.id, recruiter_id=recruiter_rows[name].id, platform="mock", platform_account_key=f"mock-{name}", account_display_name=name))
        jobs = [("VIDEO_EDITOR", "短视频编导", "视频内容"), ("LIVE_OPERATOR", "直播运营", "直播运营"), ("VIDEO_CUTTER", "视频剪辑", "视频内容")]
        job_rows = {}
        for code, canonical, category in jobs:
            job_row = db.scalar(select(RecruitmentJob).where(RecruitmentJob.company_id == company.id, RecruitmentJob.code == code))
            if not job_row:
                job_row = RecruitmentJob(company_id=company.id, code=code, canonical_name=canonical, category=category); db.add(job_row); db.flush()
            job_rows[code] = job_row
        aliases = [("短视频编导", "VIDEO_EDITOR"), ("内容编导", "VIDEO_EDITOR"), ("抖音编导", "VIDEO_EDITOR"), ("视频编导", "VIDEO_EDITOR"), ("直播运营", "LIVE_OPERATOR"), ("视频剪辑", "VIDEO_CUTTER")]
        normalizer = JobNameNormalizer()
        for alias, code in aliases:
            normalized = normalizer.normalize(alias)
            if not db.scalar(select(JobAlias).where(JobAlias.company_id == company.id, JobAlias.platform == "mock", JobAlias.normalized_alias == normalized)):
                db.add(JobAlias(company_id=company.id, job_id=job_rows[code].id, platform="mock", raw_alias=alias, normalized_alias=normalized))
        if not db.scalar(select(RecruitmentSetting).where(RecruitmentSetting.company_id == company.id)):
            db.add(RecruitmentSetting(company_id=company.id))
        db.commit()


if __name__ == "__main__": seed()
