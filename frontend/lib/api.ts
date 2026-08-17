export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * The bearer token every request carries.
 *
 * Supplied by the deployment rather than typed into the UI. This is a
 * single-user tool behind one shared secret, so asking for it on every visit
 * bought nothing — the browser bundle and the API read the same APP_TOKEN.
 */
export const APP_TOKEN = process.env.NEXT_PUBLIC_APP_TOKEN ?? "dev-local-token";

export type StageName = "titles" | "script" | "review" | "tts" | "package";
export type StageStatus = "pending" | "running" | "completed" | "failed" | "skipped";
export type RunStatus =
  | "pending"
  | "running"
  // Parked on a decision you need to make, not an error.
  | "awaiting_input"
  | "completed"
  | "failed"
  | "canceled";

export interface Stage {
  name: StageName;
  position: number;
  status: StageStatus;
  attempt: number;
  error: string | null;
  output: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Artifact {
  id: string;
  kind: string;
  content_type: string;
  size_bytes: number;
  meta: Record<string, unknown> | null;
}

/** One generated title plus the case for choosing it. */
export interface TitleCandidate {
  title: string;
  why: string;
}

export interface ScriptLengthSettings {
  default_minutes: number;
  min_minutes: number;
  max_minutes: number;
  words_per_minute: number;
}

export interface Run {
  id: string;
  topic: string;
  voice_id: string | null;
  chosen_title: string | null;
  target_minutes: number | null;
  status: RunStatus;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  tts_characters: number;
  cost_micros: number;
  created_at: string;
  updated_at: string;
  stages: Stage[];
  artifacts: Artifact[];
}

export interface RunSummary {
  id: string;
  topic: string;
  chosen_title: string | null;
  status: RunStatus;
  cost_micros: number;
  created_at: string;
}

/** How fast synthesis runs on this machine, for the progress countdown. */
export interface TtsRate {
  chars_per_second: number;
  /** "measured" once a real synthesis has finished here, "default" before. */
  source: "measured" | "default";
  samples: number;
}

export interface Voice {
  id: string;
  label: string;
  provider: string;
  description: string;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${APP_TOKEN}`,
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status}: ${body}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  settings: () => request<ScriptLengthSettings>("/settings"),
  voices: () => request<Voice[]>("/voices"),
  ttsRate: () => request<TtsRate>("/tts/rate"),
  listRuns: () => request<RunSummary[]>("/runs"),
  getRun: (id: string) => request<Run>(`/runs/${id}`),
  createRun: (topic: string) =>
    request<Run>("/runs", { method: "POST", body: JSON.stringify({ topic }) }),
  selectTitle: (id: string, title: string, targetMinutes: number) =>
    request<Run>(`/runs/${id}/title`, {
      method: "POST",
      body: JSON.stringify({ title, target_minutes: targetMinutes }),
    }),
  selectVoice: (id: string, voiceId: string) =>
    request<Run>(`/runs/${id}/voice`, {
      method: "POST",
      body: JSON.stringify({ voice_id: voiceId }),
    }),
  retryRun: (id: string) => request<Run>(`/runs/${id}/retry`, { method: "POST" }),
  artifactUrl: (runId: string, artifactId: string) =>
    request<{ url: string; content_type: string }>(
      `/runs/${runId}/artifacts/${artifactId}/url`,
    ),
  /**
   * The artifact's bytes, served by the API rather than by object storage.
   *
   * The presigned URL above points the browser at storage on its own port,
   * which turned out to answer intermittent 503s that cost the download
   * outright. This path is the same origin the rest of the app already talks
   * to successfully, so the save no longer depends on that second hop.
   */
  artifactBlob: async (runId: string, artifactId: string): Promise<Blob> => {
    const response = await fetch(
      `${API_URL}/api/runs/${runId}/artifacts/${artifactId}/download`,
      { headers: { Authorization: `Bearer ${APP_TOKEN}` } },
    );
    if (!response.ok) throw new Error(`download failed: ${response.status}`);
    return response.blob();
  },
};

export function formatCost(micros: number): string {
  return `$${(micros / 1_000_000).toFixed(4)}`;
}

export const STAGE_LABELS: Record<StageName, string> = {
  titles: "Title candidates",
  script: "Script",
  review: "Quality review",
  tts: "Voice synthesis",
  package: "Package",
};
