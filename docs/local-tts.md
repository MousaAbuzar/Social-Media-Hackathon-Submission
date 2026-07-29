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

## Start the server

```bash
git clone https://github.com/devnen/Chatterbox-TTS-Server.git
cd Chatterbox-TTS-Server
docker compose -f docker-compose-cu128.yml up -d
```

First start downloads the model (a few GB) and takes a while. When it's up,
http://localhost:8004 serves a web UI you can test in directly.

**Native alternative** (no Docker GPU setup needed):

```bash
git clone https://github.com/devnen/Chatterbox-TTS-Server.git
cd Chatterbox-TTS-Server
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python server.py
```

## Add your voice

1. Record or find a **15–30 second** clip: one speaker, no music, no
   background noise, `.wav` or `.mp3`. Quality of this clip decides quality of
   everything after it — more audio does not help, cleaner audio does.
2. Drop it in the server's `reference_audio/` folder, e.g.
   `reference_audio/my_narrator.wav`.
3. In this repo, copy the example voice list and point it at that file:

   ```bash
   cp backend/app/providers/voices.local.example.json \
      backend/app/providers/voices.local.json
   ```

   Edit it so `external_id` is your filename:

   ```json
   [
     {
       "id": "narrator_cloned",
       "label": "My Narrator",
       "mode": "clone",
       "external_id": "my_narrator.wav",
       "description": "Documentary read."
     }
   ]
   ```

   `id` is what ScriptCast stores in the database and shows in the UI, so keep
   it stable. `external_id` is just the filename on the server — change the
   sample, keep the id, and old runs still make sense.

   To use a voice that ships with the server instead of your own, set
   `"mode": "predefined"` and use a filename from `GET /get_predefined_voices`.

## Point ScriptCast at it

In `.env`:

```
TTS_PROVIDER=local
TTS_LOCAL_URL=http://host.docker.internal:8004
```

Use `http://localhost:8004` instead if you run the backend directly on Windows
rather than in Compose.

```bash
docker compose up -d --force-recreate api worker
```

The new voice shows up in the UI's voice list. Cost per run reads `$0.00`,
which is now literally true.

## Tuning

Optional knobs in `.env`, applied per request:

| Variable | Default | Effect |
|---|---|---|
| `TTS_LOCAL_EXAGGERATION` | 0.5 | Higher = more theatrical delivery |
| `TTS_LOCAL_TEMPERATURE` | 0.8 | Lower = steadier, more repeatable |
| `TTS_LOCAL_CFG_WEIGHT` | 0.5 | Higher = closer to the reference clip |
| `TTS_LOCAL_CHUNK_SIZE` | 300 | Characters per chunk before stitching |

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
