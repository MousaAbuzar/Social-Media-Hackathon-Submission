"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";

import {
  API_URL,
  api,
  formatCost,
  STAGE_LABELS,
  type Artifact,
  type Run,
} from "@/lib/api";

/**
 * Consumes the run's SSE stream with fetch rather than EventSource, because
 * EventSource cannot send an Authorization header. Falls back to nothing on
 * error — the caller reloads via the REST endpoint.
 */
async function streamRun(
  runId: string,
  token: string,
  onFrame: (data: Partial<Run>) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/api/runs/${runId}/events`, {
    headers: { Authorization: `Bearer ${token}` },
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

  if (!url) return <p className="sub">Preparing download link…</p>;
  return (
    <>
      <audio controls src={url} />
      <p className="sub">
        <a href={url} download>
          Download audio ({Math.round(artifact.size_bytes / 1024)} KB)
        </a>
      </p>
    </>
  );
}

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  useEffect(() => {
    const token = window.localStorage.getItem("scriptcast_token") ?? "";
    const controller = new AbortController();
    abort.current = controller;

    streamRun(id, token, (frame) => setRun((prev) => (prev ? { ...prev, ...frame } : prev)), controller.signal)
      .then(refresh)
      .catch(() => {
        /* stream ended or was aborted; the REST view stays authoritative */
      });

    return () => controller.abort();
  }, [id, refresh]);

  if (error) return <p className="err">{error}</p>;
  if (!run) return <p className="sub">Loading…</p>;

  const script = run.stages.find((s) => s.name === "script")?.output?.script as string | undefined;
  const review = run.stages.find((s) => s.name === "review")?.output as
    | { passed: boolean; findings: string }
    | undefined;
  const audio = run.artifacts.find((a) => a.kind === "audio");

  return (
    <>
      <div className="panel">
        <div className="row">
          <strong>{run.chosen_title ?? run.topic}</strong>
          <span className={`badge ${run.status}`}>{run.status}</span>
        </div>
        <div className="meta" style={{ marginTop: 12 }}>
          <span>Cost {formatCost(run.cost_micros)}</span>
          <span>
            {run.input_tokens.toLocaleString()} in / {run.output_tokens.toLocaleString()} out tokens
          </span>
          <span>{run.tts_characters.toLocaleString()} TTS chars</span>
          <span>Voice {run.voice_id}</span>
        </div>
        {run.error && <p className="err">{run.error}</p>}
        {run.status === "failed" && (
          <button
            className="ghost"
            style={{ marginTop: 14 }}
            onClick={() => api.retryRun(run.id).then(setRun)}
          >
            Retry from the failed stage
          </button>
        )}
      </div>

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

      {review && !review.passed && (
        <div className="panel">
          <label>Review findings</label>
          <pre>{review.findings}</pre>
        </div>
      )}

      {audio && (
        <div className="panel">
          <label>Audio</label>
          <AudioPlayer runId={run.id} artifact={audio} />
        </div>
      )}

      {script && (
        <div className="panel">
          <label>Script</label>
          <pre>{script}</pre>
        </div>
      )}
    </>
  );
}
