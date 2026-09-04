"""Merge the legacy BOSS recruiter identity 李先生 into the bound Feishu identity 李启明."""

import sqlalchemy as sa
from alembic import op

revision = "0018_merge_li_recruiter_identity"
down_revision = "0017_company_catchup_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    old, new = bind.execute(
        sa.text(
            """
            SELECT old.id, new.id
            FROM recruitment_recruiters old
            JOIN recruitment_recruiters new ON new.company_id = old.company_id
            WHERE old.display_name = '李先生'
              AND new.display_name = '李启明'
              AND new.feishu_open_id IS NOT NULL
              AND old.id <> new.id
            ORDER BY old.created_at
            LIMIT 1
            """
        )
    ).one_or_none() or (None, None)
    if not old or not new:
        return

    # Merge rows that identify the same candidate/job/recruiter before changing
    # the foreign key, otherwise the engagement unique constraint would fail.
    duplicates = bind.execute(
        sa.text(
            """
            SELECT old.id, current.id
            FROM recruitment_engagements old
            JOIN recruitment_engagements current
              ON current.candidate_source_id = old.candidate_source_id
             AND current.job_id IS NOT DISTINCT FROM old.job_id
             AND current.recruiter_id = :new
            WHERE old.recruiter_id = :old
            """
        ),
        {"old": old, "new": new},
    ).all()
    for old_id, current_id in duplicates:
        bind.execute(
            sa.text(
                """
                UPDATE recruitment_engagements current
                SET first_contact_at = COALESCE(current.first_contact_at, old.first_contact_at),
                    last_activity_at = GREATEST(current.last_activity_at, old.last_activity_at),
                    version = GREATEST(current.version, old.version),
                    updated_at = CURRENT_TIMESTAMP
                FROM recruitment_engagements old
                WHERE current.id = :current_id AND old.id = :old_id
                """
            ),
            {"old_id": old_id, "current_id": current_id},
        )
        bind.execute(sa.text("DELETE FROM recruitment_engagements WHERE id = :id"), {"id": old_id})

    fk_updates = (
        ("recruitment_engagements", "recruiter_id"),
        ("recruitment_engagements", "owner_recruiter_id"),
        ("recruitment_events", "recruiter_id"),
        ("recruitment_interviews", "recruiter_id"),
        ("recruitment_conflicts", "left_recruiter_id"),
        ("recruitment_conflicts", "right_recruiter_id"),
        ("recruitment_conflicts", "resolved_by"),
        ("recruitment_conflict_exclusions", "created_by"),
        ("recruitment_notification_outbox", "recipient_recruiter_id"),
        ("recruitment_notifications", "recipient_recruiter_id"),
        ("recruitment_plugin_devices", "recruiter_id"),
        ("recruitment_duplicate_lookup_alerts", "viewer_recruiter_id"),
        ("recruitment_duplicate_lookup_alerts", "matched_recruiter_id"),
        ("recruitment_plugin_diagnostics", "recruiter_id"),
        ("recruitment_audit_logs", "actor_id"),
        ("mock_feishu_messages", "recipient_recruiter_id"),
        ("recruitment_feishu_binding_attempts", "recruiter_id"),
    )
    for table, column in fk_updates:
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"),
            {"old": old, "new": new},
        )

    # Keep the account/source history, but make it owned by the canonical user.
    bind.execute(
        sa.text("UPDATE recruitment_accounts SET recruiter_id = :new WHERE recruiter_id = :old"),
        {"old": old, "new": new},
    )
    bind.execute(
        sa.text("UPDATE recruitment_recruiters SET status = 'INACTIVE' WHERE id = :old"),
        {"old": old},
    )


def downgrade() -> None:
    # Identity merges are intentionally irreversible; historical rows remain
    # linked to the canonical Feishu-bound recruiter.
    pass
