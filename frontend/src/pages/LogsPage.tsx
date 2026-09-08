import { Activity, CircleAlert, Filter, RefreshCw, Search, TerminalSquare, Timer, Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { api } from "../services/api";
import { useLanguage } from "../i18n/LanguageContext";
import type { TraceEvent } from "../types";

export function LogsPage() {
  const { language, t } = useLanguage();
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const [logs, setLogs] = useState<Array<Record<string, unknown>>>([]);
  const [tab, setTab] = useState<"traces" | "audit">("traces");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const load = () => void Promise.all([api.traces(), api.logs()]).then(([traceItems, logItems]) => { setTraces(traceItems); setLogs(logItems); setError(""); }).catch((cause) => setError(cause.message));
  useEffect(() => { load(); const timer = window.setInterval(load, 5000); return () => window.clearInterval(timer); }, []);
  const filteredTraces = traces.filter((item) => `${item.event} ${item.request_id} ${JSON.stringify(item.data)}`.toLowerCase().includes(query.toLowerCase()));
  const filteredLogs = logs.filter((item) => JSON.stringify(item).toLowerCase().includes(query.toLowerCase()));
  return (
    <section className="page-scroll logs-page">
      <PageHeader icon={TerminalSquare} eyebrow={t("SAFE OPERATIONS")} title={t("Logs & Traces")} description={t("Routes, inference, skills and tools—without private chain-of-thought or prompt content.")} actions={<button className="button secondary" onClick={load}><RefreshCw size={14} />{t("Refresh")}</button>} />
      <div className="toolbar-row"><div className="tabs"><button className={tab === "traces" ? "active" : ""} onClick={() => setTab("traces")}><Activity size={14} />{t("Traces")}<span>{traces.length}</span></button><button className={tab === "audit" ? "active" : ""} onClick={() => setTab("audit")}><TerminalSquare size={14} />{t("Audit log")}<span>{logs.length}</span></button></div><label className="search-box"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Filter events…")} /></label><button className="icon-control"><Filter size={14} /></button></div>
      {error && <div className="inline-error">{error}</div>}
      {tab === "traces" ? <section className="trace-table"><header><span>{t("Time")}</span><span>{t("Event")}</span><span>{t("Request")}</span><span>{t("Operational detail")}</span></header>{filteredTraces.map((trace) => <article key={trace.id}><time>{new Date(trace.timestamp).toLocaleTimeString(language)}</time><span className={`trace-event event-${trace.event.split(".").at(-1)}`}><EventIcon event={trace.event} />{trace.event}</span><code>{trace.request_id.slice(0, 8)}</code><p>{traceDetail(trace.data)}</p></article>)}</section> : <section className="audit-list">{filteredLogs.map((log, index) => <article key={index}><span><TerminalSquare size={14} /></span><pre>{JSON.stringify(log, null, 2)}</pre></article>)}</section>}
      {tab === "traces" && !filteredTraces.length && <div className="large-empty"><span><Activity size={23} /></span><h2>{t("No trace events")}</h2><p>{t("Operational events will appear after the next request.")}</p></div>}
    </section>
  );
}

function EventIcon({ event }: { event: string }) {
  if (event.includes("tool")) return <Wrench size={13} />;
  if (event.includes("failed") || event.includes("error")) return <CircleAlert size={13} />;
  if (event.includes("inference")) return <Timer size={13} />;
  return <Activity size={13} />;
}
function traceDetail(data: Record<string, unknown>) { return Object.entries(data).filter(([key]) => key !== "request_id").map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`).join(" · ") || "—"; }
