"""Remove settings superseded by fixed confirmed business rules.

Revision ID: 0012_remove_unused_settings
Revises: 0011_remove_device_codes
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_remove_unused_settings"
down_revision = "0011_remove_device_codes"
branch_labels = None
depends_on = None

TABLE = "recruitment_settings"
REMOVED_COLUMNS = ("notify_on_view", "renotify_interval_hours", "allow_company_level_rejection_roles")


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        for name in REMOVED_COLUMNS:
            if name in existing:
                batch.drop_column(name)


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        if "notify_on_view" not in existing:
            batch.add_column(sa.Column("notify_on_view", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "renotify_interval_hours" not in existing:
            batch.add_column(sa.Column("renotify_interval_hours", sa.Integer(), nullable=False, server_default="24"))
        if "allow_company_level_rejection_roles" not in existing:
            batch.add_column(sa.Column("allow_company_level_rejection_roles", sa.JSON(), nullable=False, server_default='["ADMIN", "HR_MANAGER"]'))
