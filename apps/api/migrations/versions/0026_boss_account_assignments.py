"""Track BOSS accounts separately from their current Feishu owner."""
import sqlalchemy as sa
from alembic import op

revision = "0026_boss_account_assignments"
down_revision = "0025_unique_feishu_identity"
branch_labels = None
depends_on = None

def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "recruitment_boss_account_assignments" not in inspector.get_table_names():
        op.create_table(
        "recruitment_boss_account_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("boss_account_id", sa.String(36), sa.ForeignKey("recruitment_accounts.id"), nullable=False),
        sa.Column("feishu_recruiter_id", sa.String(36), sa.ForeignKey("recruitment_recruiters.id"), nullable=True),
        sa.Column("feishu_open_id", sa.String(100), nullable=True),
        sa.Column("feishu_display_name", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("assignment_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("uq_boss_assignment_active_account", "recruitment_boss_account_assignments", ["boss_account_id"], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"), sqlite_where=sa.text("status = 'ACTIVE'"))
        op.create_index("uq_boss_assignment_active_feishu", "recruitment_boss_account_assignments", ["company_id", "feishu_recruiter_id"], unique=True, postgresql_where=sa.text("status = 'ACTIVE' AND feishu_recruiter_id IS NOT NULL"), sqlite_where=sa.text("status = 'ACTIVE' AND feishu_recruiter_id IS NOT NULL"))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, company_id, recruiter_id FROM recruitment_accounts WHERE status='ACTIVE'"))
    for account_id, company_id, recruiter_id in rows:
        if bind.execute(sa.text("SELECT 1 FROM recruitment_boss_account_assignments WHERE boss_account_id=:id AND status='ACTIVE'"), {"id": account_id}).first():
            continue
        rec = bind.execute(sa.text("SELECT feishu_open_id, feishu_display_name FROM recruitment_recruiters WHERE id=:id"), {"id": recruiter_id}).first()
        open_id = rec[0] if rec else None
        if open_id and bind.execute(sa.text("SELECT 1 FROM recruitment_boss_account_assignments WHERE company_id=:company AND feishu_recruiter_id=:recruiter AND status='ACTIVE'"), {"company": company_id, "recruiter": recruiter_id}).first():
            open_id = None
        bind.execute(sa.text("INSERT INTO recruitment_boss_account_assignments (id,company_id,boss_account_id,feishu_recruiter_id,feishu_open_id,feishu_display_name,status,assignment_version,assigned_at,created_at,updated_at) VALUES (:id,:c,:a,:r,:o,:n,'ACTIVE',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {"id": str(__import__('uuid').uuid4()), "c": company_id, "a": account_id, "r": recruiter_id if open_id else None, "o": open_id, "n": rec[1] if open_id else None})

def downgrade() -> None:
    op.drop_index("uq_boss_assignment_active_feishu", table_name="recruitment_boss_account_assignments")
    op.drop_index("uq_boss_assignment_active_account", table_name="recruitment_boss_account_assignments")
    op.drop_table("recruitment_boss_account_assignments")
