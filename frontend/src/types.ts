export type AssistantState = "IDLE" | "LISTENING" | "TRANSCRIBING" | "THINKING" | "STREAMING" | "EXECUTING" | "WAITING_CONFIRMATION" | "SPEAKING" | "PERSISTENT_ACTIVE" | "ERROR";

export interface ActionTrace {
  tool: string;
  status: "pending" | "running" | "started" | "needs_confirmation" | "completed" | "failed" | "confirmation_required" | "cancelled" | "timed_out";
  detail: string;
  action_id?: string;
  label?: string;
}

export interface Source {
  title?: string;
  url: string;
  source?: string;
  published_date?: string;
}

export interface ChatMessage {
  id: string;
  requestId?: string;
  role: "user" | "assistant";
  content: string;
  speechText?: string;
  streaming?: boolean;
  route?: string;
  agent?: string;
  model?: string;
  engine?: string;
  metrics?: Record<string, unknown>;
  actions?: ActionTrace[];
  sources?: Source[];
  confirmationId?: string | null;
  confirmationPreview?: {
    title: string;
    summary: string;
    details: Array<{ label: string; value: string }>;
  } | null;
}

export interface BackendStatus {
  online: boolean;
  version: string;
  ollama: { online: boolean; selected?: string; models: Array<{ name: string }> };
  voice: {
    stt: { available: boolean };
    tts: { available: boolean };
    theme?: { available: boolean; playing: boolean; paused?: boolean; mode?: string };
    wakeword: {
      available: boolean;
      running: boolean;
      paused?: boolean;
      wake_word: string;
      microphone_muted?: boolean;
    };
  };
}

export interface ModelInfo {
  name: string;
  engine: string;
  size_bytes: number;
  parameter_billions?: number | null;
  context_length?: number | null;
  tool_calling: boolean;
  vision: boolean;
  embeddings: boolean;
  approximate_ram_gb?: number | null;
  approximate_vram_gb?: number | null;
  available: boolean;
  selected: boolean;
  recommended: boolean;
}

export interface ModelCatalog {
  online: boolean;
  engine: string;
  models: ModelInfo[];
  selected: string;
  recommended?: string | null;
  hardware: {
    os: string;
    architecture: string;
    cpu: string;
    cpu_cores: number;
    ram_gb: number;
    gpus: Array<{ name: string; vendor: string; vram_gb: number }>;
    recommendation?: { engine: string; model_class: string; reason: string };
  };
}

export interface SkillInfo {
  name: string;
  title: string;
  description: string;
  tools: string[];
  capabilities: string[];
  category: string;
  version: string;
  deterministic: boolean;
  subskills: string[];
  instructions?: string;
}

export interface ManagedAgent {
  id: string;
  name: string;
  type: "manual" | "daily" | "weekly" | "interval" | "monitor";
  instruction: string;
  model: string;
  skills: string[];
  tools: string[];
  max_turns: number;
  memory_enabled: boolean;
  schedule: string;
  active: boolean;
  state: string;
  last_run_at?: string | null;
  next_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DataSourceInfo {
  id: string;
  name: string;
  type: string;
  status: string;
  local: boolean;
  path?: string;
  description: string;
}

export interface TraceEvent {
  id: number;
  request_id: string;
  timestamp: string;
  event: string;
  data: Record<string, unknown>;
}

export interface TelemetryDashboard {
  summary: {
    requests?: number;
    average_latency_ms?: number | null;
    average_ttft_ms?: number | null;
    average_tokens_per_second?: number | null;
    tool_calls?: number;
    failures?: number;
    cancellations?: number;
    output_tokens?: number;
  };
  series: Array<{ day: string; requests: number; ttft_ms?: number; latency_ms?: number; tool_calls: number; failures: number }>;
  generated_at: number;
}

export interface TaskGraphInfo {
  id: string;
  request_id: string;
  title: string;
  created_at: number;
  completed_at?: number | null;
  summary: Record<string, number>;
  actions: Array<{ id: string; label: string; tool: string; status: string; detail: string }>;
}

export interface SystemMetrics {
  cpu_percent: number;
  cpu_count: number;
  ram_percent: number;
  ram_used_gb: number;
  ram_total_gb: number;
  disk_percent: number;
  disk_free_gb: number;
  network_sent_mb: number;
  network_received_mb: number;
  uptime_seconds: number;
  gpu: Array<{ name: string; utilization_percent: number; vram_used_mb: number; vram_total_mb: number; temperature_c: number }>;
}

export interface ProcessInfo { pid: number; name: string; cpu_percent: number; memory_mb: number; status: string }
export interface ToolInfo { name: string; description: string; permission_level: string; category: string; enabled: boolean }
export interface MemoryInfo { id: number; key: string; value: string; category: string; created_at: string; updated_at: string }
