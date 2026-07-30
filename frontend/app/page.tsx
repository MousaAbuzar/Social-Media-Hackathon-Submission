"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { RESET_EVENT } from "@/components/ResetButton";
import { api, formatCost, type RunSummary } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  pending: "queued",
  running: "working",
  awaiting_input: "needs you",
  completed: "done",
  failed: "failed",
  canceled: "canceled",
};

export default function HomePage() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRuns(await api.listRuns());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // The reset tab lives in the layout, so it reaches the form by event rather
  // than by prop drilling through a server component.
  useEffect(() => {
    const onReset = () => {
      setTopic("");
      setError(null);
      setBusy(false);
      void load();
    };
    window.addEventListener(RESET_EVENT, onReset);
    return () => window.removeEventListener(RESET_EVENT, onReset);
  }, [load]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun(topic);
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <>
      <form className="panel" onSubmit={submit}>
        <label htmlFor="topic">Step 1 — What is the video about?</label>
        <textarea
          id="topic"
          value={topic}
          placeholder="What happens to information that falls into a black hole?"
          onChange={(e) => setTopic(e.target.value)}
          required
          minLength={3}
        />
        <p className="hint">
          You&apos;ll get five title options to choose from next. Nothing is written until you pick
          one.
        </p>

        <button type="submit" disabled={busy || topic.trim().length < 3}>
          {busy ? "Getting titles…" : "Get title options"}
        </button>
        {error && <p className="err">{error}</p>}
      </form>

      <div className="panel">
        <label>Recent runs</label>
        {runs.length === 0 && <p className="hint">Nothing yet.</p>}
        {runs.map((run) => (
          <div className="row" key={run.id}>
            <Link href={`/runs/${run.id}`}>{run.chosen_title ?? run.topic}</Link>
            <span className="meta">
              <span>{formatCost(run.cost_micros)}</span>
              <span className={`badge ${run.status}`}>
                {STATUS_LABEL[run.status] ?? run.status}
              </span>
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
