import { useLanguage } from "../i18n/LanguageContext";
import { AudioLines, Bot, CheckCircle2, CircleAlert, LoaderCircle, Mic, Radio, ShieldQuestion, Sparkles, Volume2, Wrench } from "lucide-react";
import { useJarvis } from "../store/JarvisContext";

const stateConfig = {
  IDLE: { label: "Ready", icon: CheckCircle2 },
  LISTENING: { label: "Listening", icon: Mic },
  TRANSCRIBING: { label: "Transcribing", icon: AudioLines },
  THINKING: { label: "Thinking", icon: Sparkles },
  STREAMING: { label: "Streaming", icon: Radio },
  EXECUTING: { label: "Executing tools", icon: Wrench },
  WAITING_CONFIRMATION: { label: "Awaiting approval", icon: ShieldQuestion },
  SPEAKING: { label: "Speaking", icon: Volume2 },
  PERSISTENT_ACTIVE: { label: "Agent active", icon: Bot },
  ERROR: { label: "Needs attention", icon: CircleAlert },
};

export function SystemPulse() {
  const { t } = useLanguage();
  const { state, handsFree, status } = useJarvis();
  const current = stateConfig[state] ?? { label: state, icon: LoaderCircle };
  const Icon = current.icon;
  return (
    <div className={`system-pulse pulse-${state.toLowerCase()}`} role="status" aria-live="polite">
      <span className="pulse-ring"><i /><Icon size={14} /></span>
      <div><b>{current.label}</b><small>{handsFree ? t("hands-free voice session") : status?.ollama.online ? t("local runtime") : t("offline shell")}</small></div>
    </div>
  );
}

