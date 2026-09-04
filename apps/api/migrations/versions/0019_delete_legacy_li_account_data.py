"""Delete the legacy 李先生 account and its historical candidate data."""

import sqlalchemy as sa
from alembic import op

revision = "0019_delete_legacy_li_account_data"
down_revision = "0018_merge_li_recruiter_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    account = bind.execute(
        sa.text("SELECT id, recruiter_id FROM recruitment_accounts WHERE account_display_name = '李先生' LIMIT 1")
    ).one_or_none()
    if not account:
        return
    account_id, canonical_recruiter_id = account
    legacy_recruiter_id = bind.execute(
        sa.text("SELECT id FROM recruitment_recruiters WHERE display_name = '李先生' AND status = 'INACTIVE' LIMIT 1")
    ).scalar()

    source_ids = sa.text("SELECT id FROM recruitment_candidate_sources WHERE platform_account_id = :account_id")
    # Outbox notifications reference sources through aggregate_id rather than
    # a foreign key, so remove them before deleting the source rows.
    bind.execute(
        sa.text(
            """
            DELETE FROM recruitment_notifications
            WHERE outbox_id IN (
                SELECT id FROM recruitment_notification_outbox
                WHERE aggregate_id IN (SELECT id FROM recruitment_candidate_sources WHERE platform_account_id = :account_id)
            )
            """
        ),
        {"account_id": account_id},
    )
    bind.execute(
        sa.text(
            "DELETE FROM recruitment_notification_outbox WHERE aggregate_id IN (SELECT id FROM recruitment_candidate_sources WHERE platform_account_id = :account_id)"
        ),
        {"account_id": account_id},
    )
    for table, column in (
        ("recruitment_candidate_sync_outbox", "candidate_source_id"),
        ("recruitment_interviews", "candidate_source_id"),
        ("recruitment_events", "candidate_source_id"),
        ("recruitment_engagements", "candidate_source_id"),
        ("recruitment_conflict_exclusions", "left_candidate_source_id"),
        ("recruitment_conflict_exclusions", "right_candidate_source_id"),
        ("recruitment_conflicts", "left_candidate_source_id"),
        ("recruitment_conflicts", "right_candidate_source_id"),
    ):
        bind.execute(sa.text(f"DELETE FROM {table} WHERE {column} IN ({source_ids.text})"), {"account_id": account_id})
    bind.execute(sa.text("DELETE FROM recruitment_candidate_sources WHERE platform_account_id = :account_id"), {"account_id": account_id})
    bind.execute(sa.text("DELETE FROM recruitment_accounts WHERE id = :account_id"), {"account_id": account_id})

    # The inactive legacy recruiter row is retained only if another table still
    # references it. In the normal merged state it has no remaining references.
    if legacy_recruiter_id:
        bind.execute(sa.text("DELETE FROM recruitment_recruiters WHERE id = :recruiter_id"), {"recruiter_id": legacy_recruiter_id})


def downgrade() -> None:
    pass
