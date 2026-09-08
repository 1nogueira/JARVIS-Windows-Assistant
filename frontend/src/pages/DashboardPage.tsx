import { Activity, Bot, CheckCircle2, Clock3, Cpu, Gauge, MemoryStick, RefreshCw, Timer, Wrench, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageHeader } from "../components/PageHeader";
import { api } from "../services/api";
import { useLanguage } from "../i18n/LanguageContext";
import type { ManagedAgent, SystemMetrics, TaskGraphInfo, TelemetryDashboard } from "../types";

export function DashboardPage() {
  const { language, t } = useLanguage();
  const number = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString(language) : "0";
  const [telemetry, setTelemetry] = useState<TelemetryDashboard | null>(null);
  const [system, setSystem] = useState<SystemMetrics | null>(null);
  const [tasks, setTasks] = useState<TaskGraphInfo[]>([]);
  const [agents, setAgents] = useState<ManagedAgent[]>([]);
  const [error, setError] = useState("");
  const refresh = () => void Promise.all([api.telemetry(), api.system(), api.tasks(), api.agents()]).then(([t, s, recentTasks, managed]) => { setTelemetry(t); setSystem(s.metrics); setTasks(recentTasks); setAgents(managed); setError(""); }).catch((cause) => setError(cause.message));
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 15_000); return () => window.clearInterval(timer); }, []);
  const summary = telemetry?.summary ?? {};
  return (
    <section className="page-scroll dashboard-page">
      <PageHeader icon={Gauge} eyebrow={t("OBSERVABILITY")} title={t("Dashboard")} description={t("Performance, local runtime health and recent execution outcomes.")} actions={<button className="button secondary" onClick={refresh}><RefreshCw size={14} />{t("Refresh")}</button>} />
      {error && <div className="inline-error">{error}</div>}
      <div className="metric-grid">
        <Metric icon={Activity} label={t("Requests")} value={number(summary.requests)} detail={t("recorded locally")} />
        <Metric icon={Timer} label={t("Average TTFT")} value={milliseconds(summary.average_ttft_ms)} detail={t("time to first token")} accent="cyan" />
        <Metric icon={Clock3} label={t("Average latency")} value={milliseconds(summary.average_latency_ms)} detail={t("request to completion")} />
        <Metric icon={Zap} label={t("Throughput")} value={rate(summary.average_tokens_per_second)} detail={t("generated tokens")} accent="violet" />
        <Metric icon={Wrench} label={t("Tool calls")} value={number(summary.tool_calls)} detail={t("{count} failed", { count: number(summary.failures) })} />
        <Metric icon={Bot} label={t("Active agents")} value={String(agents.filter((agent) => agent.active).length)} detail={t("{count} configured", { count: agents.length })} accent="green" />
      </div>
      <div className="dashboard-grid">
        <section className="panel chart-panel"><header><div><small>{t("LAST 14 DAYS")}</small><h2>{t("Request performance")}</h2></div><span><i />{t("TTFT")}<i className="latency" />{t("latency")}</span></header><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><AreaChart data={telemetry?.series ?? []}><defs><linearGradient id="ttft" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#43d8ef" stopOpacity={0.3} /><stop offset="1" stopColor="#43d8ef" stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="rgba(255,255,255,.05)" vertical={false} /><XAxis dataKey="day" tick={{ fill: "#66747d", fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis tick={{ fill: "#66747d", fontSize: 10 }} axisLine={false} tickLine={false} width={42} /><Tooltip contentStyle={{ background: "#11171c", border: "1px solid rgba(255,255,255,.1)", borderRadius: 10, fontSize: 11 }} /><Area type="monotone" name={t("Latency")} dataKey="latency_ms" stroke="#8b7cf6" fill="transparent" strokeWidth={2} /><Area type="monotone" name="TTFT" dataKey="ttft_ms" stroke="#43d8ef" fill="url(#ttft)" strokeWidth={2} /></AreaChart></ResponsiveContainer></div></section>
        <section className="panel runtime-health"><header><div><small>{t("LIVE")}</small><h2>{t("System health")}</h2></div><CheckCircle2 size={16} /></header><Health label={t("CPU")} value={system?.cpu_percent ?? 0} detail={t("{count} logical cores", { count: system?.cpu_count ?? 0 })} icon={Cpu} /><Health label={t("Memory")} value={system?.ram_percent ?? 0} detail={system ? `${system.ram_used_gb} / ${system.ram_total_gb} GB` : "—"} icon={MemoryStick} /><Health label={t("GPU")} value={system?.gpu[0]?.utilization_percent ?? 0} detail={system?.gpu[0]?.name ?? t("Not detected")} icon={Zap} /></section>
        <section className="panel recent-tasks"><header><div><small>{t("EXECUTION GRAPH")}</small><h2>{t("Recent tasks")}</h2></div><span>{t("{count} runs", { count: tasks.length })}</span></header>{tasks.slice(0, 6).map((task) => <article key={task.id}><span className={task.summary.failed ? "failed" : "success"}><Activity size={14} /></span><div><b>{task.title}</b><p>{task.actions.map((action) => action.label).join(" · ")}</p></div><em>{task.summary.completed || 0}/{task.actions.length}</em></article>)}{!tasks.length && <p className="empty-copy">{t("No task graphs yet.")}</p>}</section>
      </div>
    </section>
  );
}

function Metric({ icon: Icon, label, value, detail, accent = "" }: { icon: typeof Activity; label: string; value: string; detail: string; accent?: string }) { return <article className={`metric-card ${accent}`}><header><Icon size={15} />{label}</header><strong>{value}</strong><span>{detail}</span></article>; }
function Health({ label, value, detail, icon: Icon }: { label: string; value: number; detail: string; icon: typeof Cpu }) { return <div className="health-row"><Icon size={15} /><span><b>{label}</b><small>{detail}</small></span><em>{value.toFixed(0)}%</em><div><i style={{ width: `${Math.min(value, 100)}%` }} /></div></div>; }
function milliseconds(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(0)} ms` : "—"; }
function rate(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)} t/s` : "—"; }
