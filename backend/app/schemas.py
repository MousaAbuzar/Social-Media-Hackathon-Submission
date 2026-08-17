import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import RunStatus, StageName, StageStatus


class CreateRunRequest(BaseModel):
    """A run starts with a topic and nothing else.

    The title and voice arrive later, as separate decisions, so the pipeline
    stops for review instead of running end to end on one click.
    """

    topic: str = Field(min_length=3, max_length=2000)


class SelectTitleRequest(BaseModel):
    # Free text rather than an index: the user may edit a suggestion, and
    # tying the choice to a list position would break if titles regenerate.
    title: str = Field(min_length=3, max_length=300)
    # How many minutes the narration should run. Optional so an older client
    # still works; the script stage falls back to the configured default.
    target_minutes: int | None = Field(default=None, ge=1, le=120)


class SelectVoiceRequest(BaseModel):
    voice_id: str = Field(min_length=1, max_length=64)


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: StageName
    position: int
    status: StageStatus
    attempt: int
    error: str | None
    output: dict | None
    started_at: datetime | None
    finished_at: datetime | None


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    content_type: str
    size_bytes: int
    meta: dict | None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic: str
    voice_id: str | None
    chosen_title: str | None
    target_minutes: int | None
    status: RunStatus
    error: str | None
    input_tokens: int
    output_tokens: int
    tts_characters: int
    cost_micros: int
    created_at: datetime
    updated_at: datetime
    stages: list[StageOut] = []
    artifacts: list[ArtifactOut] = []

    @property
    def cost_usd(self) -> float:
        return self.cost_micros / 1_000_000


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic: str
    chosen_title: str | None
    status: RunStatus
    cost_micros: int
    created_at: datetime


class ScriptLengthOut(BaseModel):
    default_minutes: int
    min_minutes: int
    max_minutes: int
    words_per_minute: int


class TtsRateOut(BaseModel):
    """How fast synthesis runs here, so the UI can show a countdown."""

    chars_per_second: float
    # "measured" once this machine has finished a synthesis, "default" before.
    # Surfaced so the UI can hedge the first estimate rather than state it flatly.
    source: str
    samples: int


class VoiceOut(BaseModel):
    id: str
    label: str
    provider: str
    description: str
