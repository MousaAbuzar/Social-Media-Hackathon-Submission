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

function AudioPlayer({ runId, artifact }: { runId: string; artifact: Artifact }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    api
      .artifactUrl(runId, artifact.id)
      .then((r) => setUrl(r.url))
      .catch(() => setUrl(null));
  }, [runId, artifact.id]);

  if (!url) return <p className="hint">Preparing download link…</p>;
  return (
    <>
      <audio controls src={url} />
      <p className="hint">
        <a href={url} download>
          Download audio ({Math.round(artifact.size_bytes / 1024)} KB)
        </a>
      </p>
    </>
  );
}

/** Step 2: pick a title and a length, which together release the script stage. */
function TitleChooser({
  candidates,
  recommended,
  onChoose,
  busy,
}: {
  candidates: TitleCandidate[];
  recommended: string | null;
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
        Nothing is written yet. Hover a title to see why it works. Pick the one the
        script should deliver on.
      </p>

      <ol className="titles">
        {candidates.map(({ title, why }) => {
          const isRecommended = title === recommended;
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
              {why && (
                // Rendered always and revealed on hover/focus by CSS, so it
                // costs no state and works for keyboard users too.
                <div className="title-why" role="tooltip">
                  {isRecommended && <strong>Recommended. </strong>}
                  {why}
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
      <label htmlFor="voice">Step 3 — Choose a voice</label>
      <p className="hint">
        The script is ready. Synthesis is the slow part, so it waits for you.
      </p>

      <select id="voice" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
        {voices.map((v) => (
          <option key={v.id} value={v.id}>
            {v.label}
            {v.description ? ` — ${v.description}` : ""}
          </option>
        ))}
      </select>

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
  const abort = useRef<AbortController | null>(null);

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
  const script = scriptStage?.output?.script as string | undefined;
  const estimatedMinutes = scriptStage?.output?.estimated_minutes as number | undefined;
  const review = stageOf(run, "review")?.output as
    | { passed: boolean; findings: string }
    | undefined;
  const audio = run.artifacts.find((a) => a.kind === "audio");

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
          {run.voice_id && <span>Voice {run.voice_id}</span>}
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
          <label>Audio</label>
          <AudioPlayer runId={run.id} artifact={audio} />
        </div>
      )}
      {run.voice_id && !audio && run.status !== "failed" && (
        <div className="panel">
          <label>Audio</label>
          <p className="hint">
            Synthesizing… this can take several minutes for a full-length script.
          </p>
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
