import type {
  BackendStatus,
  DataSourceInfo,
  ManagedAgent,
  MemoryInfo,
  ModelCatalog,
  ProcessInfo,
  SkillInfo,
  SystemMetrics,
  TaskGraphInfo,
  TelemetryDashboard,
  ToolInfo,
  TraceEvent,
} from "../types";
import { translateCurrent } from "../i18n/messages";

const API = import.meta.env.VITE_JARVIS_API ?? "http://127.0.0.1:8742";
const SESSION_KEY = "jarvis-session-token";
let tokenPromise: Promise<string> | undefined;

async function resolveSessionToken(): Promise<string> {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const token = await invoke<string>("get_session_token");
    if (token.length >= 32) return token;
  } catch { /* browser development mode */ }

  const fromHash = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("jarvis_token");
  const token = fromHash ?? sessionStorage.getItem(SESSION_KEY) ?? "";
  if (fromHash) {
    sessionStorage.setItem(SESSION_KEY, fromHash);
    history.replaceState(null, "", `${location.pathname}${location.search}`);
  }
  if (token.length < 32) {
    throw new Error(translateCurrent("Local session credential unavailable. Open JARVIS through the application."));
  }
  return token;
}

export function sessionToken(): Promise<string> {
  tokenPromise ??= resolveSessionToken();
  return tokenPromise;
}

async function request<T>(path: string, init?: RequestInit, authenticated = true): Promise<T> {
  const token = authenticated ? await sessionToken() : "";
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? "Falha ao comunicar com o backend.");
  }
  return response.json() as Promise<T>;
}

export interface ChatStreamEvent {
  type: "request" | "route" | "token" | "complete" | "cancelled" | "error";
  request_id?: string;
  content?: string;
  route?: { mode: string; agent: string; skills: string[]; reason: string };
  response?: {
    request_id: string;
    message: string;
    display_text: string;
    speech_text: string;
    state: string;
    actions: Array<{ tool: string; status: string; detail: string; action_id?: string; label?: string }>;
    sources: Array<{ title?: string; url: string; source?: string }>;
    confirmation_id?: string;
    confirmation_preview?: { title: string; summary: string; details: Array<{ label: string; value: string }> };
    route: string;
    agent: string;
    model: string;
    engine: string;
    metrics: Record<string, unknown>;
  };
  detail?: string;
}

async function chatStream(
  message: string,
  requestId: string,
  conversationId: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = await sessionToken();
  const response = await fetch(`${API}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
    body: JSON.stringify({ request_id: requestId, message, conversation_id: conversationId }),
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? "Falha ao iniciar o streaming.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const parsed = parseSseFrames(buffer);
    buffer = parsed.remainder;
    parsed.events.forEach(onEvent);
    if (done) break;
  }
  if (buffer.trim()) {
    const parsed = parseSseFrames(`${buffer}\n\n`);
    parsed.events.forEach(onEvent);
  }
}

export function parseSseFrames(buffer: string): {
  events: ChatStreamEvent[];
  remainder: string;
} {
  const frames = buffer.split(/\r?\n\r?\n/);
  const remainder = frames.pop() ?? "";
  const events = frames.flatMap((frame) => {
    const data = frame.split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    return data ? [JSON.parse(data) as ChatStreamEvent] : [];
  });
  return { events, remainder };
}

export const api = {
  baseUrl: API,
  status: () => request<BackendStatus>("/api/status", undefined, false),
  chat: (message: string, requestId: string, conversationId = "default") => request<{
    request_id: string; message: string; display_text: string; speech_text: string; state: string; actions: Array<{ tool: string; status: string; detail: string; action_id?: string; label?: string }>;
    sources: Array<{ title?: string; url: string; source?: string }>; confirmation_id?: string;
    confirmation_preview?: { title: string; summary: string; details: Array<{ label: string; value: string }> };
    route: string; agent: string; model: string; engine: string; metrics: Record<string, unknown>;
  }>("/api/chat", { method: "POST", body: JSON.stringify({ request_id: requestId, message, conversation_id: conversationId }) }),
  chatStream,
  confirm: (confirmationId: string, approved: boolean) => request<{
    approved?: boolean; tool?: string; result?: unknown; message?: string; request_id?: string; state?: string;
    actions?: Array<{ tool: string; status: string; detail: string }>;
    sources?: Array<{ title?: string; url: string; source?: string }>;
    confirmation_id?: string;
    confirmation_preview?: { title: string; summary: string; details: Array<{ label: string; value: string }> };
  }>(
    "/api/confirm", { method: "POST", body: JSON.stringify({ confirmation_id: confirmationId, approved }) },
  ),
  cancel: (requestId?: string) => request<{ cancelled_tasks: number; stopped_speech: boolean }>(
    `/api/cancel${requestId ? `?request_id=${encodeURIComponent(requestId)}` : ""}`, { method: "POST" },
  ),
  system: () => request<{ metrics: SystemMetrics; processes: ProcessInfo[] }>("/api/system"),
  tools: () => request<ToolInfo[]>("/api/tools"),
  toggleTool: (name: string, enabled: boolean) => request(`/api/tools/${encodeURIComponent(name)}`, {
    method: "PATCH", body: JSON.stringify({ enabled }),
  }),
  memories: (query = "") => request<MemoryInfo[]>(`/api/memory?query=${encodeURIComponent(query)}`),
  createMemory: (key: string, value: string, category: string) => request<MemoryInfo>("/api/memory", {
    method: "POST", body: JSON.stringify({ key, value, category, explicit: true }),
  }),
  deleteMemory: (id: number) => request(`/api/memory/${id}`, { method: "DELETE" }),
  settings: () => request<Record<string, any>>("/api/settings"),
  saveSettings: (settings: Record<string, any>) => request<Record<string, any>>("/api/settings", {
    method: "PUT", body: JSON.stringify({ settings }),
  }),
  onboarding: () => request<Record<string, { ok?: boolean; available?: boolean; detail?: string }>>("/api/onboarding/checks"),
  logs: () => request<Array<Record<string, unknown>>>("/api/logs"),
  transcribe: async (blob: Blob, signal?: AbortSignal) => {
    const token = await sessionToken();
    const response = await fetch(`${API}/api/voice/transcribe`, { method: "POST", headers: { "Content-Type": blob.type, Authorization: `Bearer ${token}` }, body: blob, signal });
    if (!response.ok) throw new Error((await response.json()).detail ?? translateCurrent("Transcription failed."));
    return response.json() as Promise<{ text: string }>;
  },
  speak: async (text: string, requestId: string) => {
    const token = await sessionToken();
    const response = await fetch(`${API}/api/voice/speak`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify({ text, request_id: requestId }),
    });
    if (!response.ok) throw new Error((await response.json()).detail ?? translateCurrent("Voice unavailable."));
    return response.blob();
  },
  speakLocal: (text: string, requestId: string) => request<{ played: boolean }>("/api/voice/speak", {
    method: "POST", body: JSON.stringify({ text, request_id: requestId, play_local: true }),
  }),
  playTheme: () => request<{ playing: boolean; path: string; volume_percent: number }>(
    "/api/voice/theme/play", { method: "POST" },
  ),
  stopTheme: () => request<{ playing: boolean; paused?: boolean }>("/api/voice/theme/stop", { method: "POST" }),
  pauseTheme: () => request<{ playing: boolean; paused?: boolean }>("/api/voice/theme/pause", { method: "POST" }),
  resumeTheme: () => request<{ playing: boolean; paused?: boolean }>("/api/voice/theme/resume", { method: "POST" }),
  restartTheme: () => request<{ playing: boolean; paused?: boolean }>("/api/voice/theme/restart", { method: "POST" }),
  setMicrophoneMuted: (muted: boolean) => request<{ microphone_muted: boolean }>(
    `/api/voice/microphone/${muted ? "mute" : "unmute"}`, { method: "POST" },
  ),
  wakeword: (enabled: boolean) => request(`/api/voice/wakeword/${enabled ? "start" : "stop"}`, { method: "POST" }),
  wakewordAction: (action: "start" | "stop" | "pause" | "resume") => request(`/api/voice/wakeword/${action}`, { method: "POST" }),
  reminders: () => request<Array<{ id: number; title: string; due_at: string; alert_at: string; recurrence: string; minutes_before: number }>>("/api/reminders"),
  models: () => request<ModelCatalog>("/api/models"),
  selectModel: (model: string) => request<{ selected: string; restart_required: boolean }>("/api/models/selected", {
    method: "PUT", body: JSON.stringify({ model }),
  }),
  engines: () => request<Array<Record<string, unknown>>>("/api/engines"),
  skills: () => request<SkillInfo[]>("/api/skills"),
  skill: (name: string) => request<SkillInfo>(`/api/skills/${encodeURIComponent(name)}`),
  tasks: () => request<TaskGraphInfo[]>("/api/tasks"),
  traces: (requestId?: string) => request<TraceEvent[]>(`/api/traces${requestId ? `?request_id=${encodeURIComponent(requestId)}` : ""}`),
  telemetry: () => request<TelemetryDashboard>("/api/telemetry/dashboard"),
  agents: () => request<ManagedAgent[]>("/api/agents"),
  createAgent: (agent: Omit<ManagedAgent, "id" | "state" | "created_at" | "updated_at" | "last_run_at" | "next_run_at">) => request<ManagedAgent>("/api/agents", {
    method: "POST", body: JSON.stringify(agent),
  }),
  updateAgent: (id: string, changes: Partial<ManagedAgent>) => request<ManagedAgent>(`/api/agents/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify(changes),
  }),
  deleteAgent: (id: string) => request<{ deleted: boolean }>(`/api/agents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  runAgent: (id: string) => request<{ agent_id: string; request_id: string; status: string }>(`/api/agents/${encodeURIComponent(id)}/run`, { method: "POST" }),
  agentRuns: (id: string) => request<Array<{ id: string; started_at: string; completed_at?: string; status: string; summary: string; trace_request_id?: string }>>(`/api/agents/${encodeURIComponent(id)}/runs`),
  dataSources: () => request<DataSourceInfo[]>("/api/data-sources"),
};
