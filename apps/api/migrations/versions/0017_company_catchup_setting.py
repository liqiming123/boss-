"""Add company-wide extension catch-up setting."""
import sqlalchemy as sa
from alembic import op

revision = "0017_company_catchup_setting"
down_revision = "0016_remove_chat_summary"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 0001 historically created the schema from the then-current ORM model.
    # On a fresh checkout that model may already contain this column, so make
    # this migration idempotent instead of asking SQLite batch mode to rebuild
    # a table with a column that is already present.
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("recruitment_settings")}
    if "catchup_enabled" not in existing:
        op.add_column("recruitment_settings", sa.Column("catchup_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))

def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("recruitment_settings")}
    if "catchup_enabled" in existing:
        op.drop_column("recruitment_settings", "catchup_enabled")
