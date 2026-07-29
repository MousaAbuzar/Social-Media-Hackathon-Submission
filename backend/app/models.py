import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Postgres is the deployment target, but keeping the ORM types portable lets the
# orchestration tests run on SQLite too — so contributors don't need a database
# to get a green suite. CI still runs them against real Postgres.
JsonDoc = JSON().with_variant(JSONB(), "postgresql")


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class StageStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class StageName(StrEnum):
    titles = "titles"
    script = "script"
    review = "review"
    tts = "tts"
    package = "package"


# The pipeline runs stages in this order. Advancing is a pure function of
# this list plus the last completed stage, so a crashed worker can resume
# by asking "what is the first non-completed stage?".
STAGE_ORDER: list[StageName] = [
    StageName.titles,
    StageName.script,
    StageName.review,
    StageName.tts,
    StageName.package,
]


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(Text)
    voice_id: Mapped[str] = mapped_column(String(64))
    chosen_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"), default=RunStatus.pending, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Rolling cost accounting, incremented as stages complete.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tts_characters: Mapped[int] = mapped_column(Integer, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    stages: Mapped[list["Stage"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Stage.position"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Stage(Base):
    __tablename__ = "stages"
    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_stage_run_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[StageName] = mapped_column(Enum(StageName, name="stage_name"))
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[StageStatus] = mapped_column(
        Enum(StageStatus, name="stage_status"), default=StageStatus.pending
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Content hash of this stage's inputs. Used to skip work we already paid
    # for when a run is retried with unchanged upstream output.
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    output: Mapped[dict | None] = mapped_column(JsonDoc, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(back_populates="stages")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # "audio" | "script" | "metadata"
    s3_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    meta: Mapped[dict | None] = mapped_column(JsonDoc, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[Run] = relationship(back_populates="artifacts")


Index("ix_runs_created_at", Run.created_at.desc())
