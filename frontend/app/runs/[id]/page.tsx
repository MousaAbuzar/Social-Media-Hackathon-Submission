"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";

import {
  API_URL,
  APP_TOKEN,
  api,
  formatCost,
  STAGE_LABELS,
  type Artifact,
  type Run,
  type ScriptLengthSettings,
  type Stage,
  type TitleCandidate,
  type TtsRate,
  type Voice,
} from "@/lib/api";

/**
 * Consumes the run's SSE stream with fetch rather than EventSource, because
 * EventSource cannot send an Authorization header.
 */
async function streamRun(
  runId: string,
  onFrame: (data: Partial<Run>) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/api/runs/${runId}/events`, {
    headers: { Authorization: `Bearer ${APP_TOKEN}` },
    signal,
  });
  if (!response.ok || !response.body) throw new Error(`stream failed: ${response.status}`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) onFrame(JSON.parse(line.slice(6)));
    }
  }
}

// Tries at 0s, 1s, 2s, 4s. Long enough to ride out a storage restart, short
// enough that a genuinely broken download still reports quickly.
const DOWNLOAD_ATTEMPTS = 4;

/** "8:24" for a countdown, "about 14 min" for an estimate. */
function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  const mins = Math.floor(whole / 60);
  return `${mins}:${String(whole % 60).padStart(2, "0")}`;
}

/** Filesystem-safe stem for the saved file, so downloads are identifiable
 *  later without opening them. */
function downloadName(title: string, contentType: string): string {
  const stem =
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 60) || "scriptcast-audio";
  return `${stem}.${contentType.includes("wav") ? "wav" : "mp3"}`;
}

/**
 * Synthesis progress with a live countdown.
 *
 * The server cannot report percent-done — Chatterbox streams nothing back
 * until the whole script is synthesized — so the bar is projected from this
 * machine's measured characters-per-second against the script's length. It is
 * an estimate and says so; the point is telling a 20-minute wait apart from a
 * hung one.
 */
function SynthesisProgress({
  startedAt,
  characters,
}: {
  startedAt: string | null;
  characters: number;
}) {
  const [rate, setRate] = useState<TtsRate | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    api
      .ttsRate()
      .then(setRate)
      .catch(() => setRate(null));
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (!rate || !startedAt || characters <= 0) {
    return (
      <p className="hint">
        Synthesizing… this can take several minutes for a full-length script.
      </p>
    );
  }

  const total = characters / rate.chars_per_second;
  const elapsed = (now - new Date(startedAt).getTime()) / 1000;
  const remaining = total - elapsed;
  // Never let the bar sit at 100% while the work continues — that reads as
  // stuck. It creeps the last stretch instead and the text carries the truth.
  const percent = Math.min(99, Math.max(0, (elapsed / total) * 100));

  return (
    <>
      <div
        className="progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
      >
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <p className="hint">
        {remaining > 0 ? (
          <>
            <strong>About {formatDuration(remaining)} left</strong> — {formatDuration(elapsed)}{" "}
            elapsed of an estimated {formatDuration(total)}.
          </>
        ) : (
          <>
            <strong>Any moment now</strong> — running longer than the {formatDuration(total)}{" "}
            estimate ({formatDuration(elapsed)} elapsed).
          </>
        )}{" "}
        {rate.source === "measured"
          ? `Estimated from your last ${rate.samples} ${
              rate.samples === 1 ? "run" : "runs"
            } at ${rate.chars_per_second} characters/sec.`
          : "First run on this machine, so this is a rough guess — the next estimate uses your real speed."}
      </p>
    </>
  );
}

/**
 * Plays the finished audio and saves it to disk without being asked.
 *
 * The download fires automatically because the user's ask ends at having the
 * file, not at being offered it — and after a wait this long, coming back to a
 * page that still needs one more click is the annoying case. The link stays
 * for a second copy or if the browser blocks the automatic save.
 */
function AudioPlayer({
  runId,
  artifact,
  title,
}: {
  runId: string;
  artifact: Artifact;
  title: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Bumped to ask for another attempt after a failure.
  const [attempt, setAttempt] = useState(0);
  // Guards against a re-render saving the same file twice. Cleared on failure,
  // so a retry is allowed but a success is still final.
  const savedFor = useRef<string | null>(null);
  const filename = downloadName(title, artifact.content_type);

  useEffect(() => {
    api
      .artifactUrl(runId, artifact.id)
      .then((r) => setUrl(r.url))
      .catch(() => setUrl(null));
  }, [runId, artifact.id]);

  useEffect(() => {
    if (savedFor.current === artifact.id) return;
    savedFor.current = artifact.id;

    let objectUrl: string | null = null;
    let canceled = false;

    (async () => {
      // Retried because a lost download costs the user the twenty minutes they
      // just spent waiting, while a retry costs a second. Going through the API
      // removed the flaky hop that made this necessary; the retry stays because
      // the cost asymmetry has not changed.
      let lastError: unknown = null;
      for (let tries = 0; tries < DOWNLOAD_ATTEMPTS; tries++) {
        if (tries > 0) await new Promise((r) => setTimeout(r, 1000 * 2 ** (tries - 1)));
        if (canceled) return;
        try {
          objectUrl = URL.createObjectURL(await api.artifactBlob(runId, artifact.id));
          if (canceled) return;
          const link = document.createElement("a");
          link.href = objectUrl;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          link.remove();
          setSaved(filename);
          setSaveError(null);
          return;
        } catch (e) {
          lastError = e;
        }
      }
      // Out of attempts. Release the guard so the retry button can try again.
      savedFor.current = null;
      setSaveError(lastError instanceof Error ? lastError.message : String(lastError));
    })().finally(() => {
      // Revoked late: revoking before the browser has read the blob cancels
      // the save in Chrome.
      if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl!), 60_000);
    });

    return () => {
      canceled = true;
    };
  }, [runId, artifact.id, filename, attempt]);

  return (
    <>
      {/* The player still streams from storage directly, where range requests
          let it seek without pulling the whole file. The download does not. */}
      {url ? <audio controls src={url} /> : <p className="hint">Loading the player…</p>}
      {saved && <p className="done">✓ Done — downloaded {saved}. Check your Downloads folder.</p>}
      {saveError && (
        <>
          <p className="err">Automatic download failed: {saveError}.</p>
          <button
            className="ghost"
            type="button"
            onClick={() => {
              setSaveError(null);
              setAttempt((n) => n + 1);
            }}
          >
            Try the download again
          </button>
        </>
      )}
      {!saved && !saveError && <p className="hint">Downloading the audio…</p>}
      {/* Storage's own link, as a last resort if even the API path fails. */}
      {url && (
        <p className="hint">
          <a href={url} download={filename}>
            {saved ? "Download again" : "Or save it manually"} (
            {Math.round(artifact.size_bytes / 1024)} KB)
          </a>
        </p>
      )}
    </>
  );
}

/** Step 2: pick a title and a length, which together release the script stage. */
function TitleChooser({
  candidates,
  recommended,
  recommendedWhy,
  onChoose,
  busy,
}: {
  candidates: TitleCandidate[];
  recommended: string | null;
  recommendedWhy: string;
  onChoose: (title: string, minutes: number) => void;
  busy: boolean;
}) {
  // Default to the recommendation. It is the model's considered pick, and a
  // user who disagrees is one click from overriding it.
  const [selected, setSelected] = useState<string | null>(recommended);
  const [custom, setCustom] = useState("");
  const [limits, setLimits] = useState<ScriptLengthSettings | null>(null);
  const [minutes, setMinutes] = useState("");

  // Bounds come from the API so this input cannot ask for a length the
  // backend will reject.
  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setLimits(s);
        setMinutes((current) => current || String(s.default_minutes));
      })
      .catch(() => setMinutes((current) => current || "8"));
  }, []);

  const chosen = custom.trim() || selected;
  const parsed = Number(minutes);
  const min = limits?.min_minutes ?? 1;
  const max = limits?.max_minutes ?? 120;
  const wpm = limits?.words_per_minute ?? 150;
  const validMinutes = Number.isInteger(parsed) && parsed >= min && parsed <= max;

  return (
    <div className="panel step-active">
      <label>Step 2 — Choose a title and a length</label>
      <p className="hint">
        Nothing is written yet. Hover a title to see why it works — hover the best
        pick to see why it beats the rest. Pick the one the script should deliver
        on.
      </p>

      <ol className="titles">
        {candidates.map(({ title, why }) => {
          const isRecommended = title === recommended;
          // The comparison only exists for the recommended title, and it is
          // the more useful of the two, so it leads the tooltip.
          const comparison = isRecommended ? recommendedWhy : "";
          return (
            <li key={title} className="title-item">
              <button
                type="button"
                className={`title-option${
                  selected === title && !custom.trim() ? " selected" : ""
                }`}
                onClick={() => {
                  setSelected(title);
                  setCustom("");
                }}
              >
                <span className="title-text">{title}</span>
                {isRecommended && <span className="pick">Best pick</span>}
                <span className="title-len">{title.length} chars</span>
              </button>
              {(why || comparison) && (
                // Rendered always and revealed on hover/focus by CSS, so it
                // costs no state and works for keyboard users too.
                <div className="title-why" role="tooltip">
                  {comparison ? (
                    <p>
                      <strong>Why this one over the others. </strong>
                      {comparison}
                    </p>
                  ) : (
                    isRecommended && (
                      <p>
                        <strong>Recommended.</strong>
                      </p>
                    )
                  )}
                  {why && <p className="title-why-own">{why}</p>}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <label htmlFor="custom">Or write your own</label>
      <input
        id="custom"
        value={custom}
        placeholder="Your own title"
        onChange={(e) => setCustom(e.target.value)}
      />

      <label htmlFor="minutes">How long should the narration be?</label>
      <div className="minutes-row">
        <input
          id="minutes"
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          value={minutes}
          onChange={(e) => setMinutes(e.target.value)}
        />
        <span className="minutes-unit">minutes</span>
      </div>
      <p className="hint">
        {validMinutes
          ? `About ${(parsed * wpm).toLocaleString()} words, at ${wpm} words per minute read aloud.`
          : `Enter a whole number between ${min} and ${max}.`}
      </p>

      <button
        type="button"
        disabled={busy || !chosen || !validMinutes}
        onClick={() => chosen && validMinutes && onChoose(chosen, parsed)}
      >
        {busy
          ? "Starting script…"
          : validMinutes
            ? `Write ${article(parsed)} ${parsed}-minute script`
            : "Write the script"}
      </button>
    </div>
  );
}

/** Step 3: pick a voice and commit to synthesis. */
function VoiceChooser({
  onChoose,
  busy,
}: {
  onChoose: (voiceId: string) => void;
  busy: boolean;
}) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .voices()
      .then((v) => {
        setVoices(v);
        setVoiceId((current) => current || v[0]?.id || "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="panel step-active">
      <label>Step 3 — Choose a voice</label>
      <p className="hint">
        The script is ready. Synthesis is the slow part, so it waits for you.
      </p>

      {/* A list rather than a <select>: collapsed, the dropdown showed only the
          first narrator, so the cloned voices read as if they weren't there. */}
      <ul className="voices">
        {voices.map((v) => (
          <li key={v.id}>
            <button
              type="button"
              className={`voice-option${v.id === voiceId ? " selected" : ""}`}
              aria-pressed={v.id === voiceId}
              onClick={() => setVoiceId(v.id)}
            >
              <span className="voice-label">{v.label}</span>
              {v.description && <span className="voice-desc">{v.description}</span>}
            </button>
          </li>
        ))}
      </ul>

      {voices.length === 0 && !error && <p className="hint">Loading voices…</p>}
      {error && <p className="err">{error}</p>}

      <button type="button" disabled={busy || !voiceId} onClick={() => onChoose(voiceId)}>
        {busy ? "Starting synthesis…" : "Generate the audio"}
      </button>
    </div>
  );
}

/** "an 8-minute script", "a 25-minute script" — chosen by how the number is
 *  said aloud. Only 8, 11 and 18 take "an" in the 1–120 range this allows. */
function article(n: number): string {
  return n === 8 || n === 11 || n === 18 ? "an" : "a";
}

function stageOf(run: Run, name: Stage["name"]): Stage | undefined {
  return run.stages.find((s) => s.name === name);
}

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // id -> label, so the audio panels can name the narrator the way the picker
  // did rather than echoing an internal id back at the user.
  const [voiceLabels, setVoiceLabels] = useState<Record<string, string>>({});
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    api
      .voices()
      .then((vs) => setVoiceLabels(Object.fromEntries(vs.map((v) => [v.id, v.label]))))
      .catch(() => {
        /* labels are cosmetic; the raw id is a fine fallback */
      });
  }, []);

  const refresh = useCallback(async () => {
    try {
      setRun(await api.getRun(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /**
   * Backstop poll while the run is unfinished.
   *
   * The SSE stream is the fast path, but it is not a guarantee: it closes on
   * its own iteration cap, on a dev-server reload, on a sleeping laptop, on any
   * dropped connection. Without this, a stream that dies mid-synthesis leaves
   * the page insisting the run is still going forever — the stream is the only
   * thing that would have moved `status`, and the effect below only re-runs
   * when `status` changes. Cheap enough to be unconditional, at a slow enough
   * interval that the stream stays the thing you actually see updating.
   */
  useEffect(() => {
    if (!run) return;
    if (run.status === "completed" || run.status === "failed") return;

    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [run?.status, refresh]);

  // Re-open the stream whenever the run leaves a parked state, so progress on
  // the newly released stages arrives live.
  const streamKey = `${run?.chosen_title ?? ""}|${run?.voice_id ?? ""}|${run?.status ?? ""}`;

  useEffect(() => {
    if (!run) return;
    if (run.status === "completed" || run.status === "failed") return;

    const controller = new AbortController();
    abort.current = controller;

    streamRun(
      id,
      (frame) => setRun((prev) => (prev ? { ...prev, ...frame } : prev)),
      controller.signal,
    )
      .then(refresh)
      .catch(() => {
        /* stream ended or aborted; the REST view stays authoritative */
      });

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, streamKey]);

  const act = async (fn: () => Promise<Run>) => {
    setBusy(true);
    setError(null);
    try {
      setRun(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (error && !run) return <p className="err">{error}</p>;
  if (!run) return <p className="hint">Loading…</p>;

  const titlesStage = stageOf(run, "titles");
  const scriptStage = stageOf(run, "script");
  // Runs created before titles carried rationales only have the flat string
  // list, so fall back to it with an empty rationale rather than showing none.
  const titles = (titlesStage?.output?.titles as string[] | undefined) ?? [];
  const candidates =
    (titlesStage?.output?.candidates as TitleCandidate[] | undefined) ??
    titles.map((title) => ({ title, why: "" }));
  const recommended = (titlesStage?.output?.recommended as string | null | undefined) ?? null;
  // Absent on runs generated before the comparison existed; the tooltip falls
  // back to the pick's own rationale.
  const recommendedWhy = (titlesStage?.output?.recommended_why as string | undefined) ?? "";
  const script = scriptStage?.output?.script as string | undefined;
  const estimatedMinutes = scriptStage?.output?.estimated_minutes as number | undefined;
  // Absent on runs written before the script stage could search, and 0 when
  // search is turned off — both mean "no live sources", so both stay hidden.
  const webSearches = (scriptStage?.output?.web_searches as number | undefined) ?? 0;
  const review = stageOf(run, "review")?.output as
    | { passed: boolean; findings: string }
    | undefined;
  const audio = run.artifacts.find((a) => a.kind === "audio");
  const ttsStage = stageOf(run, "tts");
  // The human label if the voice list has loaded, the raw id otherwise — the
  // panel heading should never be blank while synthesis is the visible step.
  const voiceLabel = voiceLabels[run.voice_id ?? ""] ?? run.voice_id ?? "your voice";

  const needsTitle = run.chosen_title === null;
  const needsVoice = run.chosen_title !== null && run.voice_id === null;

  return (
    <>
      <div className="panel">
        <div className="row">
          <strong>{run.chosen_title ?? run.topic}</strong>
          <span className={`badge ${run.status}`}>
            {run.status === "awaiting_input" ? "needs you" : run.status}
          </span>
        </div>
        {run.chosen_title && <p className="hint topic-line">Topic: {run.topic}</p>}
        <div className="meta" style={{ marginTop: 12 }}>
          <span>Cost {formatCost(run.cost_micros)}</span>
          <span>
            {run.input_tokens.toLocaleString()} in / {run.output_tokens.toLocaleString()} out tokens
          </span>
          {run.tts_characters > 0 && <span>{run.tts_characters.toLocaleString()} TTS chars</span>}
          {run.voice_id && <span>Voice {voiceLabel}</span>}
        </div>
        {run.error && <p className="err">{run.error}</p>}
        {error && <p className="err">{error}</p>}
        {run.status === "failed" && (
          <button
            className="ghost"
            style={{ marginTop: 14 }}
            onClick={() => act(() => api.retryRun(run.id))}
          >
            Retry from the failed stage
          </button>
        )}
      </div>

      {/* Step 2 gate */}
      {needsTitle && candidates.length > 0 && (
        <TitleChooser
          candidates={candidates}
          recommended={recommended}
          recommendedWhy={recommendedWhy}
          busy={busy}
          onChoose={(title, minutes) => act(() => api.selectTitle(run.id, title, minutes))}
        />
      )}
      {needsTitle && candidates.length === 0 && run.status !== "failed" && (
        <div className="panel">
          <label>Step 2 — Choose a title and a length</label>
          <p className="hint">Writing title options…</p>
        </div>
      )}

      {/* Script, shown as soon as it exists */}
      {script && (
        <div className="panel">
          <label>
            Script — {(scriptStage?.output?.word_count as number).toLocaleString()} words
            {estimatedMinutes !== undefined && ` · about ${estimatedMinutes} min read aloud`}
            {run.target_minutes !== null && ` · asked for ${run.target_minutes} min`}
            {webSearches > 0 &&
              ` · researched with ${webSearches} web ${webSearches === 1 ? "search" : "searches"}`}
          </label>
          <pre>{script}</pre>
        </div>
      )}
      {run.chosen_title && !script && run.status !== "failed" && (
        <div className="panel">
          <label>Script</label>
          <p className="hint">
            Writing
            {run.target_minutes !== null
              ? ` ${article(run.target_minutes)} ${run.target_minutes}-minute script`
              : " the script"}
            … longer scripts take longer.
          </p>
        </div>
      )}

      {review && !review.passed && (
        <div className="panel">
          <label>Review findings</label>
          <pre>{review.findings}</pre>
        </div>
      )}

      {/* Step 3 gate */}
      {needsVoice && script && (
        <VoiceChooser
          busy={busy}
          onChoose={(voiceId) => act(() => api.selectVoice(run.id, voiceId))}
        />
      )}

      {audio && (
        <div className="panel">
          <label>Audio — narrated by {voiceLabel}</label>
          <AudioPlayer
            runId={run.id}
            artifact={audio}
            title={run.chosen_title ?? run.topic}
          />
        </div>
      )}
      {run.voice_id && !audio && run.status !== "failed" && (
        <div className="panel step-active">
          <label>Audio — narrating in {voiceLabel}</label>
          <SynthesisProgress
            startedAt={ttsStage?.started_at ?? null}
            characters={script?.length ?? 0}
          />
        </div>
      )}

      <div className="panel">
        <label>Pipeline</label>
        {run.stages.map((stage) => (
          <div className="row" key={stage.name}>
            <span>{STAGE_LABELS[stage.name]}</span>
            <span className="meta">
              {stage.attempt > 1 && <span>attempt {stage.attempt}</span>}
              <span className={`badge ${stage.status}`}>{stage.status}</span>
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
