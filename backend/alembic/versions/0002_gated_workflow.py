"""gated workflow: awaiting_input status, nullable voice_id

The user now picks the title and the voice partway through a run, so both
arrive after the row is created and the run needs a status for "parked on a
human decision".

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # Postgres, and Alembic wraps migrations in one. COMMIT first.
    op.execute("COMMIT")
    op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'awaiting_input'")

    op.alter_column("runs", "voice_id", existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    # Runs parked mid-workflow have no voice yet; give them a placeholder so
    # the column can go back to NOT NULL.
    op.execute("UPDATE runs SET voice_id = 'narrator_default' WHERE voice_id IS NULL")
    op.alter_column("runs", "voice_id", existing_type=sa.String(64), nullable=False)
    # Postgres cannot drop a single enum value; the label is left in place.
