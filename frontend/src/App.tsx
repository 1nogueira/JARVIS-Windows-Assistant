import { useLanguage } from "./i18n/LanguageContext";
import { lazy, Suspense, useEffect, useState } from "react";
import { CommandPalette } from "./components/CommandPalette";
import { Shell, type PageId } from "./components/Shell";
import { ConversationPage } from "./pages/ConversationPage";
import { useJarvis } from "./store/JarvisContext";

const AgentsPage = lazy(() => import("./pages/AgentsPage").then((module) => ({ default: module.AgentsPage })));
const DashboardPage = lazy(() => import("./pages/DashboardPage").then((module) => ({ default: module.DashboardPage })));
const DataSourcesPage = lazy(() => import("./pages/DataSourcesPage").then((module) => ({ default: module.DataSourcesPage })));
const LogsPage = lazy(() => import("./pages/LogsPage").then((module) => ({ default: module.LogsPage })));
const MemoryPage = lazy(() => import("./pages/MemoryPage").then((module) => ({ default: module.MemoryPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));
const SystemPage = lazy(() => import("./pages/SystemPage").then((module) => ({ default: module.SystemPage })));
const ToolsPage = lazy(() => import("./pages/ToolsPage").then((module) => ({ default: module.ToolsPage })));

export default function App() {
  const { t } = useLanguage();
  const [page, setPage] = useState<PageId>("chat");
  const [palette, setPalette] = useState(false);
  const { toggleRecording } = useJarvis();
  useEffect(() => {
    const disposers: Array<() => void> = [];
    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      disposers.push(await listen("shortcut://push-to-talk", () => void toggleRecording()));
      disposers.push(await listen("navigate://home", () => setPage("chat")));
      disposers.push(await listen("navigate://settings", () => setPage("settings")));
    }).catch(() => undefined);
    const keyboard = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setPalette((open) => !open); }
      if (event.ctrlKey && event.code === "Space") { event.preventDefault(); void toggleRecording(); }
    };
    window.addEventListener("keydown", keyboard);
    return () => { disposers.forEach((dispose) => dispose()); window.removeEventListener("keydown", keyboard); };
  }, [toggleRecording]);

  const content = {
    chat: <ConversationPage />,
    dashboard: <DashboardPage />,
    agents: <AgentsPage />,
    "data-sources": <DataSourcesPage />,
    memory: <MemoryPage />,
    tools: <ToolsPage />,
    logs: <LogsPage />,
    system: <SystemPage />,
    settings: <SettingsPage />,
  }[page];

  return (
    <>
      <Shell page={page} setPage={setPage} openPalette={() => setPalette(true)}>
        <Suspense fallback={<div className="page-loading" role="status">{t("Loading local module…")}</div>}>
          {content}
        </Suspense>
      </Shell>
      {palette && <CommandPalette close={() => setPalette(false)} navigate={setPage} />}
    </>
  );
}
