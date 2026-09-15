from functools import lru_cache
from typing import List

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = "development-only-secret-key-change-me"
    database_url: str = "sqlite:///./recruitment_dev.db"
    cors_origins: List[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
        ]
    )
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    feishu_mode: str = "mock"
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_encrypt_key: str = ""
    feishu_verification_token: str = ""
    feishu_redirect_uri: str = ""
    feishu_bitable_app_token: str = ""
    feishu_bitable_candidate_table_id: str = ""
    feishu_bitable_followup_table_id: str = ""
    plugin_company_code: str = ""
    # Empty means the management console uses the same scope as the plugin:
    # any active recruiter in the configured company. Set ADMIN_ROLES only
    # when a deployment explicitly needs to narrow that scope.
    admin_roles: List[str] = Field(default_factory=list)
    public_web_url: str = "http://localhost:5173"
    # A cross-BOSS-account duplicate is time-sensitive: a colleague may be
    # about to contact the same person. The same viewer/candidate/job hit is
    # therefore re-notified after this many minutes instead of the old fixed
    # 24-hour window. Set 0 to notify on every detection.
    lookup_alert_cooldown_minutes: int = 30
    candidate_cache_days: int = 30
    diagnostic_retention_days: int = 30
    event_retention_days: int = 90
    audit_retention_days: int = 180
    failed_task_retention_days: int = 30
    # Retention is intentionally a low-frequency maintenance job.  Individual
    # retention windows above still control how much history is kept; this
    # setting controls how often the database sweep runs.
    retention_run_interval_days: int = 30
    extension_package_path: str = "artifacts/recruitment-collab-extension.zip"
    extension_release_metadata_path: str = "artifacts/extension-release.json"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("admin_roles", mode="before")
    @classmethod
    def split_admin_roles(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_development(self) -> bool:
        return self.app_env in {"development", "test"}

    @model_validator(mode="after")
    def validate_production_configuration(self) -> "Settings":
        if self.app_env != "production":
            return self
        errors: list[str] = []
        if len(self.secret_key) < 32 or "development" in self.secret_key or "change-me" in self.secret_key:
            errors.append("SECRET_KEY 必须是至少 32 字符的非默认随机值")
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            errors.append("DATABASE_URL 必须使用 PostgreSQL")
        if self.feishu_mode != "real":
            errors.append("FEISHU_MODE 必须为 real")
        required = {
            "FEISHU_APP_ID": self.feishu_app_id,
            "FEISHU_APP_SECRET": self.feishu_app_secret,
            "FEISHU_REDIRECT_URI": self.feishu_redirect_uri,
            "FEISHU_BITABLE_APP_TOKEN": self.feishu_bitable_app_token,
            "FEISHU_BITABLE_CANDIDATE_TABLE_ID": self.feishu_bitable_candidate_table_id,
            "FEISHU_ENCRYPT_KEY": self.feishu_encrypt_key,
            "FEISHU_VERIFICATION_TOKEN": self.feishu_verification_token,
            "PLUGIN_COMPANY_CODE": self.plugin_company_code,
        }
        errors.extend(f"{name} 未配置" for name, value in required.items() if not value.strip())
        if self.feishu_redirect_uri and not self.feishu_redirect_uri.startswith("https://"):
            errors.append("FEISHU_REDIRECT_URI 必须使用 HTTPS")
        if self.public_web_url and not self.public_web_url.startswith("https://"):
            errors.append("PUBLIC_WEB_URL 必须使用 HTTPS")
        if not self.cors_origins or any(origin == "*" or not origin.startswith("https://") for origin in self.cors_origins):
            errors.append("CORS_ORIGINS 必须只包含明确的 HTTPS Origin")
        retention = (
            self.candidate_cache_days,
            self.diagnostic_retention_days,
            self.event_retention_days,
            self.audit_retention_days,
            self.failed_task_retention_days,
            self.retention_run_interval_days,
        )
        if any(value <= 0 for value in retention):
            errors.append("所有数据保留期限必须大于 0")
        if errors:
            raise ValueError("生产配置无效：" + "；".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
