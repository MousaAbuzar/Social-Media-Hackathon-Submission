import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_token
from app.db import get_session
from app.models import Artifact, Run, RunStatus, StageStatus
from app.pipeline.runner import create_stage_rows
from app.providers.registry import get_tts
from app.schemas import (
    ArtifactOut,
    CreateRunRequest,
    RunOut,
    RunSummary,
    StageOut,
    VoiceOut,
)
from app.storage import presigned_url
from app.worker import advance_run

router = APIRouter(dependencies=[Depends(require_token)])

RUN_LOADERS = (selectinload(Run.stages), selectinload(Run.artifacts))


async def _load_run(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await session.scalar(select(Run).options(*RUN_LOADERS).where(Run.id == run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.get("/voices", response_model=list[VoiceOut])
async def list_voices() -> list[VoiceOut]:
    return [
        VoiceOut(id=v.id, label=v.label, provider=v.provider, description=v.description)
        for v in get_tts().voices()
    ]


@router.post("/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: CreateRunRequest, session: AsyncSession = Depends(get_session)
) -> Run:
    known = {v.id for v in get_tts().voices()}
    if payload.voice_id not in known:
        raise HTTPException(422, f"Unknown voice {payload.voice_id!r}. Known: {sorted(known)}")

    run = Run(topic=payload.topic.strip(), voice_id=payload.voice_id)
    session.add(run)
    await session.flush()
    await session.run_sync(create_stage_rows, run)
    await session.commit()

    # Return immediately; the worker owns everything that takes minutes.
    advance_run.delay(str(run.id))

    return await _load_run(session, run.id)


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(limit: int = 50, session: AsyncSession = Depends(get_session)):
    result = await session.scalars(
        select(Run).order_by(Run.created_at.desc()).limit(min(limit, 200))
    )
    return list(result)


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Run:
    return await _load_run(session, run_id)


@router.post("/runs/{run_id}/retry", response_model=RunOut)
async def retry_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Run:
    run = await _load_run(session, run_id)
    if run.status is not RunStatus.failed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed runs can be retried")

    # Completed stages keep their output, so the retry resumes rather than
    # restarting — and the input-hash check stops us re-paying for them.
    for stage in run.stages:
        if stage.status is StageStatus.failed:
            stage.status = StageStatus.pending
            stage.attempt = 0
            stage.error = None

    run.status = RunStatus.pending
    run.error = None
    await session.commit()

    advance_run.delay(str(run.id))
    return await _load_run(session, run_id)


@router.get("/runs/{run_id}/artifacts/{artifact_id}/url")
async def artifact_url(
    run_id: uuid.UUID, artifact_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    artifact = await session.scalar(
        select(Artifact).where(Artifact.id == artifact_id, Artifact.run_id == run_id)
    )
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    # Presigned so the browser downloads straight from object storage.
    return {"url": presigned_url(artifact.s3_key), "content_type": artifact.content_type}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: uuid.UUID) -> StreamingResponse:
    """Server-sent events: one frame per poll, closed when the run settles.

    Polling the database beats a pub/sub channel here — the database is
    already the source of truth, so the stream can never disagree with a
    subsequent GET.
    """

    async def stream():
        from app.db import AsyncSessionLocal

        last_payload = None
        for _ in range(600):  # ~20 minutes at 2s
            async with AsyncSessionLocal() as session:
                run = await session.scalar(
                    select(Run).options(*RUN_LOADERS).where(Run.id == run_id)
                )
                if run is None:
                    yield 'event: error\ndata: {"detail":"not found"}\n\n'
                    return

                payload = json.dumps(
                    {
                        "status": run.status.value,
                        "chosen_title": run.chosen_title,
                        "cost_micros": run.cost_micros,
                        "stages": [
                            StageOut.model_validate(s).model_dump(mode="json")
                            for s in sorted(run.stages, key=lambda s: s.position)
                        ],
                        "artifacts": [
                            ArtifactOut.model_validate(a).model_dump(mode="json")
                            for a in run.artifacts
                        ],
                    }
                )
                terminal = run.status in (
                    RunStatus.completed,
                    RunStatus.failed,
                    RunStatus.canceled,
                )

            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if terminal:
                return
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
