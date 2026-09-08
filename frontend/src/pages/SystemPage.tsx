import { useLanguage } from "../i18n/LanguageContext";
import { useEffect, useState } from "react";
import { Cpu, HardDrive, MemoryStick, Network, RefreshCw, Timer, Thermometer } from "lucide-react";
import { api } from "../services/api";
import type { ProcessInfo, SystemMetrics } from "../types";

function Gauge({ label, value, detail, icon: Icon }: { label: string; value: number; detail: string; icon: typeof Cpu }) {
  return <div className="gauge-card"><header><Icon size={16} />{label}</header><strong>{value.toFixed(0)}<small>%</small></strong><div className="gauge-track"><i style={{ width: `${value}%` }} /></div><span>{detail}</span></div>;
}

export function SystemPage() {
  const { t } = useLanguage();
  const [data, setData] = useState<{ metrics: SystemMetrics; processes: ProcessInfo[] } | null>(null);
  const [error, setError] = useState("");
  const refresh = () => void api.system().then((value) => { setData(value); setError(""); }).catch((cause) => setError(cause.message));
  useEffect(() => { refresh(); const timer = setInterval(refresh, 5000); return () => clearInterval(timer); }, []);
  const metrics = data?.metrics;
  return (
    <section className="standard-page page-enter">
      <div className="page-title row"><span><Cpu size={18} /></span><div><h2>{t("System")}</h2><p>{t("Efficiently updated local telemetry.")}</p></div><button className="secondary push-right" onClick={refresh}><RefreshCw size={14} /> {t("Refresh")}</button></div>
      {error && <div className="inline-error">{error}</div>}
      <div className="gauge-grid">
        <Gauge label={t("PROCESSOR")} value={metrics?.cpu_percent ?? 0} detail={`${metrics?.cpu_count ?? 0} logical threads`} icon={Cpu} />
        <Gauge label={t("MEMORY")} value={metrics?.ram_percent ?? 0} detail={metrics ? `${metrics.ram_used_gb} / ${metrics.ram_total_gb} GB` : "—"} icon={MemoryStick} />
        <Gauge label={t("STORAGE")} value={metrics?.disk_percent ?? 0} detail={metrics ? `${metrics.disk_free_gb} GB free` : "—"} icon={HardDrive} />
        <Gauge label={t("GPU")} value={metrics?.gpu[0]?.utilization_percent ?? 0} detail={metrics?.gpu[0]?.name ?? t("Not detected")} icon={Thermometer} />
      </div>
      <div className="system-strip">
        <span><Network size={15} /><b>{t("Received")}</b>{metrics?.network_received_mb.toFixed(1) ?? "—"} MB</span>
        <span><Network size={15} /><b>{t("Sent")}</b>{metrics?.network_sent_mb.toFixed(1) ?? "—"} MB</span>
        <span><Timer size={15} /><b>{t("Uptime")}</b>{metrics ? `${Math.floor(metrics.uptime_seconds / 3600)}h` : "—"}</span>
      </div>
      <div className="table-card"><header><h3>{t("Top processes")}</h3><span>{t("sorted by memory")}</span></header><div className="data-table"><div className="table-row table-head"><span>{t("Process")}</span><span>PID</span><span>CPU</span><span>{t("Memory")}</span></div>{data?.processes.map((process) => <div className="table-row" key={process.pid}><span>{process.name}</span><span>{process.pid}</span><span>{process.cpu_percent.toFixed(1)}%</span><span>{process.memory_mb.toFixed(1)} MB</span></div>)}</div></div>
    </section>
  );
}
