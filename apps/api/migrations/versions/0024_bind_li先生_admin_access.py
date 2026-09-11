"""Grant company administration to the Feishu-bound 李先生 account."""

import sqlalchemy as sa
from alembic import op

revision = "0024_bind_li先生_admin_access"
down_revision = "0023_seed_active_feishu_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE recruitment_recruiters
            SET role = 'ADMIN', updated_at = CURRENT_TIMESTAMP
            WHERE display_name = '李先生'
              AND feishu_display_name = '李启明'
              AND feishu_open_id IS NOT NULL
              AND status = 'ACTIVE'
            """
        )
    )
    # 0022 creates profiles before this repair is needed in already-upgraded
    # deployments; keep the capability state aligned with the repaired role.
    bind.execute(
        sa.text(
            """
            UPDATE recruitment_recruiter_access_profiles
            SET data_scope = 'COMPANY',
                can_manage_team = TRUE,
                can_manage_feishu = TRUE,
                can_manage_jobs = TRUE,
                can_reset_system = TRUE,
                updated_at = CURRENT_TIMESTAMP
            WHERE recruiter_id IN (
                SELECT id FROM recruitment_recruiters
                WHERE display_name = '李先生'
                  AND feishu_display_name = '李启明'
                  AND feishu_open_id IS NOT NULL
                  AND status = 'ACTIVE'
            )
            """
        )
    )


def downgrade() -> None:
    # Do not revoke an explicitly repaired authorization during rollback.
    pass
