"""Re-enable user-authorized resume attachment metadata after the security review."""

import sqlalchemy as sa
from alembic import op

revision = "0014_resume_attachments_reenabled"
down_revision = "0013_remove_redundant_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Alembic creates version_num as VARCHAR(32). This revision identifier is
    # longer than that, so PostgreSQL must widen the bookkeeping column before
    # Alembic stamps the completed revision. SQLite does not enforce the
    # declared length and needs no schema rewrite.
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("recruitment_candidate_sources")}
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        if "resume_tokens_json" not in columns:
            batch.add_column(sa.Column("resume_tokens_json", sa.JSON(), nullable=False, server_default="[]"))
        if "resume_hash" not in columns:
            batch.add_column(sa.Column("resume_hash", sa.String(length=64), nullable=True))
        if "resume_status" not in columns:
            batch.add_column(sa.Column("resume_status", sa.String(length=30), nullable=False, server_default="NONE"))
        if "resume_file_name" not in columns:
            batch.add_column(sa.Column("resume_file_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        for name in ("resume_file_name", "resume_status", "resume_hash", "resume_tokens_json"):
            batch.drop_column(name)
