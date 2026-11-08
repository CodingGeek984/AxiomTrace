"use client";

import { useEffect, useMemo, useRef } from "react";
import { ChevronDown, TerminalSquare } from "lucide-react";

import { cn, formatMs, formatPercent } from "@/lib/utils";

/**
 * Normalise the many shapes the store can hand us into flat log lines.
 *
 * Accepts either an array of pre-built `{ level, text, meta }` entries, the
 * raw `outputs`/`detections` maps keyed by node id, or the sandbox report
 * objects themselves.
 *
 * @param {unknown} entries
 * @returns {Array<{ id: string, level: string, text: string, meta?: string }>}
 */
function normaliseEntries(entries) {
  if (!Array.isArray(entries)) return [];

  const lines = [];
  entries.forEach((entry, index) => {
    if (entry === null || entry === undefined) return;

    if (typeof entry === "string") {
      lines.push({ id: `log-${index}`, level: "info", text: entry });
      return;
    }

    if (typeof entry !== "object") {
      lines.push({ id: `log-${index}`, level: "info", text: String(entry) });
      return;
    }

    const level =
      entry.level ||
      entry.status ||
      (entry.error ? "error" : entry.type === "mutate" ? "warn" : "info");
    const text =
      entry.text ||
      entry.message ||
      entry.label ||
      entry.report?.error?.message ||
      "";
    const meta = entry.meta || entry.hint || "";

    lines.push({
      id: entry.id || `log-${index}`,
      level: String(level),
      text: String(text),
      meta: String(meta),
    });
  });

  return lines;
}

const LEVEL_TONE = {
  info: "text-text-secondary",
  success: "text-status-green",
  ok: "text-status-green",
  warn: "text-status-orange",
  warning: "text-status-orange",
  error: "text-status-red",
  timeout: "text-status-orange",
};

function LevelGlyph({ level }) {
  const tone = LEVEL_TONE[level] || LEVEL_TONE.info;

  if (level === "error" || level === "timeout") {
    return <span className={cn("shrink-0 font-bold", tone)}>✕</span>;
  }
  if (level === "success" || level === "ok") {
    return <span className={cn("shrink-0 font-bold", tone)}>✓</span>;
  }
  if (level === "warn" || level === "warning") {
    return <span className={cn("shrink-0 font-bold", tone)}>⚠</span>;
  }
  return <span className={cn("shrink-0 font-bold", tone)}>›</span>;
}

/**
 * Embedded read-only console that renders sandbox stdout/stderr and mutation
 * summaries. Auto-scrolls to the newest line unless the user has scrolled up.
 *
 * @param {{
 *   entries?: unknown[],
 *   title?: string,
 *   placeholder?: string,
 *   maxHeight?: string,
 *   className?: string,
 *   autoScroll?: boolean,
 * }} props
 */
export default function TerminalLog({
  entries = [],
  title = "Runtime console",
  placeholder = "No execution output yet.",
  maxHeight = "max-h-64",
  className,
  autoScroll = true,
}) {
  const lines = useMemo(() => normaliseEntries(entries), [entries]);
  const scrollRef = useRef(null);
  const pinnedRef = useRef(true);

  useEffect(() => {
    if (!autoScroll) return;
    const node = scrollRef.current;
    if (!node || !pinnedRef.current) return;
    node.scrollTop = node.scrollHeight;
  }, [lines, autoScroll]);

  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    pinnedRef.current = distance < 24;
  };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-3xl border border-border-subtle bg-bg-input",
        className
      )}
    >
      <div className="flex items-center gap-2 border-b border-border-subtle bg-white/[0.02] px-4 py-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-full border border-border-subtle bg-bg-card text-text-secondary">
          <TerminalSquare size={12} />
        </span>
        <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary">
          {title}
        </span>
        <span className="axiom-numeric ml-auto rounded-full border border-border-subtle bg-bg-card px-2 py-0.5 font-mono text-[9px] text-text-dimmed">
          {lines.length}
        </span>
        <ChevronDown size={12} className="text-text-dimmed" />
      </div>

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={cn(
          "overflow-y-auto px-3.5 py-3 font-mono text-[11px] leading-relaxed text-text-primary",
          maxHeight
        )}
      >
        {lines.length === 0 ? (
          <p className="text-text-dimmed">{placeholder}</p>
        ) : (
          <ul className="space-y-0.5">
            {lines.map((line) => {
              const tone = LEVEL_TONE[line.level] || LEVEL_TONE.info;
              return (
                <li key={line.id} className="flex items-start gap-1.5">
                  <LevelGlyph level={line.level} />
                  <span className={cn("min-w-0 flex-1 whitespace-pre-wrap break-all", tone)}>
                    {line.text}
                    {line.meta ? (
                      <span className="ml-1 text-text-dimmed">{line.meta}</span>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

/**
 * Build console-friendly lines from a single sandbox execution report.
 *
 * @param {Record<string, unknown>|null|undefined} report
 * @returns {Array<Record<string, string>>}
 */
export function reportToLogEntries(report) {
  if (!report) return [];
  const lines = [
    {
      id: "head",
      level: report.status === "success" ? "success" : "error",
      text: `status: ${report.status} · ${formatMs(report.execution_time)}`,
      meta: report.entry_function ? `entry: ${report.entry_function}()` : "",
    },
  ];

  if (report.stdout) {
    lines.push({ id: "stdout", level: "info", text: String(report.stdout).trimEnd() });
  }
  if (report.return_repr && report.return_repr !== "None") {
    lines.push({
      id: "return",
      level: "success",
      text: `return: ${report.return_repr}`,
    });
  }
  if (report.error) {
    lines.push({
      id: "error",
      level: "error",
      text: `${report.error.type || "Error"}: ${report.error.message || ""}`,
    });
  }
  return lines;
}

/**
 * Build console-friendly lines from a mutation stress report.
 *
 * @param {Record<string, unknown>|null|undefined} report
 * @returns {Array<Record<string, string>>}
 */
export function mutationToLogEntries(report) {
  if (!report) return [];
  const lines = [
    {
      id: "verdict",
      level: report.failed > 0 ? "error" : "success",
      text: `verdict: ${report.verdict} · crash rate ${formatPercent(report.crash_rate)}`,
      meta: `${report.failed}/${report.total_runs} runs failed`,
    },
  ];

  (Array.isArray(report.failures) ? report.failures : []).slice(0, 6).forEach(
    (failure, index) => {
      lines.push({
        id: `failure-${index}`,
        level: "error",
        text: `${failure.error_type} ×${failure.count}`,
        meta: Array.isArray(failure.inputs) ? failure.inputs.slice(0, 3).join(" ") : "",
      });
    }
  );

  return lines;
}
