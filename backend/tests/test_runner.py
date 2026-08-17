"""Orchestration tests.

Runs against in-memory SQLite by default so the suite is green with no
services running. CI sets TEST_DATABASE_URL to a real Postgres, which is the
deployment target — same tests, both engines.
"""

import os
import uuid

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Run, RunStatus, StageName, StageStatus
from app.pipeline.runner import advance, create_stage_rows

TEST_DB = os.environ.get("TEST_DATABASE_URL", "sqlite://")


@pytest.fixture
def session():
    # A single shared connection so an in-memory SQLite database survives
    # across the sessions the test opens.
    kwargs = {"poolclass": StaticPool} if TEST_DB.startswith("sqlite") else {}
    engine = create_engine(TEST_DB, **kwargs)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as s:
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def run(session):
    """A fresh run: topic only, no title and no voice chosen yet."""
    r = Run(id=uuid.uuid4(), topic="How do neutron stars form?")
    session.add(r)
    session.flush()
    create_stage_rows(session, r)
    session.commit()
    return r


def drain(session, run_id) -> None:
    while advance(session, run_id):
        pass


def test_run_parks_after_titles_waiting_for_a_choice(session, run):
    drain(session, run.id)
    session.refresh(run)

    assert run.status is RunStatus.awaiting_input
    assert run.chosen_title is None

    by_name = {s.name: s for s in run.stages}
    assert by_name[StageName.titles].status is StageStatus.completed
    # Crucially, the expensive stage did not run.
    assert by_name[StageName.script].status is StageStatus.pending
    assert by_name[StageName.titles].output["titles"]


def test_choosing_a_title_releases_the_script_then_parks_for_a_voice(session, run):
    drain(session, run.id)
    session.refresh(run)

    run.chosen_title = "How Neutron Stars Are Born"
    run.status = RunStatus.pending
    session.commit()

    drain(session, run.id)
    session.refresh(run)

    assert run.status is RunStatus.awaiting_input
    by_name = {s.name: s for s in run.stages}
    assert by_name[StageName.script].status is StageStatus.completed
    assert by_name[StageName.review].status is StageStatus.completed
    # Synthesis is the slow, expensive step — it waits for a voice.
    assert by_name[StageName.tts].status is StageStatus.pending


def test_full_gated_workflow_completes(session, run):
    drain(session, run.id)
    session.refresh(run)

    run.chosen_title = "How Neutron Stars Are Born"
    run.status = RunStatus.pending
    session.commit()
    drain(session, run.id)
    session.refresh(run)

    run.voice_id = "narrator_default"
    run.status = RunStatus.pending
    session.commit()
    drain(session, run.id)
    session.refresh(run)

    assert run.status is RunStatus.completed
    assert all(s.status is StageStatus.completed for s in run.stages)
    assert run.chosen_title == "How Neutron Stars Are Born"


def test_advancing_a_parked_run_is_a_no_op(session, run):
    drain(session, run.id)
    session.refresh(run)
    attempts_before = {s.name: s.attempt for s in run.stages}

    # A stray retry or duplicate task must not burn an attempt or flip status.
    assert advance(session, run.id) is False
    session.refresh(run)

    assert run.status is RunStatus.awaiting_input
    assert {s.name: s.attempt for s in run.stages} == attempts_before


def complete_run(session, run):
    drain(session, run.id)
    session.refresh(run)
    run.chosen_title = "How Neutron Stars Are Born"
    run.status = RunStatus.pending
    session.commit()
    drain(session, run.id)
    session.refresh(run)
    run.voice_id = "narrator_default"
    run.status = RunStatus.pending
    session.commit()
    drain(session, run.id)
    session.refresh(run)


def test_artifacts_are_recorded(session, run):
    complete_run(session, run)
    kinds = {a.kind for a in run.artifacts}
    assert kinds == {"script", "audio", "metadata"}


def test_completed_stage_is_not_re_executed(session, run):
    # Advance one stage, then reset its status without clearing its output.
    advance(session, run.id)
    session.refresh(run)

    titles = next(s for s in run.stages if s.name is StageName.titles)
    original_output = titles.output
    original_attempt = titles.attempt
    titles.status = StageStatus.pending
    session.commit()

    advance(session, run.id)
    session.refresh(titles)

    # Matching input hash means the cache path ran: same output, no new attempt.
    assert titles.status is StageStatus.completed
    assert titles.output == original_output
    assert titles.attempt == original_attempt


def test_run_is_resumable_from_the_middle(session, run):
    drain(session, run.id)
    session.refresh(run)
    run.chosen_title = "How Neutron Stars Are Born"
    run.status = RunStatus.pending
    session.commit()

    advance(session, run.id)  # script only
    session.refresh(run)

    done = [s.name for s in run.stages if s.status is StageStatus.completed]
    assert StageName.titles in done and StageName.script in done
    assert StageName.review not in done

    # A "fresh worker" picks up exactly where the previous one stopped.
    drain(session, run.id)
    session.refresh(run)
    assert run.status is RunStatus.awaiting_input
    assert {s.name for s in run.stages if s.status is StageStatus.completed} >= {
        StageName.titles,
        StageName.script,
        StageName.review,
    }


def test_cost_accumulates_across_stages(session, run):
    complete_run(session, run)

    # Fake providers are free, but the counters must still be wired up.
    assert run.input_tokens > 0
    assert run.output_tokens > 0
    assert run.tts_characters > 0


# --- Budget cap ---------------------------------------------------------
#
# The fakes are free, so these drive the guard directly: a stage ceiling that
# exceeds what is left, or a run that has already overspent. That keeps the
# tests about the enforcement rather than about any provider's price list.


def _charge_titles(monkeypatch, micros: int) -> None:
    """Make the titles stage claim it could cost `micros`."""
    from app.pipeline.stages import STAGE_COST_CEILINGS

    monkeypatch.setitem(STAGE_COST_CEILINGS, StageName.titles, lambda ctx: micros)


def test_stage_that_cannot_be_afforded_never_runs(session, run, monkeypatch):
    from app.config import get_settings

    budget = get_settings().run_budget_micros
    _charge_titles(monkeypatch, budget + 1)

    assert advance(session, run.id) is False
    session.refresh(run)

    assert run.status is RunStatus.failed
    # The point of a preflight check: the call was never made, so nothing was
    # spent finding out it was unaffordable.
    assert run.cost_micros == 0
    titles = next(s for s in run.stages if s.name is StageName.titles)
    assert titles.status is StageStatus.failed
    assert titles.attempt == 0
    assert "run budget" in run.error


def test_budget_failure_names_the_numbers(session, run, monkeypatch):
    _charge_titles(monkeypatch, 9_990_000)

    advance(session, run.id)
    session.refresh(run)

    # A halt the user cannot act on is a halt they will just retry. The message
    # has to say what it would have cost and what was left.
    assert "$9.99" in run.error
    assert "RUN_BUDGET_USD" in run.error


def test_budget_failure_is_not_retried_into_more_spend(session, run, monkeypatch):
    _charge_titles(monkeypatch, 9_990_000)

    advance(session, run.id)
    session.refresh(run)
    assert run.status is RunStatus.failed

    # Retrying spends more of exactly what ran out, so a re-advance must stay
    # halted rather than quietly attempting the stage again.
    assert advance(session, run.id) is False
    session.refresh(run)
    assert run.status is RunStatus.failed
    assert run.cost_micros == 0


def test_a_run_already_over_budget_halts_before_the_next_stage(session, run, monkeypatch):
    from app.config import get_settings

    # Pretend the first stage overspent, the case the post-stage backstop
    # exists for, then confirm the next stage refuses to start.
    drain(session, run.id)
    session.refresh(run)
    run.cost_micros = get_settings().run_budget_micros + 1
    run.chosen_title = "How Neutron Stars Are Born"
    run.status = RunStatus.pending
    session.commit()

    _charge_titles(monkeypatch, 0)
    assert advance(session, run.id) is False
    session.refresh(run)

    assert run.status is RunStatus.failed
    script = next(s for s in run.stages if s.name is StageName.script)
    assert script.status is StageStatus.failed


def test_cached_stages_still_replay_when_the_budget_is_gone(session, run):
    """A cache hit costs nothing, so an exhausted budget must not block it.

    Otherwise a run that overspent could never be inspected or resumed — every
    advance would halt on work that was already paid for.
    """
    from app.config import get_settings

    advance(session, run.id)  # titles, for real
    session.refresh(run)

    titles = next(s for s in run.stages if s.name is StageName.titles)
    original = titles.output
    titles.status = StageStatus.pending
    run.cost_micros = get_settings().run_budget_micros
    session.commit()

    advance(session, run.id)
    session.refresh(titles)

    assert titles.status is StageStatus.completed
    assert titles.output == original
