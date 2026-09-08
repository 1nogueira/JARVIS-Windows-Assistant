import { useLanguage } from "../i18n/LanguageContext";
import { Bot, Cpu, Database, Gauge, HardDrive, MessageSquare, Search, Settings, SlidersHorizontal, TerminalSquare, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import type { PageId } from "./Shell";

type Entry = { id: string; page: PageId; label: string; detail: string; icon: typeof Search; keywords: string; model?: string };

const pageEntries: Entry[] = [
  { id: "page-chat", page: "chat", label: "Chat", detail: "New conversation", icon: MessageSquare, keywords: "conversation question action" },
  { id: "page-dashboard", page: "dashboard", label: "Dashboard", detail: "TTFT, latency and health", icon: Gauge, keywords: "telemetria performance" },
  { id: "page-agents", page: "agents", label: "Agents", detail: "Persistent tasks", icon: Bot, keywords: "agente schedule monitor" },
  { id: "page-data", page: "data-sources", label: "Data Sources", detail: "Local sources and documents", icon: HardDrive, keywords: "files connectors" },
  { id: "page-memory", page: "memory", label: "Memory", detail: "Preferences in SQLite", icon: Database, keywords: "memory history" },
  { id: "page-tools", page: "tools", label: "Tools & Skills", detail: "Capabilities and permissions", icon: SlidersHorizontal, keywords: "tools skills" },
  { id: "page-logs", page: "logs", label: "Logs & Traces", detail: "Operational activity", icon: TerminalSquare, keywords: "logs traces events" },
  { id: "page-system", page: "system", label: "System", detail: "CPU, RAM, GPU and processes", icon: Cpu, keywords: "hardware system" },
  { id: "page-settings", page: "settings", label: "Settings", detail: "Models, voice and privacy", icon: Settings, keywords: "configuration models voice" },
];

export function CommandPalette({ close, navigate }: { close: () => void; navigate: (page: PageId) => void }) {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const [dynamic, setDynamic] = useState<Entry[]>([]);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", handler);
    void Promise.all([api.models(), api.skills(), api.agents()]).then(([catalog, skills, agents]) => {
      setDynamic([
        ...catalog.models.map((model) => ({ id: `model-${model.name}`, page: "settings" as const, label: model.name, detail: `${model.engine} · ${model.recommended ? t("recommended") : t("local model")}`, icon: Cpu, keywords: `modelo ${model.engine} ${model.tool_calling ? "tools" : ""} ${model.vision ? "vision" : ""}`, model: model.name })),
        ...skills.map((skill) => ({ id: `skill-${skill.name}`, page: "tools" as const, label: skill.title, detail: `Skill · ${skill.category}`, icon: SlidersHorizontal, keywords: `${skill.name} ${skill.description} ${skill.capabilities.join(" ")}` })),
        ...agents.map((agent) => ({ id: `agent-${agent.id}`, page: "agents" as const, label: agent.name, detail: `Agent · ${agent.state}`, icon: Bot, keywords: `${agent.type} ${agent.instruction}` })),
      ]);
    }).catch(() => undefined);
    return () => window.removeEventListener("keydown", handler);
  }, [close]);
  const filtered = useMemo(() => [...pageEntries, ...dynamic].filter((entry) => `${entry.label} ${entry.detail} ${entry.keywords}`.toLowerCase().includes(query.toLowerCase())), [query, dynamic]);
  useEffect(() => setSelected(0), [query]);
  const activate = async (entry: Entry | undefined) => {
    if (!entry) return;
    try {
      if (entry.model) {
        await api.selectModel(entry.model);
        window.dispatchEvent(new CustomEvent("jarvis-model-selected", { detail: entry.model }));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Could not switch models."));
      return;
    }
    navigate(entry.page);
    close();
  };
  const keyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") { event.preventDefault(); setSelected((value) => Math.min(value + 1, filtered.length - 1)); }
    if (event.key === "ArrowUp") { event.preventDefault(); setSelected((value) => Math.max(value - 1, 0)); }
    if (event.key === "Enter") { event.preventDefault(); void activate(filtered[selected]); }
  };
  return (
    <div className="palette-backdrop" onMouseDown={close}>
      <section className="command-palette" onMouseDown={(event) => event.stopPropagation()}>
        <header><Search size={18} /><input ref={input} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={keyDown} placeholder={t("Search pages, agents, skills, models and actions…")} /><button aria-label={t("Close command palette")} onClick={close}><X size={16} /></button></header>
        <div className="palette-results">
          <small>{t("Local commands")}</small>
          {error && <p className="inline-error">{error}</p>}
          {filtered.map((entry, index) => <button className={index === selected ? "selected" : ""} key={entry.id} onMouseMove={() => setSelected(index)} onClick={() => void activate(entry)}><span><entry.icon size={16} /></span><div><b>{t(entry.label)}</b><em>{t(entry.detail)}</em></div><kbd>Enter</kbd></button>)}
          {!filtered.length && <p>{t("No command matched “")}{query}”.</p>}
        </div>
        <footer><span><kbd>↑↓</kbd> {t("move")}</span><span><kbd>Enter</kbd> {t("select")}</span><span><kbd>Esc</kbd> {t("close")}</span><strong>{t("Local index")}</strong></footer>
      </section>
    </div>
  );
}
