"""script length: target_minutes on runs

The user now says how long the narration should be, picked at the same gate as
the title. Nullable because every run created before this migration has no
answer, and the script stage falls back to the configured default.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("target_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "target_minutes")
