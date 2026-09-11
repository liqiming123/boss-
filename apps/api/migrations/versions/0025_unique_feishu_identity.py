"""Prevent one Feishu identity being bound to multiple recruiters."""

import sqlalchemy as sa
from alembic import op

revision = "0025_unique_feishu_identity"
down_revision = "0024_bind_li先生_admin_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    names = {index["name"] for index in inspector.get_indexes("recruitment_recruiters")}
    if "uq_recruiter_company_feishu_open_id" not in names:
        op.create_index(
            "uq_recruiter_company_feishu_open_id",
            "recruitment_recruiters",
            ["company_id", "feishu_open_id"],
            unique=True,
            postgresql_where=sa.text("feishu_open_id IS NOT NULL"),
            sqlite_where=sa.text("feishu_open_id IS NOT NULL"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    names = {index["name"] for index in inspector.get_indexes("recruitment_recruiters")}
    if "uq_recruiter_company_feishu_open_id" in names:
        op.drop_index("uq_recruiter_company_feishu_open_id", table_name="recruitment_recruiters")
