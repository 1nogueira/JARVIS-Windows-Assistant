import { useLanguage } from "../i18n/LanguageContext";
import { useEffect, useState } from "react";
import { Database, Plus, Search, Trash2 } from "lucide-react";
import { api } from "../services/api";
import type { MemoryInfo } from "../types";

export function MemoryPage() {
  const { t, language } = useLanguage();
  const [memories, setMemories] = useState<MemoryInfo[]>([]);
  const [query, setQuery] = useState(""); const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ key: "", value: "", category: "general" }); const [error, setError] = useState("");
  const load = () => void api.memories(query).then(setMemories).catch((cause) => setError(cause.message));
  useEffect(load, [query]);
  const create = async () => { try { await api.createMemory(form.key, form.value, form.category); setForm({ key: "", value: "", category: "general" }); setAdding(false); load(); } catch (cause) { setError(cause instanceof Error ? cause.message : t("Could not save.")); } };
  const remove = async (memory: MemoryInfo) => { if (window.confirm(`${t("Delete memory")} “${memory.key}”?`)) { await api.deleteMemory(memory.id); load(); } };
  return (
    <section className="standard-page page-enter">
      <div className="page-title row"><span><Database size={18} /></span><div><h2>{t("Memory")}</h2><p>{t("Information explicitly stored in local SQLite.")}</p></div><button className="primary push-right" onClick={() => setAdding(!adding)}><Plus size={15} /> {t("New memory")}</button></div>
      <div className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Search memories…")} /></div>
      {adding && <div className="memory-form"><input placeholder={t("Key, e.g. favorite editor")} value={form.key} onChange={(event) => setForm({ ...form, key: event.target.value })} /><input placeholder={t("Value")} value={form.value} onChange={(event) => setForm({ ...form, value: event.target.value })} /><input placeholder={t("Category")} value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} /><button className="primary" disabled={!form.key || !form.value} onClick={() => void create()}>{t("Save")}</button></div>}
      {error && <div className="inline-error">{error}</div>}
      <div className="memory-grid">{memories.map((memory) => <article className="memory-card" key={memory.id}><header><span>{memory.category}</span><button onClick={() => void remove(memory)} aria-label={t("Delete")}><Trash2 size={15} /></button></header><h3>{memory.key}</h3><p>{memory.value}</p><time>{new Date(memory.updated_at + "Z").toLocaleDateString(language)}</time></article>)}</div>
      {!memories.length && <div className="empty-state"><Database size={28} /><h3>{t("No memories found")}</h3><p>{t("Say “remember that…” or add information here.")}</p></div>}
    </section>
  );
}
