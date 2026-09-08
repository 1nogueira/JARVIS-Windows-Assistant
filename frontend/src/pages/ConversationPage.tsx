import { useLanguage } from "../i18n/LanguageContext";
import { stateMessages } from "../i18n/messages";
import { Activity, Bot, CheckCircle2, Clock3, MessagesSquare, Radio, Shield, Sparkles, Wrench } from "lucide-react";
import { useEffect, useRef } from "react";
import { ChatComposer } from "../components/ChatComposer";
import { MessageBubble } from "../components/MessageBubble";
import { useJarvis } from "../store/JarvisContext";

export function ConversationPage() {
  const { t } = useLanguage();
  const { messages, state, error, status } = useJarvis();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: state === "STREAMING" ? "auto" : "smooth", block: "end" }); }, [messages, state]);
  const lastAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  return (
    <section className="chat-page">
      <div className="chat-column">
        <header className="chat-topbar"><div><span><Radio size={12} /> {t("LOCAL SESSION")}</span><h1>{t("Conversation")}</h1></div><div className="chat-badges"><span><Shield size={12} /> {t("Private")}</span><span><Bot size={12} /> {t("Auto routing")}</span></div></header>
        <div className="message-scroll">
          {!messages.length && <Welcome />}
          {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
          <div ref={end} />
        </div>
        {error && <div className="inline-error">{error}</div>}
        <ChatComposer />
      </div>
      <aside className="activity-panel">
        <header><Activity size={15} /><div><b>{t("System activity")}</b><span>{t("Safe operational events")}</span></div></header>
        <div className="activity-status"><i className={`state-${state.toLowerCase()}`} /><span><b>{t(stateMessages[state] ?? state)}</b><small>{status?.ollama.online ? t("Runtime connected") : t("Waiting for Ollama")}</small></span></div>
        <section><h3>{t("Current request")}</h3>{lastAssistant ? <><ActivityItem icon={Sparkles} label={t("Route")} value={t(lastAssistant.route || "pending")} /><ActivityItem icon={Bot} label={t("Agent")} value={t(lastAssistant.agent || "auto")} /><ActivityItem icon={Wrench} label={t("Tools")} value={t("{count} calls", { count: lastAssistant.actions?.length || 0 })} /><ActivityItem icon={Clock3} label={t("Latency")} value={typeof lastAssistant.metrics?.duration_ms === "number" ? `${lastAssistant.metrics.duration_ms.toFixed(0)} ms` : "—"} /></> : <p className="muted-copy">{t("No active request.")}</p>}</section>
        <section><h3>{t("Runtime guarantees")}</h3><div className="guarantee"><CheckCircle2 size={14} /><span>{t("External effects use real tool results")}</span></div><div className="guarantee"><CheckCircle2 size={14} /><span>{t("Internal protocol is never rendered")}</span></div><div className="guarantee"><CheckCircle2 size={14} /><span>{t("Telemetry stores no message content")}</span></div></section>
      </aside>
    </section>
  );
}

function Welcome() {
  const { t } = useLanguage();
  return <div className="chat-welcome"><span className="welcome-mark"><MessagesSquare size={23} /></span><small>{t("JARVIS EXPERIENCE")}</small><h2>{t("How may I help?")}</h2><p>{t("Ask a question, control Windows, or give me a multi-step task.")}</p><div><span>{t("“Explain black holes.”")}</span><span>{t("“Open Discord and VS Code.”")}</span><span>{t("“Research sodium batteries in depth.”")}</span></div></div>;
}

function ActivityItem({ icon: Icon, label, value }: { icon: typeof Bot; label: string; value: string }) { return <div className="activity-item"><Icon size={14} /><span><small>{label}</small><b>{value}</b></span></div>; }
