import { useLanguage } from "../i18n/LanguageContext";
import { ArrowUp, Mic, MicOff, Octagon, Paperclip, Power, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useJarvis } from "../store/JarvisContext";

export function ChatComposer() {
  const { t } = useLanguage();
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  const { send, state, recording, microphoneMuted, toggleRecording, setMicrophoneMuted, stop } = useJarvis();
  const busy = ["TRANSCRIBING", "THINKING", "STREAMING", "EXECUTING", "SPEAKING"].includes(state);
  const submit = () => { if (text.trim()) { void send(text); setText(""); } };
  useEffect(() => {
    if (!area.current) return;
    area.current.style.height = "auto";
    area.current.style.height = `${Math.min(area.current.scrollHeight, 150)}px`;
  }, [text]);
  return (
    <div className="composer-wrap">
      <div className="composer-modern">
        <button className="composer-tool" title={t("Local attachments coming soon")}><Paperclip size={18} /></button>
        <textarea
          ref={area}
          rows={1}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); } }}
          placeholder={busy ? t("Type to replace the current request…") : t("Message JARVIS…")}
        />
        <div className="composer-controls">
          <button className={`composer-tool power ${microphoneMuted ? "muted" : ""}`} onClick={() => void setMicrophoneMuted(!microphoneMuted)} title={microphoneMuted ? t("Enable microphone") : t("Disable microphone")}>{microphoneMuted ? <MicOff size={17} /> : <Power size={16} />}</button>
          <button className={`composer-tool ${recording ? "recording" : ""}`} onClick={() => void toggleRecording()} disabled={busy && !recording} title={t("Push-to-talk")}>{recording ? <Octagon size={17} /> : <Mic size={18} />}</button>
          {busy && !text.trim() ? <button className="composer-send stop" onClick={() => void stop()} title={t("Stop")}><Octagon size={17} /></button> : <button className="composer-send" onClick={submit} disabled={!text.trim()} title={t("Send")}><ArrowUp size={18} /></button>}
        </div>
      </div>
      <div className="composer-foot"><span><Sparkles size={11} /> {t("Local-first · respostas podem usar ferramentas autorizadas")}</span><span><kbd>Enter</kbd> {t("send")} <kbd>Shift Enter</kbd> {t("new line")}</span></div>
    </div>
  );
}

