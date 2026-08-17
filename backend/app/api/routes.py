import asyncio
import json
import statistics
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_token
from app.config import get_settings
from app.db import get_session
from app.models import Artifact, Run, RunStatus, Stage, StageName, StageStatus
from app.pipeline.runner import create_stage_rows
from app.providers.registry import get_tts
from app.schemas import (
    ArtifactOut,
    CreateRunRequest,
    RunOut,
    RunSummary,
    ScriptLengthOut,
    SelectTitleRequest,
    SelectVoiceRequest,
    StageOut,
    TtsRateOut,
    VoiceOut,
)
from app.storage import iter_object, object_size, presigned_url
from app.worker import advance_run

router = APIRouter(dependencies=[Depends(require_token)])

RUN_LOADERS = (selectinload(Run.stages), selectinload(Run.artifacts))

# The event stream's own polling loop: 45 minutes at 2s.
POLL_SECONDS = 2
POLL_ITERATIONS = 1350


async def _load_run(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await session.scalar(select(Run).options(*RUN_LOADERS).where(Run.id == run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.get("/settings", response_model=ScriptLengthOut)
async def script_length_settings() -> ScriptLengthOut:
    """The length bounds the UI's minutes input should honour.

    Served rather than duplicated in the frontend so the two can't drift into
    disagreeing about what the API will accept.
    """
    s = get_settings()
    return ScriptLengthOut(
        default_minutes=s.default_script_minutes,
        min_minutes=s.min_script_minutes,
        max_minutes=s.max_script_minutes,
        words_per_minute=s.words_per_minute,
    )


# Enough past runs to smooth out a warm-up outlier, few enough that the number
# still tracks the machine's current state (a driver change, a different voice).
RATE_SAMPLE_LIMIT = 10
# Scanned before filtering, since the provider a stage ran on is inside its
# JSON output rather than a column. Switching providers otherwise leaves the
# estimate stuck on "default" until the old stages scroll out of the window.
RATE_SCAN_LIMIT = 100


@router.get("/tts/rate", response_model=TtsRateOut)
async def tts_rate(session: AsyncSession = Depends(get_session)) -> TtsRateOut:
    """Synthesis speed in characters per second, measured from past runs.

    The UI turns this into a countdown. Self-hosted synthesis speed depends on
    the machine, not on us, so a configured constant would be wrong everywhere
    except where it was measured — the median of what actually happened here is
    the only honest source.
    """
    stages = (
        await session.scalars(
            select(Stage)
            .where(
                Stage.name == StageName.tts,
                Stage.status == StageStatus.completed,
                Stage.started_at.is_not(None),
                Stage.finished_at.is_not(None),
            )
            .order_by(Stage.finished_at.desc())
            .limit(RATE_SCAN_LIMIT)
        )
    ).all()

    provider = get_tts().name
    rates: list[float] = []
    for stage in stages:
        if len(rates) >= RATE_SAMPLE_LIMIT:
            break
        output = stage.output or {}
        # Only this provider's history predicts this provider's speed. The fake
        # provider in particular returns instantly, and one of its runs left in
        # the mix would promise an ETA of seconds for work that takes minutes.
        if output.get("provider") != provider:
            continue
        characters = output.get("characters") or 0
        seconds = (stage.finished_at - stage.started_at).total_seconds()
        # A stage that replayed from the content cache finished in about no
        # time and never touched the TTS server. Its "rate" is an artifact of
        # caching, and averaging it in would promise an impossible ETA.
        if characters > 0 and seconds >= 1:
            rates.append(characters / seconds)

    if not rates:
        return TtsRateOut(
            chars_per_second=get_settings().tts_chars_per_second,
            source="default",
            samples=0,
        )
    return TtsRateOut(
        chars_per_second=round(statistics.median(rates), 2),
        source="measured",
        samples=len(rates),
    )


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
    """Start a run. Generates title candidates, then parks for your pick."""
    run = Run(topic=payload.topic.strip())
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


@router.post("/runs/{run_id}/title", response_model=RunOut)
async def select_title(
    run_id: uuid.UUID,
    payload: SelectTitleRequest,
    session: AsyncSession = Depends(get_session),
) -> Run:
    """Gate 1: choose the title and length, which releases the script stage."""
    run = await _load_run(session, run_id)
    if run.chosen_title is not None:
        raise HTTPException(409, "This run already has a title")

    titles_stage = next((s for s in run.stages if s.name is StageName.titles), None)
    if titles_stage is None or titles_stage.status is not StageStatus.completed:
        raise HTTPException(409, "Title candidates are not ready yet")

    run.chosen_title = payload.title.strip()
    if payload.target_minutes is not None:
        run.target_minutes = payload.target_minutes
    run.status = RunStatus.pending
    await session.commit()

    advance_run.delay(str(run.id))
    return await _load_run(session, run_id)


@router.post("/runs/{run_id}/voice", response_model=RunOut)
async def select_voice(
    run_id: uuid.UUID,
    payload: SelectVoiceRequest,
    session: AsyncSession = Depends(get_session),
) -> Run:
    """Gate 2: choose the voice, which releases synthesis."""
    run = await _load_run(session, run_id)

    known = {v.id for v in get_tts().voices()}
    if payload.voice_id not in known:
        raise HTTPException(422, f"Unknown voice {payload.voice_id!r}. Known: {sorted(known)}")

    script_stage = next((s for s in run.stages if s.name is StageName.script), None)
    if script_stage is None or script_stage.status is not StageStatus.completed:
        raise HTTPException(409, "The script is not ready yet")

    run.voice_id = payload.voice_id
    run.status = RunStatus.pending
    await session.commit()

    advance_run.delay(str(run.id))
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
    # Presigned so the browser downloads straight from object storage. Kept for
    # the audio player, which streams and wants range requests; the download
    # goes through /download instead, where it cannot be lost to a storage blip.
    return {"url": presigned_url(artifact.s3_key), "content_type": artifact.content_type}


@router.get("/runs/{run_id}/artifacts/{artifact_id}/download")
async def artifact_download(
    run_id: uuid.UUID, artifact_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """Serve the artifact bytes through the API.

    Same origin as every other call the UI makes, so the download inherits the
    auth and CORS setup that already works rather than depending on the browser
    reaching object storage on a second port.
    """
    artifact = await session.scalar(
        select(Artifact).where(Artifact.id == artifact_id, Artifact.run_id == run_id)
    )
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")

    return StreamingResponse(
        iter_object(artifact.s3_key),
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.kind}"',
            # Set explicitly: a streamed response is chunked by default, and
            # without a length the browser cannot show download progress.
            "Content-Length": str(await asyncio.to_thread(object_size, artifact.s3_key)),
        },
    )


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
        # Must outlast the slowest stage, or the stream expires mid-run and the
        # page has nothing left telling it the run finished. Self-hosted TTS is
        # the long pole and its own timeout is 30 minutes, so this covers that
        # with headroom. The client polls as a backstop either way.
        for _ in range(POLL_ITERATIONS):
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
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
