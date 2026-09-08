import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, sessionToken } from "../services/api";
import type { AssistantState, BackendStatus, ChatMessage } from "../types";
import { translateCurrent } from "../i18n/messages";

interface JarvisContextValue {
  state: AssistantState;
  status: BackendStatus | null;
  messages: ChatMessage[];
  error: string | null;
  audioLevel: number;
  recording: boolean;
  handsFree: boolean;
  microphoneMuted: boolean;
  send: (text: string) => Promise<void>;
  confirm: (message: ChatMessage, approved: boolean) => Promise<void>;
  stop: () => Promise<void>;
  toggleRecording: () => Promise<void>;
  setMicrophoneMuted: (muted: boolean) => Promise<void>;
  refreshStatus: () => Promise<boolean>;
}

const JarvisContext = createContext<JarvisContextValue | null>(null);
const id = () => crypto.randomUUID();

export function JarvisProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AssistantState>("IDLE");
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);
  const [recording, setRecording] = useState(false);
  const [handsFree, setHandsFree] = useState(false);
  const [microphoneMuted, setMicrophoneMutedState] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const analyserFrame = useRef<number>();
  const maxRecordingTimer = useRef<number>();
  const audioContext = useRef<AudioContext>();
  const currentRequest = useRef<string>();
  const voiceAbort = useRef<AbortController>();
  const streamAbort = useRef<AbortController>();
  const speechCancelled = useRef(false);
  const handsFreeRef = useRef(false);
  const microphoneMutedRef = useRef(false);
  const themeActiveRef = useRef(false);
  const handsFreeEnabled = useRef(true);
  const specialWakePhrase = useRef("acorda crianca o papai chegou");
  const specialWakeGreeting = useRef(translateCurrent("Welcome, sir. How are things? What are we working on today?"));
  const stopPhrases = useRef(["obrigado jarvis", "pode descansar", "encerrar conversa", "ate mais jarvis"]);
  const backendRetryCount = useRef(0);
  const recordingRef = useRef(false);
  const discardRecording = useRef(false);
  const bargeInStream = useRef<MediaStream>();
  const bargeInContext = useRef<AudioContext>();
  const bargeInFrame = useRef<number>();
  const bargeInTriggered = useRef(false);
  const startRecordingRef = useRef<() => Promise<void>>(async () => undefined);
  const sendRef = useRef<(text: string) => Promise<void>>(async () => undefined);
  const pendingConfirmation = useRef<{
    messageId: string;
    confirmationId: string;
    requestId: string;
  } | null>(null);
  const turnGeneration = useRef(0);

  const applyRuntimeSettings = useCallback((config: Record<string, any>) => {
    handsFreeEnabled.current = config.voice?.hands_free ?? true;
    specialWakePhrase.current = normalizeSpeech(config.voice?.special_wake_phrase ?? "acorda criança o papai chegou");
    specialWakeGreeting.current = config.voice?.special_wake_greeting
      ?? translateCurrent("Welcome, sir. How are things? What are we working on today?");
    if (Array.isArray(config.voice?.stop_phrases)) {
      stopPhrases.current = config.voice.stop_phrases.map((phrase: string) => normalizeSpeech(phrase));
    }
    document.documentElement.dataset.animations = config.interface?.animations === false ? "off" : "on";
    void import("@tauri-apps/api/core").then(async ({ invoke }) => {
      await invoke("set_start_with_windows", { enabled: Boolean(config.interface?.start_with_windows) });
      await invoke("apply_interface_settings", {
        alwaysOnTop: Boolean(config.interface?.always_on_top),
        minimizeToTray: config.interface?.minimize_to_tray !== false,
      });
    }).catch(() => undefined);
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const nextStatus = await api.status();
      setStatus(nextStatus);
      const muted = Boolean(nextStatus.voice.wakeword.microphone_muted);
      microphoneMutedRef.current = muted;
      setMicrophoneMutedState(muted);
      if (nextStatus.voice.theme) {
        themeActiveRef.current = Boolean(nextStatus.voice.theme.playing || nextStatus.voice.theme.paused);
      }
      if (backendRetryCount.current > 0) setError(null);
      backendRetryCount.current = 0;
      return true;
    } catch {
      setStatus(null);
      backendRetryCount.current += 1;
      setError(
        backendRetryCount.current < 15
          ? translateCurrent("Local backend is starting. Please wait a few seconds...")
          : translateCurrent("Local backend is unavailable. Restart JARVIS."),
      );
      return false;
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    const poll = async () => {
      const online = await refreshStatus();
      if (!disposed) timer = window.setTimeout(poll, online ? 15_000 : 2_000);
    };
    void poll();
    return () => { disposed = true; if (timer) window.clearTimeout(timer); };
  }, [refreshStatus]);

  useEffect(() => {
    const refresh = () => { void refreshStatus(); };
    window.addEventListener("jarvis-model-selected", refresh);
    return () => window.removeEventListener("jarvis-model-selected", refresh);
  }, [refreshStatus]);

  useEffect(() => {
    const update = (event: Event) => applyRuntimeSettings((event as CustomEvent<Record<string, any>>).detail);
    window.addEventListener("jarvis-settings-updated", update);
    void api.settings().then(applyRuntimeSettings).catch(() => undefined);
    return () => window.removeEventListener("jarvis-settings-updated", update);
  }, [applyRuntimeSettings]);

  useEffect(() => {
    if (!status?.online || sessionStorage.getItem("jarvis-welcomed") === "true") return;
    sessionStorage.setItem("jarvis-welcomed", "true");
    const previousVersion = localStorage.getItem("jarvis-last-version");
    localStorage.setItem("jarvis-last-version", status.version);
    const message = previousVersion === status.version
      ? translateCurrent("Welcome back, sir.")
      : translateCurrent("Welcome back, sir. I was updated to version {version}. I am ready to help.", { version: status.version });
    void api.speakLocal(message, "startup-welcome").catch(() => undefined);
  }, [status]);

  const stopBargeInMonitor = useCallback(() => {
    if (bargeInFrame.current) cancelAnimationFrame(bargeInFrame.current);
    bargeInFrame.current = undefined;
    bargeInStream.current?.getTracks().forEach((track) => track.stop());
    bargeInStream.current = undefined;
    void bargeInContext.current?.close().catch(() => undefined);
    bargeInContext.current = undefined;
  }, []);

  const startBargeInMonitor = useCallback(async () => {
    if (!handsFreeRef.current || recordingRef.current || bargeInStream.current) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const context = new AudioContext();
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.35;
      context.createMediaStreamSource(stream).connect(analyser);
      bargeInStream.current = stream;
      bargeInContext.current = context;
      bargeInTriggered.current = false;
      const values = new Uint8Array(analyser.fftSize);
      const startedAt = performance.now();
      let noiseFloor = 0.012;
      let voiceFrames = 0;
      const sample = () => {
        if (!bargeInStream.current || bargeInTriggered.current) return;
        analyser.getByteTimeDomainData(values);
        const level = Math.sqrt(values.reduce((sum, value) => {
          const centered = (value - 128) / 128;
          return sum + centered * centered;
        }, 0) / values.length);
        const elapsed = performance.now() - startedAt;
        if (elapsed < 650) noiseFloor = Math.min(0.04, noiseFloor * 0.94 + level * 0.06);
        const threshold = Math.max(0.045, Math.min(0.12, noiseFloor * 3.2 + 0.012));
        voiceFrames = level > threshold ? voiceFrames + 1 : Math.max(0, voiceFrames - 1);
        if (elapsed > 650 && voiceFrames >= 7) {
          bargeInTriggered.current = true;
          speechCancelled.current = true;
          stopBargeInMonitor();
          void (async () => {
            await api.cancel(currentRequest.current).catch(() => undefined);
            setState("IDLE");
            await startRecordingRef.current();
          })();
          return;
        }
        bargeInFrame.current = requestAnimationFrame(sample);
      };
      sample();
    } catch {
      stopBargeInMonitor();
    }
  }, [stopBargeInMonitor]);

  const speak = useCallback(async (text: string, requestId: string) => {
    if (!status?.voice.tts.available) {
      setError(translateCurrent("Piper voice is unavailable."));
      return;
    }
    speechCancelled.current = false;
    setState("SPEAKING");
    try {
      if (handsFreeRef.current) void startBargeInMonitor();
      await api.speakLocal(text, requestId);
    } catch (cause) {
      if (!speechCancelled.current) {
        setError(cause instanceof Error ? cause.message : translateCurrent("Could not play voice."));
      }
    } finally {
      stopBargeInMonitor();
      if (!recordingRef.current) setState("IDLE");
      if (handsFreeRef.current && !speechCancelled.current) {
        window.setTimeout(() => void startRecordingRef.current(), 450);
      }
    }
  }, [startBargeInMonitor, status, stopBargeInMonitor]);

  const setMicrophoneMuted = useCallback(async (muted: boolean) => {
    setError(null);
    if (muted && recorder.current?.state === "recording") {
      discardRecording.current = true;
      recordingRef.current = false;
      setRecording(false);
      if (maxRecordingTimer.current) window.clearTimeout(maxRecordingTimer.current);
      recorder.current.stop();
    }
    handsFreeRef.current = false;
    setHandsFree(false);
    await api.setMicrophoneMuted(muted);
    microphoneMutedRef.current = muted;
    setMicrophoneMutedState(muted);
    setState("IDLE");
    const message = muted
      ? translateCurrent("JARVIS microphone disabled, sir. To enable it, say: Jarvis, enable microphone.")
      : translateCurrent("JARVIS microphone enabled, sir.");
    setMessages((items) => [...items, { id: id(), role: "assistant", content: message }]);
    await speak(message, muted ? "microphone-muted" : "microphone-unmuted");
  }, [speak]);

  const resolveConfirmation = useCallback(async (
    target: { messageId: string; confirmationId: string; requestId: string },
    approved: boolean,
  ) => {
    pendingConfirmation.current = null;
    currentRequest.current = target.requestId;
    setState("EXECUTING");
    try {
      const result = await api.confirm(target.confirmationId, approved);
      if (result.request_id && result.state && result.actions) {
        currentRequest.current = result.request_id;
        const messageId = id();
        const assistant: ChatMessage = {
          id: messageId,
          requestId: result.request_id,
          role: "assistant",
          content: result.message ?? translateCurrent("Completed."),
          actions: result.actions as ChatMessage["actions"],
          sources: result.sources,
          confirmationId: result.confirmation_id,
          confirmationPreview: result.confirmation_preview,
        };
        if (result.confirmation_id) {
          pendingConfirmation.current = {
            messageId,
            confirmationId: result.confirmation_id,
            requestId: result.request_id,
          };
        }
        setMessages((items) => items
          .map((item) => item.id === target.messageId ? { ...item, confirmationId: null, confirmationPreview: null } : item)
          .concat(assistant));
        setState(result.state as AssistantState);
        await speak(assistant.content, result.request_id);
        return;
      }
      const content = approved ? confirmationResultText(result) : translateCurrent("Action cancelled, sir.");
      setMessages((items) => items
        .map((item) => item.id === target.messageId ? { ...item, confirmationId: null, confirmationPreview: null } : item)
        .concat({ id: id(), role: "assistant", content }));
      await speak(content, `confirmation-${target.confirmationId}`);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : translateCurrent("Invalid confirmation.");
      setError(message);
      setState("ERROR");
    } finally {
      if (currentRequest.current === target.requestId) currentRequest.current = undefined;
    }
  }, [speak]);

  const send = useCallback(async (text: string) => {
    const clean = text.trim();
    if (!clean) return;
    const normalized = normalizeSpeech(clean);
    if (matchesSpecialWake(normalized, specialWakePhrase.current)) {
      setMessages((items) => [...items, { id: id(), role: "user", content: clean }]);
      setError(null);
      setState("EXECUTING");
      const continueConversation = handsFreeEnabled.current;
      handsFreeRef.current = continueConversation;
      setHandsFree(continueConversation);
      await api.wakewordAction("pause").catch(() => undefined);
      let musicStarted = false;
      try {
        await api.playTheme();
        musicStarted = true;
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : translateCurrent("Could not start theme music."));
      }
      themeActiveRef.current = musicStarted;
      const greeting = specialWakeGreeting.current;
      setMessages((items) => [...items, { id: id(), role: "assistant", content: greeting }]);
      await speak(greeting, "special-wake-command");
      if (!continueConversation) await api.wakewordAction("resume").catch(() => undefined);
      return;
    }
    const microphoneCommand = parseMicrophoneCommand(normalized);
    if (microphoneCommand) {
      setMessages((items) => [...items, { id: id(), role: "user", content: clean }]);
      try {
        await setMicrophoneMuted(microphoneCommand === "mute");
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : translateCurrent("Could not change the microphone."));
        setState("ERROR");
      }
      return;
    }
    const musicCommand = parseMusicCommand(normalized, themeActiveRef.current);
    if (musicCommand) {
      setMessages((items) => [...items, { id: id(), role: "user", content: clean }]);
      setState("EXECUTING");
      try {
        const handlers = {
          stop: api.stopTheme,
          pause: api.pauseTheme,
          resume: api.resumeTheme,
          restart: api.restartTheme,
        };
        const result = await handlers[musicCommand]();
        themeActiveRef.current = musicCommand !== "stop" && Boolean(result.playing || result.paused);
        const answers = {
          stop: translateCurrent("Music stopped, sir."),
          pause: translateCurrent("Music paused, sir."),
          resume: translateCurrent("Resuming music, sir."),
          restart: translateCurrent("Restarting music, sir."),
        };
        const answer = answers[musicCommand];
        setMessages((items) => [...items, { id: id(), role: "assistant", content: answer }]);
        await speak(answer, `music-${musicCommand}`);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : translateCurrent("Could not control music."));
        setState("ERROR");
      }
      return;
    }
    const pending = pendingConfirmation.current;
    if (pending && isPositiveConfirmation(normalized)) {
      setMessages((items) => [...items, { id: id(), role: "user", content: clean }]);
      await resolveConfirmation(pending, true);
      return;
    }
    if (pending && isNegativeConfirmation(normalized)) {
      setMessages((items) => [...items, { id: id(), role: "user", content: clean }]);
      await resolveConfirmation(pending, false);
      return;
    }
    if (pending) {
      pendingConfirmation.current = null;
      await api.confirm(pending.confirmationId, false).catch(() => undefined);
      setMessages((items) => items.map((item) => item.id === pending.messageId ? { ...item, confirmationId: null } : item));
    }
    const turn = ++turnGeneration.current;
    speechCancelled.current = true;
    stopBargeInMonitor();
    voiceAbort.current?.abort();
    await api.cancel().catch(() => undefined);
    if (turn !== turnGeneration.current) return;
    const requestId = id();
    currentRequest.current = requestId;
    const messageId = id();
    setMessages((items) => [
      ...items,
      { id: id(), role: "user", content: clean },
      { id: messageId, requestId, role: "assistant", content: "", streaming: true },
    ]);
    setState("THINKING");
    setError(null);
    const controller = new AbortController();
    streamAbort.current = controller;
    try {
      let streamError = "";
      let finalResponse: Parameters<Parameters<typeof api.chatStream>[3]>[0]["response"];
      await api.chatStream(clean, requestId, "default", (event) => {
        if (turn !== turnGeneration.current) return;
        if (event.type === "route" && event.route) {
          setMessages((items) => items.map((item) => item.id === messageId
            ? { ...item, route: event.route?.mode, agent: event.route?.agent }
            : item));
        }
        if (event.type === "token" && event.content) {
          setState("STREAMING");
          setMessages((items) => items.map((item) => item.id === messageId
            ? { ...item, content: item.content + event.content, streaming: true }
            : item));
        }
        if (event.type === "complete" && event.response) finalResponse = event.response;
        if (event.type === "error") streamError = event.detail || translateCurrent("Streaming failed.");
      }, controller.signal);
      if (turn !== turnGeneration.current) return;
      if (streamError) throw new Error(streamError);
      if (!finalResponse) throw new Error("O backend encerrou o streaming sem resposta final.");
      const result = finalResponse;
      currentRequest.current = result.request_id;
      const assistant: ChatMessage = {
        id: messageId,
        requestId: result.request_id,
        role: "assistant",
        content: result.display_text || result.message,
        speechText: result.speech_text,
        streaming: false,
        route: result.route,
        agent: result.agent,
        model: result.model,
        engine: result.engine,
        metrics: result.metrics,
        actions: result.actions as ChatMessage["actions"],
        sources: result.sources,
        confirmationId: result.confirmation_id,
        confirmationPreview: result.confirmation_preview,
      };
      if (result.confirmation_id) {
        pendingConfirmation.current = {
          messageId,
          confirmationId: result.confirmation_id,
          requestId: result.request_id,
        };
      }
      setMessages((items) => items.map((item) => item.id === messageId ? assistant : item));
      setState(result.confirmation_id ? "WAITING_CONFIRMATION" : result.state as AssistantState);
      if (result.state !== "ERROR" && result.speech_text) void speak(result.speech_text, result.request_id);
    } catch (cause) {
      if (turn !== turnGeneration.current) return;
      if (cause instanceof DOMException && cause.name === "AbortError") {
        setMessages((items) => items.map((item) => item.id === messageId
          ? { ...item, content: item.content || translateCurrent("Request cancelled, sir."), streaming: false }
          : item));
        setState("IDLE");
        return;
      }
      const message = cause instanceof Error ? cause.message : translateCurrent("Could not complete the request.");
      setError(message);
      setState("ERROR");
      setMessages((items) => items.map((item) => item.id === messageId
        ? { ...item, content: message, streaming: false }
        : item));
    } finally {
      if (streamAbort.current === controller) streamAbort.current = undefined;
      if (currentRequest.current === requestId) currentRequest.current = undefined;
    }
  }, [resolveConfirmation, setMicrophoneMuted, speak, stopBargeInMonitor]);

  useEffect(() => { sendRef.current = send; }, [send]);

  const finishRecording = useCallback(() => {
    const mediaRecorder = recorder.current;
    if (!mediaRecorder || mediaRecorder.state !== "recording") return;
    recordingRef.current = false;
    setRecording(false);
    setState("TRANSCRIBING");
    if (maxRecordingTimer.current) window.clearTimeout(maxRecordingTimer.current);
    mediaRecorder.stop();
  }, []);

  const stop = useCallback(async () => {
    turnGeneration.current += 1;
    const pending = pendingConfirmation.current;
    pendingConfirmation.current = null;
    if (pending) {
      await api.confirm(pending.confirmationId, false).catch(() => undefined);
      setMessages((items) => items.map((item) => item.id === pending.messageId ? { ...item, confirmationId: null } : item));
    }
    speechCancelled.current = true;
    streamAbort.current?.abort();
    streamAbort.current = undefined;
    stopBargeInMonitor();
    voiceAbort.current?.abort();
    const wasHandsFree = handsFreeRef.current;
    handsFreeRef.current = false;
    setHandsFree(false);
    if (recorder.current?.state === "recording") {
      discardRecording.current = true;
      recordingRef.current = false;
      setRecording(false);
      if (maxRecordingTimer.current) window.clearTimeout(maxRecordingTimer.current);
      recorder.current.stop();
    }
    setState("IDLE");
    setRecording(false);
    await api.cancel(currentRequest.current).catch(() => undefined);
    if (themeActiveRef.current) {
      await api.stopTheme().catch(() => undefined);
      themeActiveRef.current = false;
    }
    if (wasHandsFree) await api.wakewordAction("resume").catch(() => undefined);
  }, [stopBargeInMonitor]);

  const confirm = useCallback(async (message: ChatMessage, approved: boolean) => {
    if (!message.confirmationId) return;
    const pending = pendingConfirmation.current;
    const requestId = pending?.confirmationId === message.confirmationId
      ? pending.requestId
      : message.requestId;
    if (!requestId) return;
    await resolveConfirmation({ messageId: message.id, confirmationId: message.confirmationId, requestId }, approved);
  }, [resolveConfirmation]);

  const startRecording = useCallback(async () => {
    if (recordingRef.current) return;
    if (microphoneMutedRef.current) {
      setError(translateCurrent("JARVIS microphone is disabled. Say: Jarvis, enable microphone."));
      setState("IDLE");
      return;
    }
    if (!status?.voice.stt.available) {
      setError(translateCurrent("Voice transcription is unavailable."));
      return;
    }
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const preferredMime = ["audio/webm;codecs=opus", "audio/webm"].find((mime) => MediaRecorder.isTypeSupported(mime));
      const mediaRecorder = preferredMime ? new MediaRecorder(stream, { mimeType: preferredMime }) : new MediaRecorder(stream);
      recorder.current = mediaRecorder;
      chunks.current = [];
      const controller = new AbortController();
      voiceAbort.current = controller;
      const startedAt = performance.now();
      let heardSpeech = false;
      let lastSpeechAt = startedAt;
      let noiseFloor = 0.012;
      let speechFrames = 0;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size) chunks.current.push(event.data);
      };
      mediaRecorder.onstop = async () => {
        const shouldDiscard = discardRecording.current;
        discardRecording.current = false;
        stream.getTracks().forEach((track) => track.stop());
        if (analyserFrame.current) cancelAnimationFrame(analyserFrame.current);
        if (maxRecordingTimer.current) window.clearTimeout(maxRecordingTimer.current);
        await audioContext.current?.close().catch(() => undefined);
        audioContext.current = undefined;
        setAudioLevel(0);
        setRecording(false);
        recordingRef.current = false;
        if (shouldDiscard) {
          setState("IDLE");
          if (recorder.current === mediaRecorder) recorder.current = null;
          return;
        }
        setState("TRANSCRIBING");
        try {
          const blob = new Blob(chunks.current, { type: mediaRecorder.mimeType || "audio/webm" });
          if (!blob.size) throw new Error(translateCurrent("No audio was captured."));
          const result = await api.transcribe(blob, controller.signal);
          if (!result.text.trim()) throw new Error(translateCurrent("I could not understand the speech. Try again closer to the microphone."));
          const normalized = normalizeSpeech(result.text);
          if (isNonSpeechTranscript(normalized)) {
            setState("IDLE");
            if (handsFreeRef.current) {
              handsFreeRef.current = false;
              setHandsFree(false);
              await api.wakewordAction("resume").catch(() => undefined);
            }
            return;
          }
          if (handsFreeRef.current && stopPhrases.current.some((phrase) => normalized.includes(phrase))) {
            const goodbye = translateCurrent("See you later, sir. Say Jarvis when you need me.");
            setMessages((items) => items.concat(
              { id: id(), role: "user", content: result.text },
              { id: id(), role: "assistant", content: goodbye },
            ));
            handsFreeRef.current = false;
            setHandsFree(false);
            setState("SPEAKING");
            await api.speakLocal(goodbye, "hands-free-stop");
            await api.wakewordAction("resume");
            setState("IDLE");
            return;
          }
          await send(result.text);
        } catch (cause) {
          if (cause instanceof DOMException && cause.name === "AbortError") {
            setState("IDLE");
          } else {
            setError(cause instanceof Error ? cause.message : translateCurrent("Transcription failed."));
            setState("ERROR");
            if (handsFreeRef.current) {
              handsFreeRef.current = false;
              setHandsFree(false);
              void api.wakewordAction("resume").catch(() => undefined);
            }
          }
        } finally {
          if (recorder.current === mediaRecorder) recorder.current = null;
        }
      };

      const context = new AudioContext();
      audioContext.current = context;
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.4;
      source.connect(analyser);
      const values = new Uint8Array(analyser.fftSize);
      const sample = () => {
        if (mediaRecorder.state !== "recording") return;
        analyser.getByteTimeDomainData(values);
        const level = Math.sqrt(values.reduce((sum, value) => {
          const centered = (value - 128) / 128;
          return sum + centered * centered;
        }, 0) / values.length);
        setAudioLevel(level);
        const now = performance.now();
        if (!heardSpeech) noiseFloor = Math.min(0.04, noiseFloor * 0.98 + level * 0.02);
        const speechThreshold = Math.max(0.022, Math.min(0.1, noiseFloor * 2.3 + 0.006));
        if (level > speechThreshold) {
          speechFrames += 1;
          if (speechFrames >= 3) {
            heardSpeech = true;
            lastSpeechAt = now;
          }
        } else {
          speechFrames = Math.max(0, speechFrames - 1);
        }
        if (heardSpeech && now - startedAt > 700 && now - lastSpeechAt > 1_500) {
          finishRecording();
          return;
        }
        analyserFrame.current = requestAnimationFrame(sample);
      };

      mediaRecorder.start(250);
      recordingRef.current = true;
      setRecording(true);
      setState("LISTENING");
      sample();
      maxRecordingTimer.current = window.setTimeout(() => {
        if (mediaRecorder.state !== "recording") return;
        if (handsFreeRef.current && !heardSpeech) {
          discardRecording.current = true;
          recordingRef.current = false;
          setRecording(false);
          handsFreeRef.current = false;
          setHandsFree(false);
          mediaRecorder.stop();
          void api.wakewordAction("resume").catch(() => undefined);
          return;
        }
        finishRecording();
      }, 30_000);
    } catch (cause) {
      recordingRef.current = false;
      setRecording(false);
      setState("ERROR");
      setError(cause instanceof Error ? `${translateCurrent("Could not access the microphone")}: ${cause.message}` : translateCurrent("Could not access the microphone."));
      if (handsFreeRef.current) {
        handsFreeRef.current = false;
        setHandsFree(false);
        void api.wakewordAction("resume").catch(() => undefined);
      }
    }
  }, [finishRecording, send, status]);

  useEffect(() => { startRecordingRef.current = startRecording; }, [startRecording]);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | undefined;
    let reconnectTimer: number | undefined;
    const connect = () => {
      if (disposed) return;
      void sessionToken().then((token) => {
        if (disposed) return;
        socket = new WebSocket(
          api.baseUrl.replace(/^http/, "ws") + "/ws/events",
          ["jarvis-events", `jarvis-session.${token}`],
        );
      socket.onmessage = ({ data }) => {
        const event = JSON.parse(data) as { event: string; data?: Record<string, unknown> };
        const map: Record<string, AssistantState> = {
          "speech.started": "TRANSCRIBING",
          "request.routed": "THINKING",
          "inference.started": "THINKING",
          "inference.first_token": "STREAMING",
          "tool.started": "EXECUTING",
          "confirmation.required": "WAITING_CONFIRMATION",
          "tool.confirmation_required": "WAITING_CONFIRMATION",
          "persistent_agent.started": "PERSISTENT_ACTIVE",
          "persistent_agent.completed": "IDLE",
          "persistent_agent.failed": "ERROR",
          "response.completed": "IDLE",
          "request.cancelled": "IDLE",
          "request.failed": "ERROR",
          "agent.thinking": "THINKING",
          "agent.executing": "EXECUTING",
          "tts.started": "SPEAKING",
          "assistant.response": "IDLE",
          "tts.completed": "IDLE",
          "agent.error": "ERROR",
          "agent.cancelled": "IDLE",
          "wakeword.resumed": "IDLE",
        };
        const nextState = map[event.event];
        if (nextState && !(recordingRef.current && nextState === "IDLE")) {
          setState(nextState);
        }
        if (event.event === "reminder.due") {
          const message = String(event.data?.message ?? translateCurrent("Sir, you have a reminder."));
          setMessages((items) => [...items, { id: id(), role: "assistant", content: message }]);
        }
        if (event.event === "proactive.alert") {
          const message = `${translateCurrent("Sir")}, ${String(event.data?.message ?? translateCurrent("there is a system alert"))}.`;
          setMessages((items) => [...items, { id: id(), role: "assistant", content: message }]);
        }
        if (event.event === "wakeword.special" && !recordingRef.current) {
          const continueConversation = handsFreeEnabled.current;
          handsFreeRef.current = continueConversation;
          setHandsFree(continueConversation);
          setError(null);
          void (async () => {
            const greeting = String(
              event.data?.greeting
                ?? translateCurrent("Welcome, sir. How are things? What are we working on today?"),
            );
            setMessages((items) => [...items, { id: id(), role: "assistant", content: greeting }]);
            setState("SPEAKING");
            let musicStarted = false;
            await api.playTheme().then(() => { musicStarted = true; }).catch((cause) => {
              setError(cause instanceof Error ? cause.message : translateCurrent("Could not play theme music."));
            });
            themeActiveRef.current = musicStarted;
            await api.speakLocal(greeting, "special-wake-greeting").catch(() => undefined);
            if (continueConversation) {
              await new Promise((resolve) => window.setTimeout(resolve, 250));
              await startRecordingRef.current();
            } else {
              await api.wakewordAction("resume").catch(() => undefined);
              setState("IDLE");
            }
          })();
        }
        if (event.event === "wakeword.error") {
          const detail = String(event.data?.error ?? translateCurrent("Voice listener failed."));
          setError(`${translateCurrent("Voice listener is recovering")}: ${detail}`);
        }
        if (event.event === "wakeword.detected" && !recordingRef.current && handsFreeEnabled.current) {
          handsFreeRef.current = true;
          setHandsFree(true);
          setError(null);
          void (async () => {
            const transcript = String(event.data?.transcript ?? "").trim();
            if (hasCommandAfterWakeWord(transcript)) {
              await sendRef.current(transcript);
              return;
            }
            setState("SPEAKING");
            await api.speakLocal(translateCurrent("Yes, sir?"), "wakeword-ack").catch(() => undefined);
            await new Promise((resolve) => window.setTimeout(resolve, 250));
            await startRecordingRef.current();
          })();
        }
        if (event.event === "wakeword.microphone_unmuted") {
          microphoneMutedRef.current = false;
          setMicrophoneMutedState(false);
          handsFreeRef.current = false;
          setHandsFree(false);
          const message = translateCurrent("JARVIS microphone enabled, sir.");
          setMessages((items) => [...items, { id: id(), role: "assistant", content: message }]);
          void (async () => {
            await api.speakLocal(message, "microphone-wake-unmuted").catch(() => undefined);
            await api.wakewordAction("resume").catch(() => undefined);
            setState("IDLE");
          })();
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!disposed) reconnectTimer = window.setTimeout(connect, 2_000);
      };
      }).catch(() => {
        if (!disposed) reconnectTimer = window.setTimeout(connect, 2_000);
      });
    };
    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  const toggleRecording = useCallback(async () => {
    if (microphoneMutedRef.current) {
      await setMicrophoneMuted(false);
      return;
    }
    if (recorder.current?.state === "recording" && handsFreeRef.current) await stop();
    else if (recorder.current?.state === "recording") finishRecording();
    else {
      if (handsFreeEnabled.current) {
        handsFreeRef.current = true;
        setHandsFree(true);
        await api.wakewordAction("pause").catch(() => undefined);
      }
      await startRecording();
    }
  }, [finishRecording, setMicrophoneMuted, startRecording, stop]);

  return <JarvisContext.Provider value={{ state, status, messages, error, audioLevel, recording, handsFree, microphoneMuted, send, confirm, stop, toggleRecording, setMicrophoneMuted, refreshStatus }}>{children}</JarvisContext.Provider>;
}

function normalizeSpeech(value: string) {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
}

function isNonSpeechTranscript(value: string) {
  if (!value) return true;
  return /^(musica(?: de fundo)?|som(?: de (?:fundo|botao|teclado|mouse|notificacao))?|ruido(?: de fundo)?|barulho|bipe|beep|silencio|aplausos|risos|inaudivel|audio)$/.test(value);
}

function matchesSpecialWake(value: string, configured: string) {
  if (!value || !configured) return false;
  if (value.includes(configured)) return true;
  const words = new Set(value.split(" "));
  return ["acorda", "crianca", "papai", "chegou"].filter((word) => words.has(word)).length >= 3;
}

function parseMicrophoneCommand(value: string): "mute" | "unmute" | null {
  if (!/\bmicrofone\b/.test(value)) return null;
  if (/\b(?:mutar|mute|desligar|desligue)\b/.test(value)) return "mute";
  if (/\b(?:ligar|ligue|ativar|ative|desmutar|desmute)\b/.test(value)) return "unmute";
  return null;
}

function parseMusicCommand(
  value: string,
  themeActive: boolean,
): "stop" | "pause" | "resume" | "restart" | null {
  const explicit = /\b(?:musica|tema|the clash)\b/.test(value);
  if (!explicit && !themeActive) return null;
  if (/^(?:jarvis )?(?:parar|pare)$/.test(value) || /\b(?:parar|pare|desligar)\b.*\b(?:musica|tema)\b/.test(value)) return "stop";
  if (/^(?:jarvis )?(?:pausar|pause)$/.test(value) || /\b(?:pausar|pause)\b/.test(value)) return "pause";
  if (/^(?:jarvis )?(?:recomecar|recomece|reiniciar|reinicie)$/.test(value) || /\b(?:recomecar|recomece|reiniciar|reinicie|do comeco)\b/.test(value)) return "restart";
  if (/^(?:jarvis )?(?:continuar|continue|retomar|retome)$/.test(value) || /\b(?:continuar|continue|retomar|retome|despausar)\b/.test(value)) return "resume";
  return null;
}

function hasCommandAfterWakeWord(value: string) {
  const normalized = normalizeSpeech(value);
  const withoutWake = normalized.replace(/^(?:jarvis|jairvis|jervis|jarves)\b[ ,]*/, "").trim();
  return withoutWake.length >= 3;
}

function isPositiveConfirmation(value: string) {
  return /\b(confirmo|confirmar|autorizo|autorizar)\b/.test(value)
    || /^(sim|pode|pode prosseguir|prossiga|envie|manda|delete|pode deletar|apague)$/.test(value);
}

function isNegativeConfirmation(value: string) {
  return /\b(nao|cancele|cancelar|pare|desista)\b/.test(value);
}

function confirmationResultText(result: { tool?: string; result?: unknown }) {
  const envelope = result.result && typeof result.result === "object"
    ? result.result as Record<string, unknown>
    : {};
  if (envelope.success !== true || envelope.verified !== true) {
    const detail = String(envelope.detail ?? envelope.error ?? translateCurrent("the executor did not verify the effect"));
    return `${translateCurrent("I could not complete the action, sir")}: ${detail}.`;
  }
  const payload = envelope.data && typeof envelope.data === "object"
    ? envelope.data as Record<string, unknown>
    : envelope;
  if (typeof payload.opened === "string" && typeof payload.written === "string") {
    return `Pronto, senhor. Criei e abri ${payload.written}.`;
  }
  if (typeof payload.moved_to_recycle_bin === "string") {
    return `${translateCurrent("Done, sir. I moved")} ${payload.moved_to_recycle_bin} ${translateCurrent("to the Recycle Bin")}.`;
  }
  if (result.tool === "whatsapp_message" && payload.sent === true) {
    return `${translateCurrent("Done, sir. The message was sent to")} ${String(payload.contact ?? translateCurrent("the contact"))}.`;
  }
  return `${translateCurrent("The action was completed and verified")}${result.tool ? `: ${result.tool.replaceAll("_", " ")}` : ""}, ${translateCurrent("sir")}.`;
}

export function useJarvis() {
  const value = useContext(JarvisContext);
  if (!value) throw new Error("useJarvis precisa estar dentro de JarvisProvider");
  return value;
}
