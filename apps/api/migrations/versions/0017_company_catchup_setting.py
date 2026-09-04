"""Add company-wide extension catch-up setting."""
import sqlalchemy as sa
from alembic import op

revision = "0017_company_catchup_setting"
down_revision = "0016_remove_chat_summary"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("recruitment_settings") as batch:
        batch.add_column(sa.Column("catchup_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))

def downgrade() -> None:
    with op.batch_alter_table("recruitment_settings") as batch:
        batch.drop_column("catchup_enabled")
