import pytest
from pydantic import ValidationError

from recruitment_collab.config.settings import Settings


def test_production_configuration_fails_closed_with_development_defaults():
    with pytest.raises(ValidationError, match="生产配置无效"):
        Settings(app_env="production")


def test_complete_production_configuration_is_accepted():
    settings = Settings(
        app_env="production",
        secret_key="x" * 48,
        database_url="postgresql+psycopg://app:secret@db/recruitment",
        feishu_mode="real",
        feishu_app_id="app-id",
        feishu_app_secret="app-secret",
        feishu_redirect_uri="https://api.example.com/api/v1/auth/feishu/callback",
        feishu_bitable_app_token="base-token",
        feishu_bitable_candidate_table_id="table-id",
        feishu_encrypt_key="encrypt-key",
        feishu_verification_token="verification-token",
        plugin_company_code="COMPANY",
        public_web_url="https://admin.example.com",
        cors_origins=["https://admin.example.com"],
    )
    assert settings.app_env == "production"
