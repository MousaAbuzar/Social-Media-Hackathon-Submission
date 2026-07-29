"""API surface tests.

Uses an in-memory async SQLite database and a stubbed task queue, so the HTTP
contract is verified without Postgres, Redis, or a worker.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_session
from app.main import app

TOKEN = "dev-local-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with maker() as session:
            yield session

    # The worker is out of scope here; record enqueues instead of running them.
    enqueued: list[str] = []
    monkeypatch.setattr(
        "app.api.routes.advance_run",
        type("Stub", (), {"delay": staticmethod(lambda run_id: enqueued.append(run_id))}),
    )

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        # TestClient's portal runs the async setup for us.
        test_client.portal.call(_create_schema, engine)
        test_client.enqueued = enqueued
        yield test_client
    app.dependency_overrides.clear()


async def _create_schema(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def test_health_needs_no_token(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_requests_without_a_token_are_rejected(client):
    assert client.get("/api/runs").status_code == 401


def test_requests_with_a_wrong_token_are_rejected(client):
    assert client.get("/api/runs", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_voices_are_listed(client):
    response = client.get("/api/voices", headers=AUTH)
    assert response.status_code == 200
    assert any(v["id"] == "narrator_default" for v in response.json())


def test_create_run_returns_pending_and_enqueues_work(client):
    response = client.post(
        "/api/runs",
        headers=AUTH,
        json={"topic": "How do neutron stars form?", "voice_id": "narrator_default"},
    )
    assert response.status_code == 201

    body = response.json()
    assert body["status"] == "pending"
    # Stage rows exist up front, so the UI can render the pipeline immediately.
    assert [s["name"] for s in body["stages"]] == [
        "titles",
        "script",
        "review",
        "tts",
        "package",
    ]
    assert client.enqueued == [body["id"]]


def test_unknown_voice_is_rejected(client):
    response = client.post(
        "/api/runs",
        headers=AUTH,
        json={"topic": "A perfectly fine topic", "voice_id": "not-a-voice"},
    )
    assert response.status_code == 422


def test_missing_run_is_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/runs/{missing}", headers=AUTH).status_code == 404


def test_retry_rejects_a_run_that_has_not_failed(client):
    run_id = client.post(
        "/api/runs",
        headers=AUTH,
        json={"topic": "Why is the sky dark at night?", "voice_id": "narrator_default"},
    ).json()["id"]

    assert client.post(f"/api/runs/{run_id}/retry", headers=AUTH).status_code == 409
