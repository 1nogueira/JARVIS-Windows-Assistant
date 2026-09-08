import { Blocks, Search, ShieldCheck, SlidersHorizontal, Sparkles, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { api } from "../services/api";
import { useLanguage } from "../i18n/LanguageContext";
import type { SkillInfo, ToolInfo } from "../types";

export function ToolsPage() {
  const { language, t } = useLanguage();
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [tab, setTab] = useState<"skills" | "tools">("skills");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { void Promise.all([api.tools(), api.skills()]).then(([toolItems, skillItems]) => { setTools(toolItems); setSkills(skillItems); }).catch((cause) => setError(cause.message)); }, [language]);
  const shownSkills = skills.filter((item) => `${item.title} ${item.description} ${item.category}`.toLowerCase().includes(query.toLowerCase()));
  const grouped = useMemo(() => tools.filter((item) => `${item.name} ${item.description} ${item.category}`.toLowerCase().includes(query.toLowerCase())).reduce<Record<string, ToolInfo[]>>((result, tool) => { (result[tool.category] ??= []).push(tool); return result; }, {}), [tools, query]);
  const toggle = async (tool: ToolInfo) => { try { await api.toggleTool(tool.name, !tool.enabled); setTools((items) => items.map((item) => item.name === tool.name ? { ...item, enabled: !item.enabled } : item)); } catch (cause) { setError(cause instanceof Error ? cause.message : t("Could not update.")); } };
  return (
    <section className="page-scroll">
      <PageHeader icon={SlidersHorizontal} eyebrow={t("CAPABILITY LAYER")} title={t("Tools & Skills")} description={t("Lightweight skill discovery with typed tools and permissions outside the model.")} />
      <div className="security-banner"><ShieldCheck size={17} /><div><b>{t("Permission boundaries are enforced by the runtime")}</b><span>{t("SAFE, CONFIRM and RESTRICTED remain independent from model output.")}</span></div><em>{t("LOCAL POLICY")}</em></div>
      <div className="toolbar-row"><div className="tabs"><button className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}><Blocks size={14} />{t("Skills")}<span>{skills.length}</span></button><button className={tab === "tools" ? "active" : ""} onClick={() => setTab("tools")}><Wrench size={14} />{t("Tools")}<span>{tools.length}</span></button></div><label className="search-box"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Search capabilities…")} /></label></div>
      {error && <div className="inline-error">{error}</div>}
      {tab === "skills" ? <div className="skill-grid">{shownSkills.map((skill) => <article className="skill-card" key={skill.name}><header><span><Sparkles size={16} /></span><div><small>{skill.category}</small><h3>{skill.title}</h3></div>{skill.deterministic && <em>{t("FAST PATH")}</em>}</header><p>{skill.description}</p><div className="chip-list">{skill.capabilities.slice(0, 4).map((capability) => <span key={capability}>{capability}</span>)}</div><footer><span>{t("{count} tools", { count: skill.tools.length })}</span><b>v{skill.version}</b></footer></article>)}</div> : <div className="tool-sections">{Object.entries(grouped).map(([category, items]) => <section key={category}><header><div><h3>{category}</h3><span>{t("{enabled} of {total} enabled", { enabled: items.filter((tool) => tool.enabled).length, total: items.length })}</span></div></header>{items.map((tool) => <div className="tool-row-modern" key={tool.name}><span className="tool-icon"><Wrench size={14} /></span><div><b>{tool.name.replaceAll("_", " ")}</b><p>{tool.description}</p></div><em className={tool.permission_level.toLowerCase()}>{t(tool.permission_level)}</em><button className={`switch ${tool.enabled ? "on" : ""}`} onClick={() => void toggle(tool)}><i /></button></div>)}</section>)}</div>}
    </section>
  );
}
