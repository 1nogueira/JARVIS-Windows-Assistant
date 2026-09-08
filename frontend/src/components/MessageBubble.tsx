import { useLanguage } from "../i18n/LanguageContext";
import { Check, CheckCircle2, CircleAlert, Clipboard, ExternalLink, LoaderCircle, RotateCcw, ShieldQuestion, TimerOff, X } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { ChatMessage } from "../types";
import { useJarvis } from "../store/JarvisContext";

export function MessageBubble({ message }: { message: ChatMessage }) {
  const { t } = useLanguage();
  const { confirm, send } = useJarvis();
  const [copied, setCopied] = useState(false);
  const copy = async () => { await navigator.clipboard.writeText(message.content); setCopied(true); window.setTimeout(() => setCopied(false), 1200); };
  return (
    <article className={`chat-message ${message.role}`}>
      <div className="message-avatar">{message.role === "user" ? t("YOU") : "J"}</div>
      <div className="message-body">
        <header><b>{message.role === "user" ? t("You") : "JARVIS"}</b>{message.agent && <span>{message.agent}</span>}{message.route && <span>{message.route}</span>}{message.streaming && <em>{t("streaming")}</em>}</header>
        <div className="markdown-body">
          {message.role === "assistant" ? <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex, rehypeHighlight]}>{message.content || " "}</ReactMarkdown> : <p>{message.content}</p>}
          {message.streaming && <i className="stream-caret" />}
        </div>
        {!!message.actions?.length && (
          <section className="tool-activity">
            <header><span>{t("Execution")}</span><small>{message.actions.filter((action) => action.status === "completed").length}/{message.actions.length} {t("completed")}</small></header>
            {message.actions.map((action, index) => <ActionRow key={action.action_id || `${action.tool}-${index}`} action={action} />)}
          </section>
        )}
        {!!message.sources?.length && (
          <section className="source-list"><header>{t("Sources")}</header><div>{message.sources.slice(0, 8).map((source, index) => <a href={source.url} target="_blank" rel="noreferrer" key={`${source.url}-${index}`}><span>{index + 1}</span><b>{source.title || source.source || new URL(source.url).hostname}</b><ExternalLink size={12} /></a>)}</div></section>
        )}
        {message.confirmationId && message.confirmationPreview && (
          <section className="approval-card"><header><ShieldQuestion size={16} /><div><b>{message.confirmationPreview.title}</b><p>{message.confirmationPreview.summary}</p></div></header><dl>{message.confirmationPreview.details.map((detail) => <div key={`${detail.label}-${detail.value}`}><dt>{detail.label}</dt><dd>{detail.value}</dd></div>)}</dl><footer><button className="button secondary" onClick={() => void confirm(message, false)}><X size={14} /> {t("Cancel")}</button><button className="button primary" onClick={() => void confirm(message, true)}><Check size={14} /> {t("Authorize")}</button></footer></section>
        )}
        {message.role === "assistant" && !message.streaming && <footer className="message-actions"><button onClick={() => void copy()}>{copied ? <Check size={13} /> : <Clipboard size={13} />}{copied ? t("Copied") : t("Copy")}</button><button onClick={() => void send(message.content)}><RotateCcw size={13} /> {t("Retry")}</button>{message.model && <span>{message.model} · {formatMetric(message.metrics?.duration_ms)}{message.metrics?.ttft_ms ? ` · TTFT ${formatMetric(message.metrics.ttft_ms)}` : ""}</span>}</footer>}
      </div>
    </article>
  );
}

function ActionRow({ action }: { action: NonNullable<ChatMessage["actions"]>[number] }) {
  const Icon = action.status === "completed" ? CheckCircle2 : action.status === "failed" ? CircleAlert : action.status === "timed_out" ? TimerOff : action.status.includes("confirmation") || action.status === "needs_confirmation" ? ShieldQuestion : LoaderCircle;
  return <div className={`action-row status-${action.status}`}><Icon size={14} className={["pending", "running", "started"].includes(action.status) ? "spin" : ""} /><span><b>{action.label || humanize(action.tool)}</b><small>{action.detail || humanize(action.status)}</small></span><em>{humanize(action.status)}</em></div>;
}

function humanize(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function formatMetric(value: unknown) { return typeof value === "number" ? `${value.toFixed(0)} ms` : ""; }

