"""Add managed Feishu table configuration, capability profiles and reset jobs."""

import os
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - production dependency is present
    dotenv_values = None

import sqlalchemy as sa
from alembic import op

revision = "0022_recruitment_workspace_reset"
down_revision = "0021_rewind_incomplete_catchup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("recruitment_settings")}
    if "reset_in_progress" not in existing:
        op.add_column("recruitment_settings", sa.Column("reset_in_progress", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "reset_generation" not in existing:
        op.add_column("recruitment_settings", sa.Column("reset_generation", sa.Integer(), nullable=False, server_default="0"))
    inspector = sa.inspect(op.get_bind())
    if "recruitment_feishu_bitable_configs" not in inspector.get_table_names():
        op.create_table(
        "recruitment_feishu_bitable_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("table_url", sa.String(500), nullable=False),
        sa.Column("app_token", sa.String(120), nullable=False),
        sa.Column("candidate_table_id", sa.String(120), nullable=False),
        sa.Column("table_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("validation_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "ix_feishu_bitable_config_company_status" not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("recruitment_feishu_bitable_configs")}:
        op.create_index("ix_feishu_bitable_config_company_status", "recruitment_feishu_bitable_configs", ["company_id", "status"])
    if "uq_feishu_bitable_active_company" not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("recruitment_feishu_bitable_configs")}:
        op.create_index(
        "uq_feishu_bitable_active_company",
        "recruitment_feishu_bitable_configs",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
        )
    if "recruitment_recruiter_access_profiles" not in inspector.get_table_names():
        op.create_table(
        "recruitment_recruiter_access_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False, unique=True),
        sa.Column("data_scope", sa.String(20), nullable=False, server_default="OWN"),
        sa.Column("can_manage_team", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_manage_feishu", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_manage_jobs", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_reset_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    profile_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("recruitment_recruiter_access_profiles")}
    if "ix_recruiter_access_profile_company" not in profile_indexes:
        op.create_index("ix_recruiter_access_profile_company", "recruitment_recruiter_access_profiles", ["company_id"])
    if "ix_recruiter_access_profile_recruiter" not in profile_indexes:
        op.create_index("ix_recruiter_access_profile_recruiter", "recruitment_recruiter_access_profiles", ["recruiter_id"])
    if "recruitment_system_reset_jobs" not in inspector.get_table_names():
        op.create_table(
        "recruitment_system_reset_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False, unique=True),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("preview_version", sa.String(64), nullable=False),
        sa.Column("counts_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "ix_system_reset_job_company" not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("recruitment_system_reset_jobs")}:
        op.create_index("ix_system_reset_job_company", "recruitment_system_reset_jobs", ["company_id"])

    # Migrate the current environment-based target once. Credentials remain
    # environment-only; only opaque app/table identifiers are persisted.
    env_values = dotenv_values(Path.cwd() / ".env") if dotenv_values else {}
    app_token = (os.getenv("FEISHU_BITABLE_APP_TOKEN") or env_values.get("FEISHU_BITABLE_APP_TOKEN") or "").strip()
    table_id = (os.getenv("FEISHU_BITABLE_CANDIDATE_TABLE_ID") or env_values.get("FEISHU_BITABLE_CANDIDATE_TABLE_ID") or "").strip()
    if app_token and table_id:
        companies = op.get_bind().execute(sa.text("SELECT id FROM companies WHERE status = 'ACTIVE' LIMIT 1")).fetchall()
        for (company_id,) in companies:
            op.get_bind().execute(
                sa.text(
                    "INSERT INTO recruitment_feishu_bitable_configs "
                    "(id, company_id, table_url, app_token, candidate_table_id, table_name, status, validation_json, "
                    "validated_at, activated_at, created_by, created_at, updated_at) "
                    "VALUES (:id, :company_id, :url, :app_token, :table_id, :name, 'ACTIVE', :validation, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": "env-migrated-" + company_id,
                    "company_id": company_id,
                    "url": "",
                    "app_token": app_token,
                    "table_id": table_id,
                    "name": "环境变量迁移的候选人表",
                    "validation": "{}",
                },
            )

    # Existing administrators retain company scope; all other identities start
    # with their own-data scope. The reset flow can later remove legacy rows.
    rows = op.get_bind().execute(sa.text("SELECT id, company_id, role FROM recruitment_recruiters")).fetchall()
    for recruiter_id, company_id, role in rows:
        company_scope = str(role).upper() == "ADMIN"
        op.get_bind().execute(
            sa.text(
                "INSERT INTO recruitment_recruiter_access_profiles "
                "(id, company_id, recruiter_id, data_scope, can_manage_team, can_manage_feishu, can_manage_jobs, can_reset_system, created_at, updated_at) "
                "VALUES (:id, :company_id, :recruiter_id, :scope, :team, :feishu, :jobs, :reset, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": recruiter_id,
                "company_id": company_id,
                "recruiter_id": recruiter_id,
                "scope": "COMPANY" if company_scope else "OWN",
                "team": company_scope,
                "feishu": company_scope,
                "jobs": company_scope,
                "reset": company_scope,
            },
        )


def downgrade() -> None:
    op.drop_column("recruitment_settings", "reset_generation")
    op.drop_column("recruitment_settings", "reset_in_progress")
    op.drop_index("ix_system_reset_job_company", table_name="recruitment_system_reset_jobs")
    op.drop_table("recruitment_system_reset_jobs")
    op.drop_index("ix_recruiter_access_profile_recruiter", table_name="recruitment_recruiter_access_profiles")
    op.drop_index("ix_recruiter_access_profile_company", table_name="recruitment_recruiter_access_profiles")
    op.drop_table("recruitment_recruiter_access_profiles")
    op.drop_index("uq_feishu_bitable_active_company", table_name="recruitment_feishu_bitable_configs")
    op.drop_index("ix_feishu_bitable_config_company_status", table_name="recruitment_feishu_bitable_configs")
    op.drop_table("recruitment_feishu_bitable_configs")
