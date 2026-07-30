# ScriptCast

Topic in, narrated audio out. A durable async pipeline that turns a one-line
topic into a title, a narration script, a QA pass, synthesized audio, and an
upload-ready metadata bundle.

Built as a portfolio project. The interesting part is not the API calls — it's
the job orchestration around them.

## Pipeline

```
topic ──▶ titles ──▶ [pick title + length] ──▶ script ──▶ review
                                                            │
                            audio + metadata ◀── package ◀── tts ◀── [pick voice]
```

The titles stage returns each candidate with the case for choosing it and names
the strongest one, so the pick is informed rather than a coin flip. Length is
requested in **minutes of narration** — the unit that actually matters — and
converted to a word target at 150 wpm (`WORDS_PER_MINUTE`).

Each stage is a committed database row. The worker runs **one stage per task**
and re-enqueues itself for the next, which buys three things:

- **Crash safety.** A worker killed mid-run loses at most one stage. On restart
  the run resumes at the first non-completed stage.
- **Idempotency.** Every stage stores a SHA-256 hash of its inputs. A retry
  whose inputs are unchanged reuses the stored output instead of paying a
  vendor again.
- **Observability.** Progress lives in Postgres, not in worker logs, so the UI
  and the API can never disagree about where a run is.

## Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + Pydantic | Async request path; schemas validate LLM output at the boundary |
| Worker | Celery + Redis | Runs take minutes — they cannot live in an HTTP request |
| Database | Postgres + SQLAlchemy + Alembic | Relational run/stage/artifact model, JSONB for stage output, versioned migrations |
| Storage | S3-compatible (MinIO locally) | Audio doesn't belong in a database; presigned URLs skip the app server |
| Frontend | Next.js + TypeScript | Streams live progress over SSE |
| Infra | Docker Compose → Fly.io / AWS | One command locally, one target in prod |

## Provider abstraction

`app/providers/base.py` defines two Protocols — `LLMProvider` and
`TTSProvider`. Nothing above that layer imports a vendor SDK. Adding a vendor
is one new file plus one line in `registry.py`; the pipeline, API, and tests
are untouched.

Both have offline fakes (`llm_fake.py`, `tts_fake.py`) that produce real,
well-formed output — the TTS fake emits a playable WAV sized to the script. So
the entire pipeline, and the entire test suite, runs with **no API key, no
network, and no spend**. That is why CI can exercise every stage on every
commit.

There are three TTS providers: `fake` (offline placeholder), `local`
(self-hosted, free), and `http` (a paid hosted vendor).

### Self-hosted TTS

`TTS_PROVIDER=local` points the pipeline at a
[Chatterbox](https://github.com/devnen/Chatterbox-TTS-Server) server running on
your own machine. It clones a voice from a 15–30 second audio sample, needs no
account or API key, and reports a true zero cost per run. Setup:
[`docs/local-tts.md`](docs/local-tts.md).

This is the reason the provider abstraction earns its keep: swapping a paid
vendor for a GPU in the next room is one new file (`tts_local.py`) and one line
in `registry.py`. Nothing in the pipeline, API, or tests changed.

### Voices

Hosted voices are declared in `app/providers/voices.json` (see
`voices.example.json`); self-hosted ones in `voices.local.json` (see
`voices.local.example.json`). In both files, `id` is our stable public
identifier and `external_id` is the vendor's — so a provider swap doesn't
invalidate the voice ids recorded on past runs.

Use voices you hold the rights to — an original or licensed synthetic voice.
Cloning a real person's voice to test the pipeline locally is one thing;
publishing it as them is a publicity-rights problem no disclosure label
solves, and it would make this unusable as a public portfolio piece.

The `package` stage always writes a synthetic-narration disclosure into the
metadata bundle, which is what platforms require you to declare on upload.

## Cost tracking

Every stage reports tokens and characters consumed; the run row accumulates
them into `cost_micros` (millionths of a dollar). The UI shows cost per run,
so the economics of the pipeline are visible rather than inferred.

## Running it

```bash
cp .env.example .env          # defaults run fully offline
docker compose up --build
docker compose exec api alembic upgrade head
```

- UI: http://localhost:3000
- API docs: http://localhost:8000/docs
- MinIO console: http://localhost:9001 (`minioadmin` / `minioadmin`)

The UI reads `APP_TOKEN` from your `.env` at startup, so there is nothing to
paste in. Changing the token means restarting the `web` container.

**If port 3000 is already taken**, set `WEB_PORT` in `.env` (e.g. `WEB_PORT=3006`)
and add the matching origin to `CORS_ORIGINS` — the API rejects browser
requests from origins it doesn't list.

Two endpoints exist for object storage because they are reached from different
places: `S3_ENDPOINT_URL` is how the API talks to MinIO inside the Compose
network, while `S3_PUBLIC_ENDPOINT_URL` is what download URLs are signed
against. A presigned URL's signature covers the host, so signing with the
internal hostname produces links no browser can open.

For real narration with no spend, set `TTS_PROVIDER=local` and follow
[`docs/local-tts.md`](docs/local-tts.md). For a paid vendor instead, set
`TTS_PROVIDER=http` with `TTS_BASE_URL` / `TTS_API_KEY` and a populated
`voices.json`. Set `ANTHROPIC_API_KEY` for real scripts.

## Tests

```bash
cd backend
pip install -e ".[dev]"
pytest
```

20 tests, no services required — the ORM types are portable, so orchestration
tests run on in-memory SQLite. They cover a full run to completion, artifact
recording, the input-hash cache skip, mid-pipeline resumability, and cost
accumulation. Set `TEST_DATABASE_URL` to run the same tests against Postgres;
CI does exactly that, so the deployment engine is covered too.

## Layout

```
backend/
  app/
    api/          FastAPI routes, bearer auth
    pipeline/     stages.py (work), runner.py (orchestration), prompts.py
    providers/    Protocols, vendor adapters, offline fakes, registry
    models.py     Run / Stage / Artifact + the stage state machine
    worker.py     Celery app and the advance-one-stage task
  alembic/        migrations
  tests/
frontend/         Next.js UI with SSE progress
```

## Deliberately not built

- Multi-user auth. Single bearer token; this is a personal tool.
- Video assembly. Audio and metadata out; editing stays manual.
- An eval harness for script quality. The `review` stage is the seed of one —
  it's advisory today, and scoring its findings against a labeled set is the
  obvious next step.
