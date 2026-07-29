"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { RESET_EVENT } from "@/components/ResetButton";
import { api, formatCost, type RunSummary, type Voice } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setToken(window.localStorage.getItem("scriptcast_token") ?? "");
  }, []);

  const load = async () => {
    try {
      const [v, r] = await Promise.all([api.voices(), api.listRuns()]);
      setVoices(v);
      setRuns(r);
      setVoiceId((current) => current || v[0]?.id || "");
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (token) void load();
  }, [token]);

  // The reset tab lives in the layout, so it reaches the form by event rather
  // than by prop drilling through a server component.
  useEffect(() => {
    const onReset = () => {
      setTopic("");
      setVoiceId(voices[0]?.id ?? "");
      setError(null);
      setBusy(false);
      // Pull the run list again so a run started moments ago shows up.
      void load();
    };
    window.addEventListener(RESET_EVENT, onReset);
    return () => window.removeEventListener(RESET_EVENT, onReset);
  }, [voices]);

  const saveToken = (value: string) => {
    window.localStorage.setItem("scriptcast_token", value);
    setToken(value);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun(topic, voiceId);
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <>
      <div className="panel">
        <label htmlFor="token">API token</label>
        <input
          id="token"
          type="password"
          value={token}
          placeholder="APP_TOKEN from your .env"
          onChange={(e) => saveToken(e.target.value)}
        />
      </div>

      <form className="panel" onSubmit={submit}>
        <label htmlFor="topic">Topic</label>
        <textarea
          id="topic"
          value={topic}
          placeholder="What happens to information that falls into a black hole?"
          onChange={(e) => setTopic(e.target.value)}
          required
          minLength={3}
        />

        <label htmlFor="voice">Voice</label>
        <select id="voice" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
          {voices.map((v) => (
            <option key={v.id} value={v.id}>
              {v.label} — {v.description}
            </option>
          ))}
        </select>

        <button type="submit" disabled={busy || !topic.trim() || !voiceId}>
          {busy ? "Starting…" : "Generate"}
        </button>
        {error && <p className="err">{error}</p>}
      </form>

      <div className="panel">
        <label>Recent runs</label>
        {runs.length === 0 && <p className="sub">Nothing yet.</p>}
        {runs.map((run) => (
          <div className="row" key={run.id}>
            <Link href={`/runs/${run.id}`}>{run.chosen_title ?? run.topic}</Link>
            <span className="meta">
              <span>{formatCost(run.cost_micros)}</span>
              <span className={`badge ${run.status}`}>{run.status}</span>
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
