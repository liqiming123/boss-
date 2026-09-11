"""Rewind the incomplete 李启明 catch-up and remove the obsolete account checkpoint."""

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision = "0021_rewind_incomplete_catchup"
down_revision = "0020_restore_li_admin_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    checkpoints = sa.table(
        "recruitment_conversation_scan_checkpoints",
        sa.column("account_display_name", sa.String()),
        sa.column("completed_through_at", sa.DateTime(timezone=True)),
        sa.column("cursor_json", sa.JSON()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind.execute(checkpoints.delete().where(checkpoints.c.account_display_name == "李先生"))
    rewind_to = datetime.now(timezone.utc) - timedelta(hours=48)
    bind.execute(
        checkpoints.update()
        .where(checkpoints.c.account_display_name == "李启明")
        .values(
            completed_through_at=rewind_to,
            cursor_json={"complete": False, "reason": "incomplete_scan_repair"},
            updated_at=datetime.now(timezone.utc),
        )
    )


def downgrade() -> None:
    # A catch-up watermark must not be moved forward automatically.
    pass
