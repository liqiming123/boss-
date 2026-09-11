"""Requeue daily rows after an explicitly confirmed Feishu table reset."""
from alembic import op

revision = "0029_requeue_daily_after_table_reset"
down_revision = "0028_company_daily_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The destination table was explicitly cleared; old record ids are no
    # longer valid. Rebuild every logical row through the normal worker.
    op.execute("""
        UPDATE boss_company_daily_rows
        SET status = 'PENDING', feishu_record_id = NULL, retry_count = 0,
            last_error = NULL, next_retry_at = CURRENT_TIMESTAMP
    """)


def downgrade() -> None:
    # Data requeue is intentionally irreversible; rows remain valid business
    # data and must not be restored to deleted external record ids.
    pass
