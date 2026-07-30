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


def test_create_run_takes_a_topic_only(client):
    response = client.post("/api/runs", headers=AUTH, json={"topic": "How do neutron stars form?"})
    assert response.status_code == 201

    body = response.json()
    assert body["status"] == "pending"
    # Both decisions are still outstanding at creation time.
    assert body["chosen_title"] is None
    assert body["voice_id"] is None
    # Stage rows exist up front, so the UI can render the pipeline immediately.
    assert [s["name"] for s in body["stages"]] == [
        "titles",
        "script",
        "review",
        "tts",
        "package",
    ]
    assert client.enqueued == [body["id"]]


def new_run(client, topic="A perfectly fine topic") -> str:
    return client.post("/api/runs", headers=AUTH, json={"topic": topic}).json()["id"]


def test_choosing_a_title_before_candidates_exist_is_rejected(client):
    run_id = new_run(client)
    # The worker is stubbed here, so the titles stage never ran.
    response = client.post(f"/api/runs/{run_id}/title", headers=AUTH, json={"title": "Some Title"})
    assert response.status_code == 409
    assert "not ready" in response.json()["detail"].lower()


def test_choosing_a_voice_before_the_script_exists_is_rejected(client):
    run_id = new_run(client)
    response = client.post(
        f"/api/runs/{run_id}/voice", headers=AUTH, json={"voice_id": "narrator_default"}
    )
    assert response.status_code == 409


def test_unknown_voice_is_rejected(client):
    run_id = new_run(client)
    response = client.post(
        f"/api/runs/{run_id}/voice", headers=AUTH, json={"voice_id": "not-a-voice"}
    )
    # Validated before the readiness check, so a typo is always a 422.
    assert response.status_code == 422


def test_script_length_settings_are_served(client):
    body = client.get("/api/settings", headers=AUTH).json()
    assert body["min_minutes"] <= body["default_minutes"] <= body["max_minutes"]
    assert body["words_per_minute"] > 0


@pytest.mark.parametrize("minutes", [0, 121, -5])
def test_an_out_of_range_length_is_rejected(client, minutes):
    run_id = new_run(client)
    response = client.post(
        f"/api/runs/{run_id}/title",
        headers=AUTH,
        json={"title": "Some Title", "target_minutes": minutes},
    )
    # Rejected on validation, before the "candidates not ready" check.
    assert response.status_code == 422


def test_missing_run_is_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/runs/{missing}", headers=AUTH).status_code == 404


def test_retry_rejects_a_run_that_has_not_failed(client):
    run_id = new_run(client, "Why is the sky dark at night?")
    assert client.post(f"/api/runs/{run_id}/retry", headers=AUTH).status_code == 409
