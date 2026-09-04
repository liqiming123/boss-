"""Add Feishu-only resume attachment metadata.

Revision ID: 0009_resume_attachments
Revises: 0008_worker_heartbeats
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_resume_attachments"
down_revision = "0008_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("recruitment_candidate_sources")}
    additions = {
        "resume_tokens_json": sa.Column("resume_tokens_json", sa.JSON(), nullable=False, server_default="[]"),
        "resume_hash": sa.Column("resume_hash", sa.String(length=64), nullable=True),
        "resume_status": sa.Column("resume_status", sa.String(length=30), nullable=False, server_default="NONE"),
        "resume_file_name": sa.Column("resume_file_name", sa.String(length=255), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("recruitment_candidate_sources", column)


def downgrade() -> None:
    for name in ("resume_file_name", "resume_status", "resume_hash", "resume_tokens_json"):
        op.drop_column("recruitment_candidate_sources", name)
