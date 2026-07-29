export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type StageName = "titles" | "script" | "review" | "tts" | "package";
export type StageStatus = "pending" | "running" | "completed" | "failed" | "skipped";
export type RunStatus = "pending" | "running" | "completed" | "failed" | "canceled";

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

export interface Run {
  id: string;
  topic: string;
  voice_id: string;
  chosen_title: string | null;
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

export interface Voice {
  id: string;
  label: string;
  provider: string;
  description: string;
}

function token(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem("scriptcast_token") ?? "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token()}`,
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
  voices: () => request<Voice[]>("/voices"),
  listRuns: () => request<RunSummary[]>("/runs"),
  getRun: (id: string) => request<Run>(`/runs/${id}`),
  createRun: (topic: string, voiceId: string) =>
    request<Run>("/runs", {
      method: "POST",
      body: JSON.stringify({ topic, voice_id: voiceId }),
    }),
  retryRun: (id: string) => request<Run>(`/runs/${id}/retry`, { method: "POST" }),
  artifactUrl: (runId: string, artifactId: string) =>
    request<{ url: string; content_type: string }>(
      `/runs/${runId}/artifacts/${artifactId}/url`,
    ),
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
