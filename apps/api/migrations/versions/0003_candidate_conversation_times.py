"""Store verified candidate conversation timestamps.

Revision ID: 0003_conversation_times
Revises: 0002_optional_account
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_conversation_times"
down_revision = "0002_optional_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("recruitment_candidate_sources")}
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        if "conversation_started_at" not in columns:
            batch.add_column(sa.Column("conversation_started_at", sa.DateTime(timezone=True), nullable=True))
        if "conversation_updated_at" not in columns:
            batch.add_column(sa.Column("conversation_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        batch.drop_column("conversation_updated_at")
        batch.drop_column("conversation_started_at")
