"""Restore the admin role lost when the Feishu identity moved to 李启明."""

import sqlalchemy as sa
from alembic import op

revision = "0020_restore_li_admin_role"
down_revision = "0019_delete_legacy_li_account_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE recruitment_recruiters
            SET role = 'ADMIN', updated_at = CURRENT_TIMESTAMP
            WHERE display_name = '李启明'
              AND feishu_display_name = '李启明'
              AND feishu_open_id IS NOT NULL
              AND role NOT IN ('ADMIN', 'HR_MANAGER')
              AND EXISTS (
                  SELECT 1
                  FROM recruitment_recruiters legacy_admin
                  WHERE legacy_admin.company_id = recruitment_recruiters.company_id
                    AND legacy_admin.display_name = '开发管理员'
                    AND legacy_admin.role = 'ADMIN'
                    AND legacy_admin.status = 'ACTIVE'
              )
            """
        )
    )


def downgrade() -> None:
    # This is an authorization repair. Automatically removing the restored
    # permission during a rollback would be unsafe.
    pass
