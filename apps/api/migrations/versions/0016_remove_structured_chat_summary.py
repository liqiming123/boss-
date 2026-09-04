"""Remove structured chat fields; keep the chat screenshot attachment only."""

import sqlalchemy as sa
from alembic import op

revision = "0016_remove_chat_summary"
down_revision = "0015_structured_chat_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("recruitment_candidate_sources")}
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        for name in (
            "recent_chat_actions_json",
            "last_candidate_message_at",
            "last_recruiter_message_at",
            "candidate_message_count",
            "recruiter_message_count",
            "chat_summary_status",
        ):
            if name in columns:
                batch.drop_column(name)


def downgrade() -> None:
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        batch.add_column(sa.Column("chat_summary_status", sa.String(length=20), nullable=False, server_default="NONE"))
        batch.add_column(sa.Column("recruiter_message_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("candidate_message_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_recruiter_message_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_candidate_message_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("recent_chat_actions_json", sa.JSON(), nullable=False, server_default="[]"))
