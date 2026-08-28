from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from recruitment_collab.infrastructure.database import Base, get_db
from recruitment_collab.infrastructure.models import Company, JobAlias, Recruiter, RecruitmentAccount, RecruitmentJob, RecruitmentSetting
from recruitment_collab.infrastructure.security import hash_password
from recruitment_collab.main import app


@pytest.fixture()
def session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        company = Company(name="测试公司", code="TEST"); db.add(company); db.flush()
        users = []
        for email, name, role in [("admin@example.com", "管理员", "ADMIN"), ("xie@example.com", "谢女士", "RECRUITER"), ("jiali@example.com", "珈莉", "RECRUITER")]:
            user = Recruiter(company_id=company.id, display_name=name, email=email, role=role, password_hash=hash_password("dev-password")); db.add(user); db.flush(); users.append(user)
        for user in users[1:]: db.add(RecruitmentAccount(company_id=company.id, recruiter_id=user.id, platform="mock", platform_account_key=user.email, account_display_name=user.display_name))
        job = RecruitmentJob(company_id=company.id, code="VIDEO", canonical_name="短视频编导", category="视频内容"); db.add(job); db.flush()
        db.add(JobAlias(company_id=company.id, job_id=job.id, platform="mock", raw_alias="短视频编导", normalized_alias="短视频编导")); db.add(RecruitmentSetting(company_id=company.id)); db.commit()
        yield db


@pytest.fixture()
def client(session: Session):
    def override(): yield session
    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client: yield test_client
    app.dependency_overrides.clear()


def login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/v1/dev/login", json={"email": email, "password": "dev-password"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

