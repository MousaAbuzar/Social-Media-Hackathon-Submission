# A real run, start to finish

Every file in this folder came out of one actual pipeline run. Nothing was
edited by hand. If you only look at one thing in this repo, make it
[`titles.json`](titles.json) — it's the clearest picture of what the pipeline
does that a script alone can't show.

**Input:** `what is a black hole` — plus a 2-minute length, typed into the UI.

**Output:** everything below, in about four minutes of wall-clock time.

| | |
|---|---|
| Run date | 17 Aug 2026 |
| Cost | **$0.57** — Claude only; narration was synthesized locally at zero cost |
| Tokens | 63,886 in / 5,089 out |
| Web searches | 4, during the script stage |
| Script | 334 words (300 requested) |
| Audio | 99 seconds, 24 kHz mono WAV, cloned voice |

## The files

| File | Stage | What it is |
|---|---|---|
| [`titles.json`](titles.json) | 1 · titles | Five candidates. Each carries the case for picking it **and its own weakness**, then a named recommendation with a comparative argument against the other four. |
| [`script.txt`](script.txt) | 2 · script | The narration, written after 4 live web searches. |
| [`review.json`](review.json) | 3 · review | The automated QA pass. |
| [`narration.wav`](narration.wav) | 4 · tts | The finished audio, self-hosted voice clone. |
| [`metadata.json`](metadata.json) | 5 · package | Upload-ready bundle: title, alternates, runtime, review findings, synthetic-narration disclosure. |

## Two things worth noticing

**The titles stage argues with itself.** It didn't just rank five options — it
named the trade-off. The recommended title wins on curiosity but, in its own
words, "captures no specific search query." So it flagged the runner-up and
said exactly when to choose it instead: *"pick it instead only if search
traffic is the priority."* That's a briefing, not a ranking.

**The review stage found a real bug — and let the run continue.** It caught
markdown asterisks around the word `*close*` in the script, which a
text-to-speech engine would read aloud or garble. So `review_passed` is
`false` in the metadata, and the run finished anyway.

That's deliberate. The review stage is advisory: it records what it found and
gets out of the way, because a run stuck on a cosmetic flag is worse than a
run that ships with a note attached. The finding is right there in
`metadata.json` for a human to act on.
