"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# The types are created once, up front. The column definitions below reference
# them with create_type=False so create_table does not try to CREATE TYPE a
# second time (Postgres has no CREATE TYPE IF NOT EXISTS).
run_status = postgresql.ENUM(
    "pending", "running", "completed", "failed", "canceled", name="run_status"
)
stage_status = postgresql.ENUM(
    "pending", "running", "completed", "failed", "skipped", name="stage_status"
)
stage_name = postgresql.ENUM("titles", "script", "review", "tts", "package", name="stage_name")

run_status_col = postgresql.ENUM(name="run_status", create_type=False)
stage_status_col = postgresql.ENUM(name="stage_status", create_type=False)
stage_name_col = postgresql.ENUM(name="stage_name", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    run_status.create(bind, checkfirst=True)
    stage_status.create(bind, checkfirst=True)
    stage_name.create(bind, checkfirst=True)

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("voice_id", sa.String(64), nullable=False),
        sa.Column("chosen_title", sa.Text(), nullable=True),
        sa.Column("status", run_status_col, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tts_characters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_created_at", "runs", [sa.text("created_at DESC")])

    op.create_table(
        "stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", stage_name_col, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", stage_status_col, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "name", name="uq_stage_run_name"),
    )
    op.create_index("ix_stages_run_id", "stages", ["run_id"])
    op.create_index("ix_stages_input_hash", "stages", ["input_hash"])

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("stages")
    op.drop_table("runs")
    bind = op.get_bind()
    stage_name.drop(bind, checkfirst=True)
    stage_status.drop(bind, checkfirst=True)
    run_status.drop(bind, checkfirst=True)
