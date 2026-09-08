import { useLanguage } from "../i18n/LanguageContext";
import { stateMessages } from "../i18n/messages";
import {
  Activity,
  Bot,
  BrainCircuit,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  Maximize2,
  MessageSquare,
  Minus,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Settings,
  SlidersHorizontal,
  Sparkles,
  TerminalSquare,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { useJarvis } from "../store/JarvisContext";
import { SystemPulse } from "./SystemPulse";
import { StatusOrb } from "./StatusOrb";

export type PageId = "chat" | "dashboard" | "agents" | "data-sources" | "memory" | "tools" | "logs" | "system" | "settings";

const primaryNavigation: Array<{ id: PageId; label: string; icon: typeof Bot }> = [
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "dashboard", label: "Dashboard", icon: Gauge },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "data-sources", label: "Data Sources", icon: HardDrive },
];

const secondaryNavigation: Array<{ id: PageId; label: string; icon: typeof Bot }> = [
  { id: "memory", label: "Memory", icon: Database },
  { id: "tools", label: "Tools & Skills", icon: SlidersHorizontal },
  { id: "logs", label: "Logs & Traces", icon: TerminalSquare },
  { id: "system", label: "System", icon: Cpu },
  { id: "settings", label: "Settings", icon: Settings },
];

export function Shell({
  page,
  setPage,
  children,
  openPalette,
}: {
  page: PageId;
  setPage: (page: PageId) => void;
  children: ReactNode;
  openPalette: () => void;
}) {
  const { t } = useLanguage();
  const { status, state, messages, stop } = useJarvis();
  const [collapsed, setCollapsed] = useState(false);
  const minimize = async () => { try { const { getCurrentWindow } = await import("@tauri-apps/api/window"); await getCurrentWindow().minimize(); } catch { /* browser mode */ } };
  const close = async () => { try { const { getCurrentWindow } = await import("@tauri-apps/api/window"); await getCurrentWindow().close(); } catch { /* browser mode */ } };
  const mini = async () => { setPage("chat"); try { const { invoke } = await import("@tauri-apps/api/core"); await invoke("set_mini_mode", { mini: true }); } catch { /* browser mode */ } };
  const restore = async () => { setPage("chat"); try { const { invoke } = await import("@tauri-apps/api/core"); await invoke("set_mini_mode", { mini: false }); } catch { /* browser mode */ } };
  const activeLabel = [...primaryNavigation, ...secondaryNavigation].find((item) => item.id === page)?.label ?? "JARVIS";
  return (
    <div className="jarvis-root">
      <div className="jarvis-desktop">
        <header className="window-bar" data-tauri-drag-region>
          <button className="window-brand" onClick={() => setPage("chat")}><BrainCircuit size={16} /><span>JARVIS</span><em>{t("LOCAL INTELLIGENCE")}</em></button>
          <div className="window-context"><span>{t(activeLabel)}</span><i className={status?.ollama.online ? "online" : "offline"} />{status?.ollama.online ? t("OLLAMA ONLINE") : t("OLLAMA OFFLINE")}</div>
          <div className="window-buttons">
            <button onClick={() => void mini()} title={t("Mini mode")}><Sparkles size={14} /></button>
            <button onClick={() => void minimize()} title={t("Minimize")}><Minus size={15} /></button>
            <button onClick={() => void close()} title={t("Close to tray")}><X size={15} /></button>
          </div>
        </header>
        <div className="workspace-shell">
          <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
            <div className="sidebar-head">
              <button className="new-chat" onClick={() => setPage("chat")}><Plus size={16} /><span>{t("New conversation")}</span></button>
              <button className="sidebar-collapse" onClick={() => setCollapsed(!collapsed)} title={t("Toggle sidebar")}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button>
            </div>
            <nav className="sidebar-nav">
              <NavGroup items={primaryNavigation} page={page} setPage={setPage} />
              <div className="nav-divider"><span>{t("Workspace")}</span></div>
              <NavGroup items={secondaryNavigation} page={page} setPage={setPage} />
            </nav>
            {!collapsed && (
              <section className="recent-conversation">
                <header><Activity size={13} /> {t("Current session")}</header>
                <p>{messages.at(-1)?.content || t("Ready for a new instruction.")}</p>
                <span>{t("{count} messages · local only", { count: messages.length })}</span>
              </section>
            )}
            <button className="model-switcher" onClick={openPalette}>
              <Cpu size={15} />
              <span><b>{status?.ollama.selected || status?.ollama.models?.[0]?.name || t("Select local model")}</b><small>{status?.ollama.online ? t("Ollama · ready") : t("Engine unavailable")}</small></span>
              {!collapsed && <kbd>Ctrl K</kbd>}
            </button>
          </aside>
          <main className="app-main">
            <SystemPulse />
            {children}
          </main>
        </div>
      </div>

      <div className="jarvis-mini">
        <header data-tauri-drag-region><BrainCircuit size={15} /><b>JARVIS</b><span>{t(stateMessages[state] ?? state)}</span><button onClick={() => void restore()}><Maximize2 size={14} /> {t("Expand")}</button></header>
        <div className="mini-core"><StatusOrb /><p>{messages.at(-1)?.content || t("Waiting, sir.")}</p></div>
        <div className="mini-actions"><button onClick={() => void stop()} className="danger">{t("Stop")}</button><button onClick={() => void restore()}>{t("Open chat")} <ChevronRight size={14} /></button></div>
      </div>
    </div>
  );
}

function NavGroup({ items, page, setPage }: { items: typeof primaryNavigation; page: PageId; setPage: (page: PageId) => void }) {
  const { t } = useLanguage();
  return <>{items.map((item) => <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)}><item.icon size={16} /><span>{t(item.label)}</span>{page === item.id && <ChevronLeft size={12} />}</button>)}</>;
}
