---
name: run-scriptcast
description: Launch and drive the ScriptCast stack locally (Docker Compose - api, worker, web, db, redis, minio). Use when asked to start, run, restart, or screenshot the app, or to confirm a change works in the running app.
---

# Running ScriptCast

## Start it

```powershell
.\scripts\start.ps1
```

That is the whole thing. The script is idempotent — run it even when the stack
is already up. It starts Docker Desktop if needed, skips the build when images
exist, brings up Compose, runs migrations, and blocks until the API and the UI
both return 200. It prints the URLs at the end.

After a change to `backend/pyproject.toml` or `frontend/package.json`:

```powershell
.\scripts\start.ps1 -Rebuild
```

Nothing else needs a rebuild — `./backend` and `./frontend` are bind-mounted
into the containers and the API runs under `uvicorn --reload`, so ordinary
source edits are live.

## Two things that will waste your time if you don't know them

**BuildKit cannot build this repo.** The project lives under OneDrive, so every
file is a cloud placeholder (a reparse point), and the builder fails on it:

```
#2 [api internal] load build definition from Dockerfile
#2 transferring dockerfile: 31B 0.0s done
#2 ERROR: invalid file request Dockerfile
target api: failed to solve: failed to read dockerfile: invalid file request Dockerfile
```

`attrib +P -U /s` does not clear it. The legacy builder reads the files fine, so
the script exports `DOCKER_BUILDKIT=0` and `COMPOSE_DOCKER_CLI_BUILD=0`. If you
ever run `docker compose build` by hand, set those first. The durable fix is
moving the repo out of OneDrive.

**The UI is not on port 3000.** `.env` sets `WEB_PORT=3006`. The script reads it
and prints the real URL — use that, don't assume. The API rejects browser
requests from origins missing from `CORS_ORIGINS`, so a new port needs adding
there too.

## Drive it

- UI: `http://localhost:<WEB_PORT>` (3006 today)
- API docs: `http://localhost:8000/docs`
- MinIO console: `http://localhost:9001` (`minioadmin` / `minioadmin`)

Auth is a single bearer token — `APP_TOKEN` in `.env`, baked into the web
container at startup, so there is nothing to paste into the UI.

In the browser, the home page is the whole entry point: type a topic, click
**Get title options**, then pick a title and length. The run then gates on your
picks (`needs you`) before it proceeds to script → review → tts → package.
Take a screenshot after each step and actually look at it.

Smoke test without a browser:

```bash
curl -s -H "Authorization: Bearer dev-local-token" http://localhost:8000/api/runs
```

Watch a run progress:

```powershell
docker compose logs -f worker
```

## Stop it

```powershell
docker compose stop        # keeps volumes and the warm start path
docker compose down        # also removes containers; next start is slower
```

Prefer `stop`. `down -v` additionally drops Postgres and MinIO data — past runs
disappear.
