import { useLanguage } from "../i18n/LanguageContext";
import { useEffect, useState } from "react";
import { Bot, Check, CircleAlert, Eye, Mic2, RefreshCw, Save, Settings, Shield, Volume2 } from "lucide-react";
import { api } from "../services/api";

export function SettingsPage() {
  const { t } = useLanguage();
  const [config, setConfig] = useState<Record<string, any> | null>(null); const [checks, setChecks] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false); const [error, setError] = useState("");
  const [testingVoice, setTestingVoice] = useState(false);
  const load = () => {
    void api.settings().then((value) => { setConfig(value); setError(""); }).catch((cause) => setError(cause.message));
    void api.onboarding().then(setChecks).catch(() => undefined);
  };
  useEffect(load, []);
  const set = (section: string, key: string, value: unknown) => setConfig((current) => current ? { ...current, [section]: { ...current[section], [key]: value } } : current);
  const save = async () => {
    if (!config) return;
    try {
      const updated = await api.saveSettings(config);
      setConfig(updated);
      window.dispatchEvent(new CustomEvent("jarvis-settings-updated", { detail: updated }));
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Could not save."));
    }
  };
  const testVoice = async () => { try { setTestingVoice(true); setError(""); await api.speakLocal(t("Voice test completed. I am ready to help."), "settings-test"); } catch (cause) { setError(cause instanceof Error ? cause.message : t("Voice test failed.")); } finally { setTestingVoice(false); } };
  if (!config) return <section className="standard-page"><div className="empty-state"><RefreshCw className="spin" /> {t("Loading settings…")}</div></section>;
  return (
    <section className="standard-page page-enter settings-page">
      <div className="page-title row"><span><Settings size={18} /></span><div><h2>{t("Settings")}</h2><p>{t("Models, voice, privacy and behavior.")}</p></div><button className="primary push-right" onClick={() => void save()}><Save size={15} />{saved ? t("Saved") : t("Save")}</button></div>
      {error && <div className="inline-error">{error}</div>}
      <div className="onboarding-card"><header><div><span>{t("FIRST-RUN DIAGNOSTICS")}</span><h3>{t("{ready} of {total} components ready", { ready: Object.values(checks).filter((check: any) => check.ok ?? check.available).length, total: Object.keys(checks).length })}</h3></div><button className="secondary" onClick={load}><RefreshCw size={14} /> {t("Check")}</button></header><div className="check-grid">{Object.entries(checks).map(([name, check]: [string, any]) => { const ok = check.ok ?? check.available; return <div key={name} className={ok ? "ok" : t("missing")}>{ok ? <Check size={14} /> : <CircleAlert size={14} />}<span><b>{name}</b>{check.detail || (ok ? t("Available") : t("Setup required"))}</span></div>; })}</div></div>
      <div className="settings-grid">
        <SettingsSection icon={Bot} title={t("Local intelligence")} description={t("Ollama and independent models")}>
          <Field label={t("Ollama URL")}><input value={config.ollama.url} onChange={(event) => set("ollama", "url", event.target.value)} /></Field>
          <Field label={t("Chat model")}><input value={config.ollama.chat_model} placeholder={t("automatic detection")} onChange={(event) => set("ollama", "chat_model", event.target.value)} /></Field>
          <Field label={t("Vision model")}><input value={config.ollama.vision_model} placeholder={t("e.g. qwen2.5vl:7b")} onChange={(event) => set("ollama", "vision_model", event.target.value)} /></Field>
          <div className="field-pair"><Field label={t("Temperature")}><input type="number" min="0" max="2" step="0.05" value={config.ollama.temperature} onChange={(event) => set("ollama", "temperature", Number(event.target.value))} /></Field><Field label={t("Context")}><input type="number" min="2048" step="1024" value={config.ollama.context_size} onChange={(event) => set("ollama", "context_size", Number(event.target.value))} /></Field></div>
        </SettingsSection>
        <SettingsSection icon={Mic2} title={t("Voice")} description={t("whisper.cpp, Piper and wake word")}>
          <div className="voice-ready"><Check size={15} /><div><strong>{t("Built-in local voice")}</strong><span>{t("Whisper and Piper are configured automatically.")}</span></div></div>
          <Toggle label={t("Speak responses aloud")} checked={config.voice.enabled} onChange={(value) => set("voice", "enabled", value)} />
          <Field label={t("Speech rate")}><input type="number" min="0.5" max="2" step="0.1" value={config.voice.speech_rate} onChange={(event) => set("voice", "speech_rate", Number(event.target.value))} /></Field>
          <Field label={t("Wake word")}><input value={config.voice.wake_word} onChange={(event) => set("voice", "wake_word", event.target.value)} /></Field>
          <Field label={t("Sensibilidade da palavra-chave")}><input type="number" min="0.05" max="0.9" step="0.05" value={config.voice.sensitivity} onChange={(event) => set("voice", "sensitivity", Number(event.target.value))} /></Field>
          <Toggle label={t("Wake word ativa")} checked={config.voice.wake_word_enabled} onChange={(value) => set("voice", "wake_word_enabled", value)} />
          <Toggle label={t("Hands-free conversation")} checked={config.voice.hands_free ?? true} onChange={(value) => set("voice", "hands_free", value)} />
          <Field label={t("Special phrase")}><input value={config.voice.special_wake_phrase ?? ""} onChange={(event) => set("voice", "special_wake_phrase", event.target.value)} /></Field>
          <Field label={t("Special phrase music")}><input value={config.voice.special_wake_music ?? ""} onChange={(event) => set("voice", "special_wake_music", event.target.value)} /></Field>
          <Field label={t("Special greeting")}><input value={config.voice.special_wake_greeting ?? ""} onChange={(event) => set("voice", "special_wake_greeting", event.target.value)} /></Field>
          <Field label={t("Default city")}><input value={config.user?.default_location ?? ""} onChange={(event) => set("user", "default_location", event.target.value)} /></Field>
          <button className="secondary" disabled={testingVoice} onClick={() => void testVoice()}><Volume2 size={14} />{testingVoice ? t("Speaking…") : t("Test voice now")}</button>
        </SettingsSection>
        <SettingsSection icon={Eye} title={t("Interface")} description={t("Desktop behavior")}>
          <Field label={t("Language")}><select value={config.user?.language ?? "pt-BR"} onChange={(event) => set("user", "language", event.target.value)}><option value="pt-BR">Português (Brasil)</option><option value="en-US">English (United States)</option></select></Field>
          <Toggle label={t("Always on top")} checked={config.interface.always_on_top} onChange={(value) => set("interface", "always_on_top", value)} />
          <Toggle label={t("Minimize to tray")} checked={config.interface.minimize_to_tray} onChange={(value) => set("interface", "minimize_to_tray", value)} />
          <Toggle label={t("Start with Windows")} checked={config.interface.start_with_windows} onChange={(value) => set("interface", "start_with_windows", value)} />
          <Toggle label={t("Animations")} checked={config.interface.animations} onChange={(value) => set("interface", "animations", value)} />
          <Toggle label={t("Quiet proactive alerts")} checked={config.proactive?.enabled ?? false} onChange={(value) => set("proactive", "enabled", value)} />
          <Field label={t("Files and screenshots folder")}><input value={config.storage?.artifact_directory ?? ""} onChange={(event) => set("storage", "artifact_directory", event.target.value)} /></Field>
        </SettingsSection>
        <SettingsSection icon={Shield} title={t("Privacy")} description={t("Data under your control")}>
          <Toggle label={t("Local memory")} checked={config.privacy.memory_enabled} onChange={(value) => set("privacy", "memory_enabled", value)} />
          <Toggle label={t("Conversation history")} checked={config.privacy.history_enabled} onChange={(value) => set("privacy", "history_enabled", value)} />
          <Toggle label={t("Structured logs")} checked={config.privacy.structured_logs} onChange={(value) => set("privacy", "structured_logs", value)} />
          <Toggle label={t("Keep screenshots")} checked={config.privacy.keep_screenshots} onChange={(value) => set("privacy", "keep_screenshots", value)} />
        </SettingsSection>
      </div>
    </section>
  );
}

function SettingsSection({ icon: Icon, title, description, children }: { icon: typeof Bot; title: string; description: string; children: React.ReactNode }) { return <section className="settings-section"><header><span><Icon size={17} /></span><div><h3>{title}</h3><p>{description}</p></div></header><div className="settings-content">{children}</div></section>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="field"><span>{label}</span>{children}</label>; }
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) { return <label className="toggle-field"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>; }
