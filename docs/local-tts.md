# Self-hosted TTS

Runs the narration on your own machine instead of a paid API. No key, no
per-run charge. You give it an audio sample of a voice; it reads your script
in that voice.

The engine is [Chatterbox](https://github.com/devnen/Chatterbox-TTS-Server).
It runs as its own stack, separate from ScriptCast's Compose file, because it
needs a CUDA image that has nothing to do with this app.

## Requirements

- An NVIDIA GPU with ~6 GB VRAM. CPU works but is several times slower.
- Docker Desktop with WSL2 + the NVIDIA container toolkit, so containers can
  see the GPU. `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04
  nvidia-smi` should print your card. If that fails, use the native install
  below instead — it's less fiddly on Windows than fixing GPU passthrough.

## Install

```bash
git clone https://github.com/devnen/Chatterbox-TTS-Server.git
powershell -ExecutionPolicy Bypass -File scripts/setup-local-tts.ps1
```

The script installs natively rather than via Docker: it needs ~8 GB instead of
~20 GB and skips GPU passthrough entirely. It moves the clone to
`C:\dev\Chatterbox-TTS-Server` (off OneDrive, which would try to sync the
model cache), frees disk, builds a venv, installs the `cu128` wheels, and
reports whether the GPU was found. Re-running it is safe.

Three of its steps exist only because upstream's dependencies fight each
other, and all three are silent until something fails much later:

- The engine package, `chatterbox-tts`, is deliberately absent from the
  requirements file — its metadata pins an older torch and a normal install
  quietly downgrades the CUDA build. It goes in separately with `--no-deps`.
- `--no-deps` then leaves protobuf at 3.19, because `descript-audiotools`
  caps it below 3.20 while onnx needs 3.20.2+. The server imports fine right
  up until onnx raises `cannot import name 'builder'`. The script pins 3.20.3;
  the pin only affects a TensorBoard logger that inference never calls, and
  pip's conflict warning about it is expected.
- The `cu128` requirements are labelled for Blackwell (RTX 50xx), and
  upstream points 20/30/40-series cards at `requirements-nvidia.txt`. cu128
  covers those architectures too and is what this was tested on (RTX 4050).
  Swap files if the engine misbehaves at runtime.

Then start the server:

```bash
cd C:\dev\Chatterbox-TTS-Server
.\.venv\Scripts\python.exe server.py
```

First start downloads the model (a few GB). Once up, http://localhost:8004
serves a web UI you can test in directly.

**Docker instead**, if you'd rather and have ~25 GB free plus the NVIDIA
container toolkit working:

```bash
docker compose -f docker-compose-cu128.yml up -d
```

## Voices

`backend/app/providers/voices.local.json` is the list ScriptCast shows in its
UI. It ships pre-filled with three of the 28 voices the server bundles, plus
one entry exercising the cloning path — so you can run a real narration before
recording anything.

`id` is what ScriptCast stores in the database, so keep it stable.
`external_id` is just a filename on the server: swap the sample, keep the id,
and past runs still make sense. `mode` picks which folder it reads from —
`predefined` for `voices/`, `clone` for `reference_audio/`.

### Using your own voice

1. Record a **15–30 second** clip: one speaker, no music, no background noise,
   `.wav` or `.mp3`. This clip decides the quality of everything downstream —
   more audio does not help, cleaner audio does.
2. Drop it in `C:\dev\Chatterbox-TTS-Server\reference_audio\`.
3. Point the `narrator_cloned` entry's `external_id` at your filename.

The full list of bundled voices is at `GET /get_predefined_voices`.

## Point ScriptCast at it

In `.env`:

```
TTS_PROVIDER=local
TTS_LOCAL_URL=http://localhost:8004
```

Use `http://host.docker.internal:8004` instead if you run api/worker inside
Compose. The rule of thumb: match whatever `DATABASE_URL` and
`S3_ENDPOINT_URL` already use, since they face the same choice.

`env_file` in `config.py` is a relative path, so the backend only sees `.env`
when started from the repo root. Started from `backend/`, it silently falls
back to the defaults in `config.py` — which means `TTS_PROVIDER=fake` and
silent WAVs, with nothing in the log to say why.

```bash
docker compose up -d --force-recreate api worker
```

The new voice shows up in the UI's voice list. Cost per run reads `$0.00`,
which is now literally true.

## Voice character

Every request sends these explicitly. The server has its own editable defaults
for each, and sending ours means a change made in its web UI cannot quietly
restyle a ScriptCast run. Override per machine in `.env`.

| Variable | Default | Effect |
|---|---|---|
| `TTS_LOCAL_TEMPERATURE` | 0.6 | Lower = steadier, more repeatable |
| `TTS_LOCAL_EXAGGERATION` | 0.85 | Higher = more theatrical delivery |
| `TTS_LOCAL_CFG_WEIGHT` | 0.5 | Higher = closer to the reference clip |
| `TTS_LOCAL_SPEED_FACTOR` | 1.0 | 1.0 leaves pace untouched |
| `TTS_LOCAL_CHUNK_SIZE` | 300 | Characters per chunk before stitching |

The first two are deliberately off the server's stock 0.8/0.5: steadier, so
delivery does not drift between the chunks of a long narration, and a lot more
expressive, so it reads as documentary rather than flat.

Leave `SPEED_FACTOR` at 1.0 unless you have a reason. The server resamples the
audio after generating it, which costs quality — to change pace, change
`WORDS_PER_MINUTE`, which makes the model write for the pace instead.

## While it runs

The audio panel counts down instead of just saying "synthesizing". There is no
percent-done to report — Chatterbox returns nothing until the whole script is
done — so the bar is projected from the script's length against this machine's
measured speed, taken from the median of your last few runs (`GET
/api/tts/rate`). The first run has no history and says so; after that the
estimate is your own hardware's number. Measured on an RTX 4050: ~5.2
characters per second, so a 1200-word script lands around 20 minutes.

When the audio is ready the browser saves it automatically, named after the
title, and the panel confirms with the filename. The link below it re-downloads
if the browser blocked the automatic save.

## Notes

- A 1200-word script takes roughly as long to generate as it does to listen
  to — about 8–10 minutes on a laptop GPU. The pipeline is async and each
  stage is committed, so this is a slow stage, not a broken one.
- If the server is down, the stage fails with a message telling you so.
  Setting `TTS_PROVIDER=fake` gets you back to running offline.
- Cloning a real person's voice is fine for testing on your own machine.
  Publishing it as them is a publicity-rights problem, and no disclosure label
  fixes it — so for anything you actually upload, use your own voice or one
  you've licensed.
