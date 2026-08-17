"""Pipeline orchestration.

The worker advances a run **one stage per task**. After a stage commits, the
task re-enqueues itself for the next stage. That gives three properties worth
having:

- Crash safety: a worker killed mid-run loses at most one stage. Restarting
  the task picks up at the first non-completed stage.
- Idempotency: a stage that already completed with a matching input hash is
  skipped rather than re-paid for.
- Observability: every transition is a committed row, so progress is visible
  from the database rather than from worker logs.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    STAGE_GATES,
    STAGE_ORDER,
    Artifact,
    Run,
    RunStatus,
    Stage,
    StageStatus,
)
from app.pipeline.stages import STAGE_COST_CEILINGS, STAGE_IMPLS, StageContext

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def _usd(micros: int) -> str:
    return f"${micros / 1_000_000:.2f}"


def _halt_over_budget(run: Run, reason: str) -> None:
    """Stop the run for good. Budget failures are not retryable: retrying
    spends more of exactly what ran out, so the stage is marked failed rather
    than left pending for another attempt."""
    log.warning("run=%s halted on budget: %s", run.id, reason)
    run.status = RunStatus.failed
    run.error = reason
    stage = next_pending_stage(run)
    if stage is not None:
        stage.status = StageStatus.failed
        stage.error = reason
        stage.finished_at = datetime.now(UTC)


def create_stage_rows(session: Session, run: Run) -> None:
    for position, name in enumerate(STAGE_ORDER):
        session.add(Stage(run_id=run.id, name=name, position=position, status=StageStatus.pending))


def _prior_outputs(run: Run) -> dict[str, dict]:
    return {
        stage.name.value: stage.output
        for stage in run.stages
        if stage.status is StageStatus.completed and stage.output is not None
    }


def next_pending_stage(run: Run) -> Stage | None:
    for stage in sorted(run.stages, key=lambda s: s.position):
        if stage.status is not StageStatus.completed:
            return stage
    return None


def advance(session: Session, run_id: str) -> bool:
    """Execute the next pending stage. Returns True if more work remains."""
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"Run {run_id} not found")

    if run.status in (RunStatus.completed, RunStatus.failed, RunStatus.canceled):
        return False

    # `awaiting_input` is resumable: the API flips it back to `pending` once the
    # user supplies the missing choice, so falling through here is correct.

    stage = next_pending_stage(run)
    if stage is None:
        run.status = RunStatus.completed
        session.commit()
        return False

    # Park rather than run when the next stage needs a decision we don't have.
    # This is checked every time the task fires, so re-enqueueing a parked run
    # is a no-op instead of a crash.
    gate_field = STAGE_GATES.get(stage.name)
    if gate_field and getattr(run, gate_field) is None:
        if run.status is not RunStatus.awaiting_input:
            log.info("run=%s parked before %s, needs %s", run.id, stage.name.value, gate_field)
        run.status = RunStatus.awaiting_input
        session.commit()
        return False

    budget = get_settings().run_budget_micros
    remaining = budget - run.cost_micros

    run.status = RunStatus.running
    ctx = StageContext(
        run_id=str(run.id),
        topic=run.topic,
        chosen_title=run.chosen_title,
        voice_id=run.voice_id,
        prior=_prior_outputs(run),
        target_minutes=run.target_minutes,
        budget_remaining_micros=remaining,
    )

    impl, hash_fn = STAGE_IMPLS[stage.name]
    expected_hash = hash_fn(ctx)

    # Already did this exact work on a previous attempt — don't pay again.
    if stage.output is not None and stage.input_hash == expected_hash:
        log.info("run=%s stage=%s cache hit, skipping", run.id, stage.name.value)
        stage.status = StageStatus.completed
        stage.finished_at = datetime.now(UTC)
        session.commit()
        return True

    # Refuse to start a stage the remaining budget cannot cover. Checked after
    # the cache hit above, so replaying already-paid work stays free even on a
    # run that has spent most of its allowance.
    ceiling_fn = STAGE_COST_CEILINGS.get(stage.name)
    ceiling = ceiling_fn(ctx) if ceiling_fn else 0
    if ceiling > remaining:
        _halt_over_budget(
            run,
            f"Stage {stage.name.value} could cost up to {_usd(ceiling)} but only "
            f"{_usd(remaining)} of the {_usd(budget)} run budget is left. "
            f"Raise RUN_BUDGET_USD or ask for a shorter script.",
        )
        session.commit()
        return False

    stage.status = StageStatus.running
    stage.attempt += 1
    stage.started_at = datetime.now(UTC)
    stage.input_hash = expected_hash
    stage.error = None
    session.commit()

    try:
        result = impl(ctx)
    except Exception as exc:  # noqa: BLE001 - recorded and re-raised for Celery retry
        log.exception("run=%s stage=%s failed", run.id, stage.name.value)
        stage.error = f"{type(exc).__name__}: {exc}"
        if stage.attempt >= MAX_ATTEMPTS:
            stage.status = StageStatus.failed
            stage.finished_at = datetime.now(UTC)
            run.status = RunStatus.failed
            run.error = f"stage {stage.name.value}: {stage.error}"
        else:
            stage.status = StageStatus.pending
        session.commit()
        raise

    stage.output = result.output
    stage.status = StageStatus.completed
    stage.finished_at = datetime.now(UTC)

    run.input_tokens += result.input_tokens
    run.output_tokens += result.output_tokens
    run.tts_characters += result.tts_characters
    run.cost_micros += result.cost_micros

    # Backstop. The ceiling above should make this unreachable, so if it fires
    # a ceiling under-predicted — a stale price in the table, a provider
    # reporting usage we didn't model. Keep the completed work and its cost on
    # record, then stop: the alternative is discovering the gap one stage later
    # having spent more.
    if run.cost_micros > budget:
        stage.output = result.output
        stage.status = StageStatus.completed
        stage.finished_at = datetime.now(UTC)
        _halt_over_budget(
            run,
            f"Run spent {_usd(run.cost_micros)}, over the {_usd(budget)} budget, "
            f"during stage {stage.name.value}. Stopped before the next stage.",
        )
        session.commit()
        return False

    # The titles stage deliberately does not set chosen_title — that is the
    # user's decision, and leaving it empty is what parks the run at the gate.

    for spec in result.artifacts:
        existing = session.scalar(
            select(Artifact).where(Artifact.run_id == run.id, Artifact.s3_key == spec.s3_key)
        )
        if existing is not None:
            session.delete(existing)
        session.add(
            Artifact(
                run_id=run.id,
                kind=spec.kind,
                s3_key=spec.s3_key,
                content_type=spec.content_type,
                size_bytes=spec.size_bytes,
                meta=spec.meta,
            )
        )

    session.commit()

    if next_pending_stage(run) is None:
        run.status = RunStatus.completed
        session.commit()
        return False
    return True
