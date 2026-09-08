import { useLanguage } from "../i18n/LanguageContext";
import { Mic, MicOff, Square } from "lucide-react";
import type { CSSProperties } from "react";
import { useJarvis } from "../store/JarvisContext";

const stateLabels = {
  IDLE: "Ready", LISTENING: "Listening", TRANSCRIBING: "Transcribing", THINKING: "Thinking",
  STREAMING: "Streaming", EXECUTING: "Executing tools", WAITING_CONFIRMATION: "Awaiting approval",
  SPEAKING: "Speaking", PERSISTENT_ACTIVE: "Agent active", ERROR: "Needs attention",
};

export function StatusOrb() {
  const { t } = useLanguage();
  const { state, status, audioLevel, recording, handsFree, microphoneMuted, toggleRecording, stop } = useJarvis();
  const busy = ["TRANSCRIBING", "THINKING", "STREAMING", "EXECUTING", "SPEAKING"].includes(state);
  return (
    <div className="orb-stage">
      <div className={`orb-radar state-${state.toLowerCase()}`} />
      <button
        className={`orb state-${state.toLowerCase()}`}
        aria-label={microphoneMuted ? t("Enable JARVIS microphone") : recording && handsFree ? t("End conversation") : recording ? t("Finish speaking") : busy ? t("Stop task") : t("Talk to JARVIS")}
        onClick={() => void (busy ? stop() : toggleRecording())}
        style={{ "--audio-glow": `${22 + audioLevel * 16}px` } as CSSProperties}
      >
        <span className="orb-shell orb-shell-one" />
        <span className="orb-shell orb-shell-two" />
        <span className="orb-core">{microphoneMuted ? <MicOff size={27} /> : recording || busy ? <Square size={24} /> : <Mic size={27} />}</span>
      </button>
      <div className="orb-state"><span />{microphoneMuted ? t("MICROPHONE OFF") : state === "IDLE" && status?.voice.wakeword.running ? t("SAY JARVIS") : handsFree && state === "LISTENING" ? t("CONVERSATION ACTIVE") : t(stateLabels[state])}</div>
    </div>
  );
}
