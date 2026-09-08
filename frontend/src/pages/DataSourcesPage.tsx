import { Database, FileText, FolderOpen, HardDrive, Image, LockKeyhole, Plus, RefreshCw, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { api } from "../services/api";
import { useLanguage } from "../i18n/LanguageContext";
import type { DataSourceInfo } from "../types";

const icons = { sqlite: Database, filesystem: HardDrive, folder: FolderOpen, vision: Image };

export function DataSourcesPage() {
  const { language, t } = useLanguage();
  const [sources, setSources] = useState<DataSourceInfo[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const load = () => void api.dataSources().then((items) => { setSources(items); setError(""); }).catch((cause) => setError(cause.message));
  useEffect(load, [language]);
  const shown = sources.filter((source) => `${source.name} ${source.description} ${source.type}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <section className="page-scroll">
      <PageHeader icon={HardDrive} eyebrow={t("LOCAL KNOWLEDGE")} title={t("Data Sources")} description={t("Filesystem, memory and visual context remain local and explicitly scoped.")} actions={<button className="button secondary" onClick={load}><RefreshCw size={14} />{t("Refresh")}</button>} />
      <div className="data-source-banner"><LockKeyhole size={17} /><div><b>{t("Local-first boundary")}</b><span>{t("Source content is always untrusted data and never receives system-level authority.")}</span></div><span>{t("No cloud required")}</span></div>
      <label className="search-box wide"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Search data sources…")} /></label>
      {error && <div className="inline-error">{error}</div>}
      <div className="source-grid">{shown.map((source) => { const Icon = icons[source.type as keyof typeof icons] ?? FileText; return <article className="source-card" key={source.id}><header><span><Icon size={19} /></span><em className={`source-status ${source.status}`}><i />{source.status}</em></header><h3>{source.name}</h3><p>{source.description}</p>{source.path && <code>{source.path}</code>}<footer><span>{source.type}</span><b>{source.local ? t("ON DEVICE") : t("EXTERNAL")}</b></footer></article>; })}<button className="source-card add-source" disabled title={t("Folder picker will be connected to Tauri secure scope")}><Plus size={20} /><b>{t("Add local folder")}</b><span>{t("Choose a directory to index")}</span></button></div>
    </section>
  );
}
