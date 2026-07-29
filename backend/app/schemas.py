import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import RunStatus, StageName, StageStatus


class CreateRunRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=2000)
    voice_id: str = Field(default="narrator_default", max_length=64)


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
    voice_id: str
    chosen_title: str | None
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


class VoiceOut(BaseModel):
    id: str
    label: str
    provider: str
    description: str
