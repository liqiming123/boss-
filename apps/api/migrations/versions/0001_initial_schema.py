"""Initial complete recruitment schema."""
from alembic import op

from recruitment_collab.infrastructure import models  # noqa: F401
from recruitment_collab.infrastructure.database import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)

