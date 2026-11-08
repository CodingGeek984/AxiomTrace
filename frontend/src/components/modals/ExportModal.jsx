"use client";

import { useEffect, useRef, useState } from "react";
import {
  Check,
  Copy,
  Download,
  FileJson,
  FileText,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { downloadJson, downloadText, fileStamp } from "@/lib/download";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FOCUS_RING } from "@/components/ui/styles";
import { useGraphStore } from "@/store/useGraphStore";

export default function ExportModal() {
  const { open, status, payload, error } = useGraphStore(
    (state) => state.exportState
  );
  const closeExport = useGraphStore((state) => state.closeExport);
  const openExport = useGraphStore((state) => state.openExport);
  const buildTraceSnapshot = useGraphStore((state) => state.buildTraceSnapshot);
  const notify = useGraphStore((state) => state.notify);

  const [copied, setCopied] = useState(false);
  const closeRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const timer = setTimeout(() => {
      if (closeRef.current) closeRef.current.focus();
    }, 30);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") closeExport();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, closeExport]);

  // The dialog is fully unmounted when closed, so no hidden overlay keeps
  // the rendered dump (or its React state) alive.
  if (!open) return null;

  const handleCopy = async () => {
    if (!payload) return;
    try {
      await navigator.clipboard.writeText(payload.markdown);
      setCopied(true);
      notify("LLM context copied to clipboard.", "ok");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      notify("Clipboard unavailable - use Download instead.", "error");
    }
  };

  const handleDownload = () => {
    if (!payload) return;
    const stamp = fileStamp();
    downloadText(
      payload.markdown,
      `axiom-context-${stamp}.md`,
      "text/markdown;charset=utf-8"
    );
    notify("Downloaded axiom-context.md", "ok");
  };

  const handleDownloadTrace = () => {
    const stamp = fileStamp();
    downloadJson(buildTraceSnapshot(), `axiom-trace-${stamp}.json`);
    notify("Downloaded axiom-trace.json", "ok");
  };

  return (
    <div
      className="fixed inset-0 z-50 flex animate-fade-in-up items-center justify-center bg-bg-scrim p-6 backdrop-blur-xl"
      role="dialog"
      aria-modal="true"
      aria-label="Export LLM context"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeExport();
      }}
    >
      <div className="w-full max-w-2xl space-y-5 rounded-[2rem] border border-border-subtle bg-bg-card p-6 shadow-panel sm:p-8">
        <div className="flex items-start gap-3.5">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-3xl bg-white text-revolut-textMain">
            <FileText size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-xl font-bold tracking-tight text-revolut-textMain">
              Export LLM Context
            </h2>
            <p className="mt-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary">
              {payload
                ? payload.isPartial
                  ? `${payload.node_count} selected nodes`
                  : `${payload.node_count} nodes · full graph`
                : "stitching graph dump…"}
            </p>
          </div>

          <button
            ref={closeRef}
            type="button"
            onClick={closeExport}
            aria-label="Close export dialog"
            className={cn(
              "rounded-full border border-border-subtle p-2.5 text-text-secondary transition-all duration-150 ease-in-out hover:border-border-active hover:bg-bg-cardHover hover:text-revolut-textMain",
              FOCUS_RING
            )}
          >
            <X size={15} />
          </button>
        </div>

        {payload ? (
          <div className="axiom-numeric flex items-center gap-2 rounded-full border border-border-subtle bg-bg-input px-4 py-2 font-mono text-[10px] text-text-secondary">
            <span className="h-1.5 w-1.5 rounded-full bg-status-green" />
            {payload.char_count.toLocaleString()} chars · ~
            {payload.estimated_tokens.toLocaleString()} tokens
          </div>
        ) : null}

        {status === "loading" ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-3xl border border-border-subtle bg-bg-input px-4 py-12">
            <Loader2 size={22} className="animate-spin text-accent-blue" />
            <p className="text-xs text-text-secondary">
              Stitching selection into a context dump…
            </p>
          </div>
        ) : status === "error" ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-3xl border border-status-red/40 bg-status-red/5 px-4 py-12 text-center">
            <p className="text-sm text-status-red">{error}</p>
            <button
              type="button"
              onClick={openExport}
              className={cn(BUTTON_SECONDARY, "w-auto px-5 py-2")}
            >
              <RefreshCw size={13} />
              Retry
            </button>
          </div>
        ) : payload ? (
          <pre className="max-h-[350px] overflow-auto whitespace-pre-wrap break-words rounded-3xl border border-border-subtle bg-bg-input p-5 font-mono text-xs leading-relaxed text-text-secondary">
            {payload.markdown}
          </pre>
        ) : null}

        <div className="flex items-center gap-3">
          <p className="hidden items-center gap-1.5 text-[10px] text-text-dimmed sm:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-status-green" />
            Stay local · paste into DeepSeek, Claude or GPT
          </p>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={handleDownloadTrace}
              className={cn(BUTTON_SECONDARY, "px-4 py-2")}
            >
              <FileJson size={13} />
              Trace .json
            </button>
            <button
              type="button"
              onClick={handleCopy}
              disabled={status !== "ready"}
              className={cn(
                BUTTON_SECONDARY,
                "px-4 py-2",
                copied &&
                  "border-status-green/30 bg-status-green/10 text-status-green hover:border-status-green/40 hover:bg-status-green/10 hover:text-status-green"
              )}
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? "Copied" : "Copy"}
            </button>
            <button
              type="button"
              onClick={handleDownload}
              disabled={status !== "ready"}
              className={cn(BUTTON_PRIMARY, "px-4 py-2")}
            >
              <Download size={13} />
              Download .md
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
