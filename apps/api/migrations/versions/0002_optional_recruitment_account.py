"""Make recruitment account mapping optional.

Revision ID: 0002_optional_account
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_optional_account"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        batch.alter_column("platform_account_id", existing_type=sa.String(length=36), nullable=True)
    with op.batch_alter_table("recruitment_engagements") as batch:
        batch.alter_column("recruitment_account_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("recruitment_engagements") as batch:
        batch.alter_column("recruitment_account_id", existing_type=sa.String(length=36), nullable=False)
    with op.batch_alter_table("recruitment_candidate_sources") as batch:
        batch.alter_column("platform_account_id", existing_type=sa.String(length=36), nullable=False)
