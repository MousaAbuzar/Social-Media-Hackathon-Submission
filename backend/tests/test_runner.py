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
    r = Run(id=uuid.uuid4(), topic="How do neutron stars form?", voice_id="narrator_default")
    session.add(r)
    session.flush()
    create_stage_rows(session, r)
    session.commit()
    return r


def test_run_completes_all_stages(session, run):
    while advance(session, run.id):
        pass

    session.refresh(run)
    assert run.status is RunStatus.completed
    assert all(s.status is StageStatus.completed for s in run.stages)
    assert run.chosen_title


def test_artifacts_are_recorded(session, run):
    while advance(session, run.id):
        pass

    session.refresh(run)
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
    advance(session, run.id)
    advance(session, run.id)
    session.refresh(run)

    done = [s.name for s in run.stages if s.status is StageStatus.completed]
    assert StageName.titles in done and StageName.script in done
    assert run.status is RunStatus.running

    # A "fresh worker" picks up exactly where the previous one stopped.
    while advance(session, run.id):
        pass
    session.refresh(run)
    assert run.status is RunStatus.completed


def test_cost_accumulates_across_stages(session, run):
    while advance(session, run.id):
        pass
    session.refresh(run)

    # Fake providers are free, but the counters must still be wired up.
    assert run.input_tokens > 0
    assert run.output_tokens > 0
    assert run.tts_characters > 0
