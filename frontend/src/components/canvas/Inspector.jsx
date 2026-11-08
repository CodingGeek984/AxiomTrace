"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Braces,
  Check,
  Code2,
  Copy,
  FlaskConical,
  Loader2,
  PanelRight,
  Play,
  RotateCcw,
  ShieldAlert,
  Terminal,
  Trash2,
  Languages,
} from "lucide-react";

import { COLORS } from "@/config/theme";
import { cn } from "@/lib/cn";
import { SEMANTIC_TYPES, groupMeta, lineLabel, typeMeta } from "@/lib/graph";
import { resolveNodeSource } from "@/lib/nodeSource";
import { traceStepAt } from "@/lib/tracer";
import { parseJsonInput } from "@/lib/utils";
import TerminalLog, {
  mutationToLogEntries,
  reportToLogEntries,
} from "@/components/ui/TerminalLog";
import MeterRing from "@/components/ui/MeterRing";
import StatusBadge from "@/components/ui/StatusBadge";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, SECTION_LABEL } from "@/components/ui/styles";
import { useGraphStore } from "@/store/useGraphStore";

const TABS = [
  { key: "code", label: "Code", icon: Code2 },
  { key: "payload", label: "Payload", icon: Braces },
  { key: "transpile", label: "Transpile", icon: Languages },
  { key: "stress", label: "Mutation", icon: FlaskConical },
  { key: "logs", label: "Logs", icon: Terminal },
];

const PRIMARY_BUTTON = cn(BUTTON_PRIMARY, "w-full py-2.5");
const SECONDARY_BUTTON = cn(BUTTON_SECONDARY, "w-full py-2.5");

const EMPTY_TAB_HINTS = {
  code: "inspect its source code",
  payload: "edit the JSON payload handed to the sandbox",
  transpile: "transpile its logic to another language",
  stress: "stress test it with mutated inputs",
  logs: "view its execution logs",
};

/** Shared tab strip so the Inspector shows the same nav with or without a selection. */
function normalizeTab(raw) {
  if (raw === "mutate" || raw === "mutation") return "stress";
  return TABS.some((entry) => entry.key === raw) ? raw : "code";
}

function InspectorTabs({ activeTab, onSelect }) {
  const safeTab = normalizeTab(activeTab);
  const onKeyDown = (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const index = TABS.findIndex((entry) => entry.key === safeTab);
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const next = TABS[(index + delta + TABS.length) % TABS.length];
    onSelect(next.key);
  };
  const selectTab = (key) => onSelect(normalizeTab(key));
  return (
    <div className="shrink-0 border-b border-revolut-border px-1.5 py-1">
      <div className="flex gap-0.5" role="tablist" aria-label="Inspector views" onKeyDown={onKeyDown}>
        {TABS.map(({ key, label, icon: Icon }) => {
          const selected = safeTab === key;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              id={`inspector-tab-${key}`}
              aria-selected={selected}
              aria-controls={`inspector-panel-${key}`}
              tabIndex={selected ? 0 : -1}
              title={label}
              onClick={() => selectTab(key)}
              className={cn(
                "flex flex-1 items-center justify-center gap-1 whitespace-nowrap rounded-md px-1.5 py-1 text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/70",
                selected
                  ? "bg-white text-revolut-textMain shadow-sm"
                  : "text-revolut-textMuted hover:bg-white hover:text-revolut-textMain"
              )}
            >
              <Icon size={11} aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SectionTitle({ children, hint }) {
  return (
    <div className="mb-2.5 flex items-baseline justify-between gap-3">
      <h3 className={SECTION_LABEL}>{children}</h3>
      {hint ? (
        <span className="axiom-numeric shrink-0 font-mono text-[10px] text-text-dimmed">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

function EmptyState({ diagnostics, partial, stats }) {
  const safeDiagnostics = Array.isArray(diagnostics) ? diagnostics : [];
  const hasDiagnostics = safeDiagnostics.length > 0;

  return (
    <div className="flex h-full flex-col items-start justify-start gap-5 px-4 py-5">
      <div className="w-full rounded-3xl border border-border-subtle bg-bg-card/60 px-5 py-7 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-3xl bg-white text-revolut-textMain shadow-sm">
          <PanelRight size={22} />
        </div>
        <p className="font-display text-lg font-bold tracking-tight text-revolut-textMain">
          No node selected
        </p>
        <p className="mx-auto mt-2 max-w-[16rem] text-xs leading-relaxed text-text-secondary">
          Click a node to inspect its source, run it in the sandbox, or stress
          test it with mutated inputs.
        </p>
        <span className="mx-auto mt-4 inline-flex items-center gap-1.5 rounded-full border border-status-green/30 bg-status-green/10 px-3 py-1 text-[9px] font-semibold uppercase tracking-[0.16em] text-status-green">
          <span className="h-1.5 w-1.5 animate-led-pulse rounded-full bg-status-green" />
          Inspector online
        </span>
      </div>

      {partial ? (
        <div className="flex w-full items-start gap-2 rounded-2xl border border-status-orange/40 bg-status-orange/5 px-3.5 py-3">
          <AlertTriangle size={13} className="mt-0.5 shrink-0 text-status-orange" />
          <p className="text-[11px] leading-relaxed text-status-orange">
            Partial parse - some regions could not be recovered. The graph below
            is a best-effort reconstruction.
          </p>
        </div>
      ) : null}

      {hasDiagnostics ? (
        <div className="w-full">
          <SectionTitle hint={`${safeDiagnostics.length}`}>
            Parse diagnostics
          </SectionTitle>
          <div className="space-y-1.5">
            {safeDiagnostics.slice(0, 10).map((entry, index) => {
              const line =
                entry.start_line ?? entry.line ?? entry.line_number ?? null;
              return (
                <div
                  key={`${line ?? "x"}-${index}`}
                  className="rounded-2xl border border-border-subtle bg-bg-input px-3.5 py-2.5"
                >
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-status-orange/15 px-2 py-px font-mono text-[9px] font-semibold text-status-orange">
                      {line ? `L${line}` : "parser"}
                    </span>
                    <span className="truncate font-mono text-[10px] text-text-secondary">
                      {entry.kind || entry.region || entry.phase || "recovery"}
                    </span>
                    {entry.end_line && entry.end_line !== line ? (
                      <span className="ml-auto shrink-0 font-mono text-[9px] text-text-dimmed">
                        L{entry.end_line}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1 break-words text-[11px] leading-relaxed text-text-primary">
                    {entry.message || String(entry)}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {stats && Object.keys(stats).length > 0 ? (
        <div className="w-full">
          <SectionTitle>Graph stats</SectionTitle>
          <div className="grid grid-cols-3 gap-1.5">
            {Object.entries(stats)
              .filter(([, value]) => typeof value === "number" || typeof value === "string")
              .slice(0, 9)
              .map(([key, value]) => (
                <div
                  key={key}
                  className="rounded-2xl border border-border-subtle bg-bg-input px-2 py-2 text-center"
                >
                  <p className="font-display text-sm font-bold text-revolut-textMain">
                    {typeof value === "number" ? value : String(value)}
                  </p>
                  <p className="truncate text-[9px] uppercase tracking-wider text-text-dimmed">
                    {key.replace(/_/g, " ")}
                  </p>
                </div>
              ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SecurityPanel({
  severity,
  vulnerabilityType,
  description,
  mitigation,
  exploit,
  patch,
  securityIndex,
  futureRisk,
  findings = [],
}) {
  const safeFindings = Array.isArray(findings) ? findings : [];
  const extras = safeFindings.slice(1);
  const primaryExploit = exploit || safeFindings[0]?.exploit || "";
  const primaryPatch = patch || safeFindings[0]?.patch || "";
  const riskLabel = String(futureRisk || "").toUpperCase() || null;
  return (
    <div className="rounded-2xl border border-status-red/40 bg-status-red/5 p-3.5">
      <div className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-status-red/30 bg-white text-status-red">
          <ShieldAlert size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-status-red">
            Security finding
          </p>
          <p className="mt-0.5 truncate font-mono text-[11px] font-semibold uppercase tracking-wide text-revolut-textMain">
            {String(vulnerabilityType || "SECURITY_ISSUE").replace(/_/g, " ")}
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-status-red/40 bg-status-red/10 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-status-red">
          {severity}
        </span>
      </div>

      <p className="mt-3 text-xs leading-relaxed text-revolut-textMain">
        {description || "This node matched a security anti-pattern."}
      </p>

      {typeof securityIndex === "number" || riskLabel ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          {typeof securityIndex === "number" ? (
            <span className="rounded-full border border-status-red/30 bg-white px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-revolut-textMain">
              Security index: {securityIndex}/100
            </span>
          ) : null}
          {riskLabel ? (
            <span className="rounded-full border border-status-red/30 bg-white px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-status-red">
              Future risk: {riskLabel}
            </span>
          ) : null}
        </div>
      ) : null}

      {primaryExploit ? (
        <div className="mt-2.5 rounded-xl border border-status-red/20 bg-white px-3 py-2">
          <p className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider text-text-dimmed">
            <ShieldAlert size={10} className="text-status-red" />
            How this gets exploited
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-text-secondary">
            {primaryExploit}
          </p>
        </div>
      ) : null}

      <div className="mt-2.5 rounded-xl border border-status-red/20 bg-white px-3 py-2">
        <p className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider text-text-dimmed">
          <AlertTriangle size={10} className="text-status-orange" />
          Mitigation
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-text-secondary">
          {mitigation || "Review this node before deploying it."}
        </p>
      </div>

      {primaryPatch ? (
        <div className="mt-2.5 rounded-xl border border-status-red/20 bg-white px-3 py-2">
          <p className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider text-text-dimmed">
            <ShieldAlert size={10} className="text-status-red" />
            Secure patch
          </p>
          <pre className="mt-1.5 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-text-primary">
            {primaryPatch}
          </pre>
        </div>
      ) : null}

      {extras.length > 0 ? (
        <div className="mt-2.5 space-y-1.5">
          <p className="text-[9px] font-semibold uppercase tracking-wider text-text-dimmed">
            Also flagged
          </p>
          {extras.map((finding, index) => (
            <div
              key={`${finding.vulnerability_type}-${index}`}
              className="flex items-start gap-2 rounded-xl border border-status-red/15 bg-white px-2.5 py-1.5"
            >
              <span className="mt-0.5 shrink-0 rounded-full bg-status-red/10 px-1.5 py-px font-mono text-[8px] font-semibold uppercase text-status-red">
                {finding.severity || "HIGH"}
              </span>
              <span className="min-w-0 text-[10px] leading-relaxed text-text-secondary">
                {String(finding.vulnerability_type || "SECURITY_ISSUE").replace(
                  /_/g,
                  " "
                )}
                {finding.description ? ` — ${finding.description}` : ""}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function TracePanel({ step, total }) {
  const payload = step && step.payload ? step.payload : {};
  const stepNumber = Number.isInteger(step?.index) ? step.index + 1 : 1;
  return (
    <div className="rounded-2xl border border-accent-blue/40 bg-accent-blue/5 p-3.5">
      <div className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-accent-blue/30 bg-white text-accent-blue">
          <Activity size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-accent-blue">
            Data flow trace
          </p>
          <p className="mt-0.5 truncate text-xs font-semibold text-revolut-textMain">
            {step?.label || "Active step"}
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-accent-blue/40 bg-white px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-accent-blue">
          step {stepNumber}/{total || stepNumber}
        </span>
      </div>

      <p className="mt-2.5 text-[10px] leading-relaxed text-text-secondary">
        Simulated payload passed into this node at this exact step. Values are
        generated from the graph topology - no code is executed.
      </p>

      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-xl border border-accent-blue/15 bg-white p-3 font-mono text-[10px] leading-relaxed text-text-secondary">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </div>
  );
}

const TRANSPILATION_TARGETS = [
  { value: "python", label: "Python", ext: "py" },
  { value: "javascript", label: "JavaScript", ext: "js" },
  { value: "typescript", label: "TypeScript", ext: "ts" },
  { value: "cpp", label: "C++", ext: "cpp" },
  { value: "csharp", label: "C#", ext: "cs" },
  { value: "java", label: "Java", ext: "java" },
  { value: "rust", label: "Rust", ext: "rs" },
  { value: "go", label: "Go", ext: "go" },
  { value: "php", label: "PHP", ext: "php" },
];

const HIGHLIGHT_RULES = [
  { pattern: /(\/\/[^\n]*|#[^\n]*)/g, className: "text-text-dimmed italic" },
  { pattern: /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)/g, className: "text-status-green" },
  {
    pattern:
      /\b(def|class|return|if|elif|else|for|while|try|except|finally|raise|import|from|as|with|async|await|const|let|var|function|switch|case|break|continue|new|throw|catch|public|private|static|interface|type|enum|package|func|go|defer|select|range|map|foreach|echo|print|lambda|None|null|true|false|True|False)\b/g,
    className: "text-accent-blue font-semibold",
  },
  { pattern: /\b(\d+(?:\.\d+)?)\b/g, className: "text-status-orange" },
];

/**
 * Minimal dependency-free syntax highlighter. Tokenises with a small set of
 * ordered rules (comments → strings → keywords → numbers) and emits React
 * elements, so no `dangerouslySetInnerHTML` is involved.
 */
function highlightCode(source) {
  const text = String(source ?? "");
  const ranges = [];
  for (const rule of HIGHLIGHT_RULES) {
    const regex = new RegExp(rule.pattern.source, rule.pattern.flags);
    let match = regex.exec(text);
    while (match) {
      const start = match.index;
      const end = start + match[0].length;
      const overlaps = ranges.some((range) => start < range.end && end > range.start);
      if (!overlaps && match[0].length > 0) {
        ranges.push({ start, end, className: rule.className });
      }
      match = regex.exec(text);
    }
  }
  ranges.sort((a, b) => a.start - b.start);
  const parts = [];
  let cursor = 0;
  ranges.forEach((range, index) => {
    if (range.start > cursor) parts.push(text.slice(cursor, range.start));
    parts.push(
      <span key={index} className={range.className}>
        {text.slice(range.start, range.end)}
      </span>
    );
    cursor = range.end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

function TranspilePanel({ node }) {
  const transpileNode = useGraphStore((state) => state.transpileNode);
  const [target, setTarget] = useState("typescript");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  // Guards against out-of-order responses while the user flips languages:
  // only the latest effect run may apply its result.
  const sequenceRef = useRef(0);
  const copyTimerRef = useRef(null);

  const nodeId = node?.id || null;
  // Resolve through the shared helper so a node without bound text still
  // transpiles its generated fallback instead of being rejected.
  const nodeSource = nodeId ? resolveNodeSource(node) : null;
  const nodeCode = nodeSource ? nodeSource.code : "";
  const hasCode = Boolean(nodeId && String(nodeCode || "").trim());

  // NOTE: this panel is remounted per node via `key={node.id}` at the call
  // site, so no render-phase state syncing is needed here.

  // Dynamic on-demand conversion: transpile the moment the node, its source,
  // or the selected target language changes. Source edits are debounced
  // (300ms) so typing in the Code editor does not spam the backend.
  useEffect(() => {
    if (!hasCode) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      const sequence = sequenceRef.current + 1;
      sequenceRef.current = sequence;
      setLoading(true);
      setError(null);
      setCopied(false);
      transpileNode(nodeId, target)
        .then((res) => {
          if (cancelled || res?.stale || sequence !== sequenceRef.current) {
            return;
          }
          setResult(res);
        })
        .catch((err) => {
          if (cancelled || sequence !== sequenceRef.current) return;
          setResult(null);
          setError(err?.message || "Transpilation failed");
        })
        .finally(() => {
          if (!cancelled && sequence === sequenceRef.current) {
            setLoading(false);
          }
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [nodeId, nodeCode, hasCode, target, transpileNode]);

  useEffect(() => () => {
    if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
  }, []);

  const copyCode = useCallback(async () => {
    const text = result?.translated_code ?? "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
      } finally {
        document.body.removeChild(area);
      }
    }
    setCopied(true);
    if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
    copyTimerRef.current = window.setTimeout(() => setCopied(false), 1600);
  }, [result]);

  const activeTarget =
    TRANSPILATION_TARGETS.find((t) => t.value === target) ||
    TRANSPILATION_TARGETS[2];

  return (
    <div className="space-y-4">
      <div>
        <SectionTitle>Target Language</SectionTitle>
        <div className="relative">
          <select
            aria-label="Target language"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            className="h-10 w-full appearance-none rounded-xl border border-[#E3E5E9] bg-white pl-3.5 pr-9 text-xs font-semibold text-[#0B0C0E] outline-none transition-colors focus:border-accent-blue"
          >
            {TRANSPILATION_TARGETS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label} (.{t.ext})
              </option>
            ))}
          </select>
          <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-text-dimmed">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
          </span>
        </div>
        <div className="mt-2 grid grid-cols-3 gap-1.5" role="group" aria-label="Quick target language">
          {TRANSPILATION_TARGETS.map((t) => {
            const active = t.value === target;
            return (
              <button
                key={t.value}
                type="button"
                aria-pressed={active}
                onClick={() => setTarget(t.value)}
                className={cn(
                  "rounded-lg border px-1 py-1.5 font-mono text-[9px] font-semibold uppercase tracking-wider transition-colors",
                  active
                    ? "border-[#0B0C0E] bg-[#0B0C0E] text-white"
                    : "border-[#E3E5E9] bg-white text-text-secondary hover:border-[#0B0C0E] hover:text-[#0B0C0E]"
                )}
              >
                {t.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex items-center gap-2 text-[11px] text-text-secondary">
        <Loader2
          size={13}
          className={loading ? "animate-spin text-accent-blue" : "text-text-dimmed"}
        />
        <span>
          {loading
            ? `Transpiling to ${activeTarget?.label || target}…`
            : `Auto-converts to ${activeTarget?.label || target} on selection`}
        </span>
      </div>

      {error && (
        <div className="rounded-2xl border border-status-red/40 bg-status-red/5 p-3.5 text-status-red text-xs">
          {error}
        </div>
      )}

      {loading && !error ? (
        <div className="flex items-center justify-center gap-2.5 rounded-2xl border border-[#E3E5E9] bg-[#F4F5F7] px-4 py-8">
          <Loader2 size={16} className="animate-spin text-[#0B0C0E]" />
          <p className="text-xs font-medium text-[#0B0C0E]">
            Translating backend…
          </p>
        </div>
      ) : null}

      {!loading && result && (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <SectionTitle hint={`confidence: ${Math.round((result.confidence || 0) * 100)}%`}>
              Translated Code
            </SectionTitle>
            <span className="shrink-0 rounded-full border border-[#E3E5E9] bg-white px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-text-secondary">
              .{activeTarget?.ext || target}
            </span>
          </div>

          {result.warnings && result.warnings.length > 0 && (
            <div className="rounded-xl border border-status-orange/30 bg-status-orange/5 px-3 py-2 text-[10px] text-status-orange">
              <p className="font-semibold">Warnings:</p>
              <ul className="mt-1 list-disc list-inside space-y-0.5">
                {result.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="relative">
            <pre
              aria-readonly="true"
              className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-2xl border border-[#E3E5E9] bg-[#F4F5F7] p-3.5 font-mono text-[11px] leading-relaxed text-[#0B0C0E]"
            >
              {highlightCode(result.translated_code ?? "")}
            </pre>
            <button
              type="button"
              onClick={copyCode}
              className="absolute top-2 right-2 flex items-center gap-1.5 rounded-lg border border-[#E3E5E9] bg-white/90 px-2 py-1.5 text-[10px] font-semibold text-[#0B0C0E] transition-colors hover:bg-white"
              title="Copy code"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? "Copied" : "Copy code"}
            </button>
          </div>
        </div>
      )}

      {!hasCode ? (
        <div className="rounded-2xl border border-dashed border-[#E3E5E9] bg-[#F4F5F7] px-4 py-7 text-center">
          <Languages size={16} className="mx-auto mb-2 text-text-dimmed" />
          <p className="text-xs text-text-secondary">
            {nodeId
              ? "This node has no source code to transpile."
              : "Select a node to transpile its logic."}
          </p>
        </div>
      ) : null}
      {hasCode && !loading && !result && !error && (
        <div className="rounded-2xl border border-dashed border-[#E3E5E9] bg-[#F4F5F7] px-4 py-7 text-center">
          <Languages size={16} className="mx-auto mb-2 text-text-dimmed" />
          <p className="text-xs text-text-secondary">
            Pick a target language to convert this node logic instantly.
          </p>
        </div>
      )}
    </div>
  );
}

export default function Inspector() {
  const tab = useGraphStore((state) => state.inspectorTab);
  // Never trust the persisted tab blindly: an unknown/stale value falls
  // back to "code" so the panel always renders a valid view. User-facing
  // labels ("Mutate"/"Mutation") resolve to the internal "stress" key.
  const activeTab = normalizeTab(tab);
  const setTab = useGraphStore((state) => state.setInspectorTab);
  const nodes = useGraphStore((state) =>
    Array.isArray(state.nodes) ? state.nodes : []
  );
  const selectedIds = useGraphStore((state) =>
    Array.isArray(state.selectedIds) ? state.selectedIds : []
  );
  const outputs = useGraphStore((state) =>
    state.outputs && typeof state.outputs === "object" ? state.outputs : {}
  );
  const detections = useGraphStore((state) =>
    state.detections && typeof state.detections === "object" ? state.detections : {}
  );
  const busy = useGraphStore((state) =>
    state.busy && typeof state.busy === "object" ? state.busy : {}
  );
  const payloads = useGraphStore((state) =>
    state.payloads && typeof state.payloads === "object" ? state.payloads : {}
  );
  const executionLogs = useGraphStore((state) =>
    Array.isArray(state.executionLogs) ? state.executionLogs : []
  );
  const parseDiagnostics = useGraphStore((state) =>
    Array.isArray(state.parseDiagnostics) ? state.parseDiagnostics : []
  );
  const parsePartial = useGraphStore((state) => state.parsePartial);
  const parseStats = useGraphStore((state) => state.parseStats);
  const tracer = useGraphStore((state) => state.tracer);
  const updateNodeData = useGraphStore((state) => state.updateNodeData);
  const parseSource = useGraphStore((state) => state.parseSource);
  const setNodePayload = useGraphStore((state) => state.setNodePayload);
  const deleteSelected = useGraphStore((state) => state.deleteSelected);
  const runNode = useGraphStore((state) => state.runNode);
  const runMutations = useGraphStore((state) => state.runMutations);
  const runDetection = useGraphStore((state) => state.runDetection);

  const [iterations, setIterations] = useState(3);

  const selectedNodeId = selectedIds[0] || null;
  const traceStep = useMemo(() => traceStepAt(tracer), [tracer]);
  const traceTotal = Array.isArray(tracer?.steps) ? tracer.steps.length : 0;

  const selectedNode = useMemo(
    () => nodes.find((entry) => entry && entry.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );
  const traceNode = useMemo(
    () =>
      traceStep
        ? nodes.find((entry) => entry && entry.id === traceStep.nodeId) || null
        : null,
    [nodes, traceStep]
  );

  // Follow the live trace when the canvas selection is cleared (or points at
  // a node that no longer exists) so the payload inspector always tracks the
  // executing step instead of falling back to the empty state.
  const node = selectedNode || traceNode;
  const nodeId = node ? node.id : null;
  const output = nodeId ? outputs[nodeId] || null : null;
  const detection = nodeId ? detections[nodeId] || null : null;
  const busyKey = nodeId ? busy[nodeId] || null : null;
  const payloadText = nodeId ? payloads[nodeId] ?? "{}" : "{}";
  const payloadState = useMemo(
    () => parseJsonInput(payloadText),
    [payloadText]
  );
  const traceEntries = useMemo(
    () =>
      executionLogs
        .filter(
          (entry) =>
            entry &&
            (!nodeId || !entry.nodeId || entry.nodeId === nodeId)
        )
        .map((entry) => ({
          id: entry.id,
          level: entry.level,
          text: entry.label ? `${entry.label} · ${entry.text}` : entry.text,
          meta: entry.meta,
        })),
    [executionLogs, nodeId]
  );

  if (!node) {
    const ActiveIcon = (TABS.find((entry) => entry.key === activeTab) || TABS[0]).icon;
    return (
    <aside className="hidden w-[320px] shrink-0 flex-col border-l border-revolut-border bg-revolut-surface lg:flex">
      <div className="flex h-11 shrink-0 items-center border-b border-revolut-border px-4">
        <span className="text-xs font-medium uppercase tracking-wide text-revolut-textMuted">INSPECTOR</span>
      </div>
      <InspectorTabs activeTab={activeTab} onSelect={setTab} />
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-revolut-textMuted">
        <ActiveIcon size={18} className="text-text-dimmed" />
        <p className="text-xs font-medium text-revolut-textMain">No node selected</p>
        <p className="max-w-[220px] text-[11px] leading-relaxed">
          Click a node on the canvas to {EMPTY_TAB_HINTS[activeTab] || "inspect it"}.
        </p>
      </div>
    </aside>
    );
  }

  const data = node.data && typeof node.data === "object" ? node.data : {};
  const meta = typeMeta(data.type || node.type);
  const groupInfo = data.group ? groupMeta(data.group) : null;
  const parameters = Array.isArray(data.parameters) ? data.parameters : [];
  const description =
    data.node_description ||
    (!data.is_vulnerable ? data.description : "") ||
    meta.description ||
    "";
  const complexity = data.complexity || "O(1)";
  const category = data.category || meta.label;
  const returns = data.returns || null;
  const isVulnerable = Boolean(data.is_vulnerable);
  const severity = String(data.severity || "HIGH").toUpperCase();
  const vulnerabilityType = data.vulnerability_type || "SECURITY_ISSUE";
  const securityDescription =
    data.security_description || data.description || "";
  const mitigation = data.mitigation || "";
  const securityFindings = Array.isArray(data.security_findings)
    ? data.security_findings
    : [];

  const onRun = () => runNode(node.id).catch(() => {});
  const onMutate = () => runMutations(node.id, iterations).catch(() => {});
  const onDetect = () => runDetection(node.id).catch(() => {});

  return (
    <aside className="hidden w-[320px] shrink-0 flex-col border-l border-revolut-border bg-revolut-surface lg:flex">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-revolut-border px-4">
        <span
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded-sm font-mono text-[9px]"
          style={{ backgroundColor: `${meta.color}14`, color: meta.color }}
        >
          {meta.badge}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium uppercase tracking-wide text-revolut-textMuted">INSPECTOR</p>
        </div>
        {isVulnerable ? (
          <span
            title={`${vulnerabilityType}: ${securityDescription}`}
            className="shrink-0 rounded-full border border-status-red/40 bg-status-red/10 p-1 text-status-red"
          >
            <ShieldAlert size={11} />
          </span>
        ) : null}
        <span className="truncate font-mono text-[9px] text-revolut-textMuted">
          {lineLabel(data)}
        </span>
        <button
          type="button"
          title="Delete selected node(s)"
          onClick={deleteSelected}
          className="shrink-0 rounded-md p-1 text-revolut-textMuted transition-colors hover:bg-white hover:text-revolut-textMain"
        >
          <Trash2 size={12} />
        </button>
      </div>

      <InspectorTabs activeTab={activeTab} onSelect={setTab} />

      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2.5">
        {traceStep && traceStep.nodeId === node.id ? (
          <div className="mb-3">
            <TracePanel step={traceStep} total={traceTotal} />
          </div>
        ) : null}

        {activeTab === "code" ? (
          <div role="tabpanel" id="inspector-panel-code" aria-labelledby="inspector-tab-code" className="space-y-4">
            {isVulnerable ? (
              <SecurityPanel
                severity={severity}
                vulnerabilityType={vulnerabilityType}
                description={securityDescription}
                mitigation={mitigation}
                exploit={
                  data.security?.exploit ||
                  data.security_findings?.[0]?.exploit ||
                  ""
                }
                patch={
                  data.security?.patch ||
                  data.security_findings?.[0]?.patch ||
                  ""
                }
                securityIndex={
                  typeof data.security_index === "number"
                    ? data.security_index
                    : typeof data.security?.security_index === "number"
                      ? data.security.security_index
                      : undefined
                }
                futureRisk={
                  data.future_risk_rating ||
                  data.security?.future_risk_rating ||
                  ""
                }
                findings={securityFindings}
              />
            ) : null}

            {data.diagnostic ? (
              <div className="flex items-start gap-2 rounded-2xl border border-status-orange/40 bg-status-orange/5 px-3.5 py-3">
                <AlertTriangle size={13} className="mt-0.5 shrink-0 text-status-orange" />
                <div className="min-w-0">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-status-orange">
                    Recovered node
                  </p>
                  <p className="mt-0.5 break-words text-[11px] leading-relaxed text-text-secondary">
                    {String(data.diagnostic)}
                  </p>
                </div>
              </div>
            ) : null}

            <div className="rounded-2xl border border-revolut-border bg-white p-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-text-dimmed">
                    Overview
                  </p>
                  <p className="mt-1.5 text-xs leading-relaxed text-revolut-textMain">
                    {description || "No description available for this node."}
                  </p>
                </div>
                <span
                  className="shrink-0 rounded-full border px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider"
                  style={{
                    borderColor: `${meta.color}33`,
                    backgroundColor: `${meta.color}0f`,
                    color: meta.color,
                  }}
                >
                  {category}
                </span>
              </div>

              <div className="mt-3 grid grid-cols-2 gap-1.5">
                <div className="rounded-xl border border-revolut-border bg-revolut-surface px-2.5 py-2">
                  <p className="text-[9px] uppercase tracking-wider text-text-dimmed">
                    Domain
                  </p>
                  <p className="mt-0.5 truncate text-[11px] font-medium text-revolut-textMain">
                    {groupInfo?.label || data.group_label || "—"}
                  </p>
                </div>
                <div className="rounded-xl border border-revolut-border bg-revolut-surface px-2.5 py-2">
                  <p className="text-[9px] uppercase tracking-wider text-text-dimmed">
                    Complexity
                  </p>
                  <p className="axiom-numeric mt-0.5 font-mono text-[11px] font-medium text-revolut-textMain">
                    {complexity}
                  </p>
                </div>
              </div>

              <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                <div className="rounded-xl border border-revolut-border bg-revolut-surface px-2.5 py-2">
                  <p className="text-[9px] uppercase tracking-wider text-text-dimmed">
                    Parameters
                  </p>
                  <p className="mt-0.5 truncate font-mono text-[10px] text-revolut-textMain">
                    {parameters.length > 0 ? parameters.join(", ") : "—"}
                  </p>
                </div>
                <div className="rounded-xl border border-revolut-border bg-revolut-surface px-2.5 py-2">
                  <p className="text-[9px] uppercase tracking-wider text-text-dimmed">
                    Returns
                  </p>
                  <p className="mt-0.5 truncate font-mono text-[10px] text-revolut-textMain">
                    {returns || "—"}
                  </p>
                </div>
              </div>
            </div>

            <div>
              <SectionTitle>Label</SectionTitle>
              <input
                value={data.label ?? ""}
                onChange={(event) =>
                  updateNodeData(node.id, { label: event.target.value })
                }
                className="w-full rounded-md border border-revolut-border bg-white px-2.5 py-1.5 text-xs text-revolut-textMain outline-none transition-all duration-150 ease-in-out focus:border-accent-blue"
              />
            </div>

            <div>
              <SectionTitle>Semantic type</SectionTitle>
              <div className="grid grid-cols-3 gap-1.5">
                {SEMANTIC_TYPES.map((type) => {
                  const typeInfo = typeMeta(type);
                  const active = data.type === type;
                  return (
                    <button
                      key={type}
                      type="button"
                      onClick={() =>
                        updateNodeData(node.id, {
                          type,
                          semantic_type: type,
                          category: typeInfo.label,
                          group: typeInfo.group,
                          group_label: groupMeta(typeInfo.group).label,
                          layer: groupMeta(typeInfo.group).order,
                        })
                      }
                      className={cn(
                        "rounded-full border px-1 py-2 text-[10px] font-semibold uppercase tracking-wider transition-all duration-150 ease-in-out",
                        active
                          ? "border-transparent text-revolut-textMain"
                          : "border-revolut-border text-text-secondary hover:border-border-active hover:text-revolut-textMain"
                      )}
                      style={
                        active
                          ? {
                              backgroundColor: `${typeInfo.color}14`,
                              borderColor: `${typeInfo.color}66`,
                              color: typeInfo.color,
                            }
                          : undefined
                      }
                    >
                      {typeInfo.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div>
              <SectionTitle
                hint={`${(data.code || data.source || "").length} chars`}
              >
                Source
              </SectionTitle>
              {data.file || data.module ? (
                <p
                  title={data.file || data.module}
                  className="mb-1.5 truncate font-mono text-[10px] text-text-dimmed"
                >
                  {data.module ? `${data.module} · ` : ""}
                  {data.file || ""}
                </p>
              ) : null}
              <textarea
                value={data.code ?? data.source ?? ""}
                onChange={(event) =>
                  updateNodeData(node.id, {
                    code: event.target.value,
                    source: event.target.value,
                  })
                }
                spellCheck={false}
                rows={14}
                className="w-full resize-y rounded-2xl border border-border-subtle bg-bg-input p-3.5 font-mono text-xs leading-relaxed text-text-primary outline-none transition-all duration-150 ease-in-out focus:border-accent-blue"
              />
            </div>

            <button
              type="button"
              onClick={onRun}
              disabled={Boolean(busyKey)}
              className={PRIMARY_BUTTON}
            >
              {busyKey === "exec" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Play size={13} />
              )}
              Run node
            </button>
          </div>
        ) : null}

        {activeTab === "payload" ? (
          <div role="tabpanel" id="inspector-panel-payload" aria-labelledby="inspector-tab-payload" className="space-y-4">
            <p className="text-xs leading-relaxed text-text-secondary">
              JSON payload handed to the sandbox when this node executes. It is
              sent verbatim as <span className="font-mono">input_data</span>.
            </p>

            <div>
              <SectionTitle
                hint={
                  payloadState.ok
                    ? "valid json"
                    : "invalid json"
                }
              >
                Payload
              </SectionTitle>
              <textarea
                value={payloadText}
                onChange={(event) =>
                  setNodePayload(node.id, event.target.value)
                }
                spellCheck={false}
                rows={12}
                className={cn(
                  "w-full resize-y rounded-md border bg-white p-2.5 font-mono text-[11px] leading-relaxed text-text-primary outline-none transition-all duration-150 ease-in-out",
                  payloadState.ok
                    ? "border-border-subtle focus:border-accent-blue"
                    : "border-status-red/50 focus:border-status-red"
                )}
              />
              {!payloadState.ok ? (
                <p className="mt-1.5 font-mono text-[10px] text-status-red">
                  {payloadState.error}
                </p>
              ) : null}
            </div>

            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setNodePayload(node.id, "{}")}
                className={SECONDARY_BUTTON}
              >
                <RotateCcw size={13} />
                Reset
              </button>
              <button
                type="button"
                onClick={onRun}
                disabled={Boolean(busyKey) || !payloadState.ok}
                className={PRIMARY_BUTTON}
              >
                {busyKey === "exec" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Play size={13} />
                )}
                Run with payload
              </button>
            </div>
          </div>
        ) : null}

        {activeTab === "transpile" ? (
          <div role="tabpanel" id="inspector-panel-transpile" aria-labelledby="inspector-tab-transpile">
          <TranspilePanel key={node.id} node={node} />
          </div>
        ) : null}

        {activeTab === "stress" ? (
          <div role="tabpanel" id="inspector-panel-stress" aria-labelledby="inspector-tab-stress" className="space-y-4">
            <div>
              <SectionTitle>Mutation iterations</SectionTitle>
              <div className="flex gap-1.5">
                {[1, 2, 3, 4, 5].map((count) => (
                  <button
                    key={count}
                    type="button"
                    onClick={() => setIterations(count)}
                    className={cn(
                      "axiom-numeric h-9 flex-1 rounded-full border text-xs font-semibold transition-all duration-150 ease-in-out",
                      iterations === count
                        ? "border-accent-blue/60 bg-accent-blue/12 text-accent-blue shadow-glow-blue"
                        : "border-border-subtle text-text-secondary hover:border-border-active hover:text-text-primary"
                    )}
                  >
                    {count}
                  </button>
                ))}
              </div>
            </div>

            <button
              type="button"
              onClick={onMutate}
              disabled={Boolean(busyKey)}
              className={PRIMARY_BUTTON}
            >
              {busyKey === "mutate" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <FlaskConical size={13} />
              )}
              Run mutation corpus
            </button>

            {output && output.type === "mutate" ? (
              <MutationReport report={output.report} />
            ) : (
              <div className="rounded-2xl border border-dashed border-border-subtle px-4 py-7 text-center">
                <FlaskConical size={16} className="mx-auto mb-2 text-text-dimmed" />
                <p className="text-xs text-text-secondary">
                  No mutation corpus has been run against this node yet.
                </p>
              </div>
            )}
          </div>
        ) : null}

        {activeTab === "logs" ? (
          <div role="tabpanel" id="inspector-panel-logs" aria-labelledby="inspector-tab-logs" className="space-y-4">
            <div className="grid grid-cols-3 gap-1.5">
              <button
                type="button"
                onClick={onRun}
                disabled={Boolean(busyKey)}
                className={cn(SECONDARY_BUTTON, "px-2")}
              >
                {busyKey === "exec" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Play size={13} />
                )}
                Execute
              </button>
              <button
                type="button"
                onClick={onMutate}
                disabled={Boolean(busyKey)}
                className={cn(SECONDARY_BUTTON, "px-2")}
              >
                {busyKey === "mutate" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <FlaskConical size={13} />
                )}
                Mutate
              </button>
              <button
                type="button"
                onClick={onDetect}
                disabled={Boolean(busyKey)}
                className={cn(
                  SECONDARY_BUTTON,
                  "px-2 hover:border-status-orange/40 hover:text-status-orange"
                )}
              >
                {busyKey === "detect" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <ShieldAlert size={13} />
                )}
                Detect
              </button>
            </div>

            {!output && !detection ? (
              <div className="rounded-2xl border border-dashed border-border-subtle px-4 py-7 text-center">
                <Activity size={16} className="mx-auto mb-2 text-text-dimmed" />
                <p className="text-xs text-text-secondary">
                  No execution data yet for this node.
                </p>
              </div>
            ) : null}

            {output && output.type === "execute" ? (
              <ExecutionReport report={output.report} />
            ) : null}

            {detection ? <DetectionReport report={detection} /> : null}

            <TerminalLog
              title="Sandbox console"
              maxHeight="max-h-52"
              entries={
                output && output.type === "mutate"
                  ? mutationToLogEntries(output.report)
                  : output
                    ? reportToLogEntries(output.report)
                    : []
              }
            />

            <TerminalLog
              title="Execution log"
              maxHeight="max-h-40"
              placeholder="No trace entries yet - run a node to populate the log."
              entries={traceEntries}
            />
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function ExecutionReport({ report }) {
  if (!report || typeof report !== "object") return null;
  const success = report.status === "success";
  const timeout = report.status === "timeout";
  const tone = success
    ? "text-status-green"
    : timeout
      ? "text-status-orange"
      : "text-status-red";
  const ringColor = success
    ? COLORS.success
    : timeout
      ? COLORS.warning
      : COLORS.error;
  const ringValue = success ? 100 : timeout ? 50 : 0;

  return (
    <div className="space-y-3">
      <SectionTitle hint={`${report.execution_time} ms`}>Execute result</SectionTitle>

      <div className="flex items-center gap-3 rounded-2xl border border-border-subtle bg-bg-input p-3">
        <MeterRing
          value={ringValue}
          size={58}
          stroke={5}
          color={ringColor}
          label={success ? "OK" : timeout ? "T/O" : "ERR"}
          sublabel="run"
        />
        <div className="min-w-0 flex-1">
          <span className={cn("text-xs font-semibold uppercase tracking-wider", tone)}>
            {report.status}
          </span>
          <p className="mt-1 truncate font-mono text-[10px] text-text-dimmed">
            {report.entry_function
              ? `entry: ${report.entry_function}`
              : "no entry function"}
          </p>
        </div>
      </div>

      {report.stdout ? (
        <div>
          <SectionTitle>stdout</SectionTitle>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-2xl border border-border-subtle bg-bg-input p-3.5 font-mono text-[11px] leading-relaxed text-text-secondary">
            {report.stdout}
            {report.stdout_truncated ? "\n… truncated" : ""}
          </pre>
        </div>
      ) : null}

      {report.return_repr ? (
        <div>
          <SectionTitle>return</SectionTitle>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-2xl border border-border-subtle bg-bg-input p-3.5 font-mono text-[11px] leading-relaxed text-status-green/90">
            {report.return_repr}
          </pre>
        </div>
      ) : null}

      {report.error ? (
        <div>
          <SectionTitle>
            error · {report.error.type || "Exception"}
          </SectionTitle>
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-2xl border border-status-red/40 bg-status-red/5 p-3.5 font-mono text-[11px] leading-relaxed text-status-red">
            {`${report.error.type || "Error"}: ${report.error.message || ""}`}
            {report.error.traceback ? `\n\n${report.error.traceback}` : ""}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function MutationReport({ report }) {
  if (!report || typeof report !== "object") return null;
  const stable = report.failed === 0;
  const crashRate = Number(report.crash_rate) || 0;
  const stability = Math.max(0, Math.min(100, Math.round((1 - crashRate) * 100)));
  return (
    <div className="space-y-3">
      <SectionTitle hint={`${report.duration_ms ?? "?"} ms`}>
        Mutation report
      </SectionTitle>

      <div
        className={cn(
          "flex items-center gap-4 rounded-2xl border p-4",
          stable
            ? "border-status-green/40 bg-status-green/5"
            : "border-status-red/40 bg-status-red/5"
        )}
      >
        <MeterRing
          value={stability}
          size={82}
          stroke={7}
          color={stable ? COLORS.success : COLORS.error}
          sublabel="stable"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {stable ? (
              <Check size={14} className="text-status-green" />
            ) : (
              <AlertTriangle size={14} className="text-status-red" />
            )}
            <p
              className={cn(
                "text-xs font-semibold uppercase tracking-wider",
                stable ? "text-status-green" : "text-status-red"
              )}
            >
              {report.verdict}
            </p>
          </div>
          <p className="mt-1.5 text-[10px] leading-relaxed text-text-secondary">
            crash rate {report.crash_rate} · {report.failed}/{report.total_runs}{" "}
            runs failed · {Array.isArray(report.edge_cases) ? report.edge_cases.length : 0} edge
            input(s) · {report.iterations_per_input}x per input
          </p>
        </div>
      </div>

      {Array.isArray(report.failures) && report.failures.length > 0 ? (
        <div className="space-y-2">
          <SectionTitle hint={`${report.failures.length} unique failure(s)`}>
            Failures
          </SectionTitle>
          {report.failures.slice(0, 8).map((failure, index) => (
            <div
              key={`${failure.error_type}-${index}`}
              className="rounded-2xl border border-border-subtle bg-bg-input p-3.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-[10px] text-accent-blue">
                  {failure.inputs && failure.inputs.length > 0
                    ? failure.inputs.slice(0, 3).join("  ")
                    : "unknown input"}
                </span>
                <span className="shrink-0 rounded-full bg-status-red/15 px-2 py-px text-[9px] font-semibold uppercase text-status-red">
                  {failure.error_type} ×{failure.count}
                </span>
              </div>
              <p className="mt-1.5 break-all font-mono text-[10px] leading-relaxed text-text-secondary">
                {String(failure.error_message || "")
                  .split("\n")
                  .slice(0, 3)
                  .join("\n")}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-text-secondary">
          All mutated inputs survived - the node is stable under this corpus.
        </p>
      )}
    </div>
  );
}

const SEVERITY_BADGE = {
  critical: "#FF3B30",
  high: "#FF3B30",
  medium: "#FFCC00",
  low: "#8E8E93",
};

const RATING_BADGE = {
  CRITICAL: "#FF3B30",
  HIGH: "#FF3B30",
  MEDIUM: "#FFCC00",
  LOW: "#30D158",
};

function DetectionReport({ report }) {
  if (!report || typeof report !== "object") return null;
  const findings = Array.isArray(report.findings) ? report.findings : [];
  const score =
    typeof report.security_index === "number" ? report.security_index : 100;
  const rating = String(report.future_risk_rating || "LOW").toUpperCase();
  const scoreColor =
    rating === "LOW"
      ? COLORS.success
      : rating === "MEDIUM"
        ? "#FFCC00"
        : COLORS.error;
  return (
    <div className="space-y-3">
      <SectionTitle hint={report.source}>Security scan</SectionTitle>

      <div className="flex items-center gap-3 rounded-2xl border border-border-subtle bg-bg-input p-3.5">
        <MeterRing
          value={score}
          size={64}
          stroke={6}
          color={scoreColor}
          label={`${score}`}
          sublabel="score / 100"
        />
        <div className="min-w-0 flex-1">
          <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-text-dimmed">
            Security index
          </p>
          <p className="mt-0.5 text-xs font-semibold text-revolut-textMain">
            {score} / 100
          </p>
          <span
            className="mt-1.5 inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider"
            style={{
              borderColor: `${RATING_BADGE[rating] || "#FFCC00"}66`,
              backgroundColor: `${RATING_BADGE[rating] || "#FFCC00"}14`,
              color: RATING_BADGE[rating] || "#FFCC00",
            }}
          >
            risk: {rating}
          </span>
        </div>
      </div>

      <p className="rounded-2xl border border-border-subtle bg-bg-input px-4 py-3 text-xs leading-relaxed text-text-secondary">
        {report.summary || "No summary returned."}
      </p>

      {report.fallback_reason ? (
        <p className="text-[10px] text-status-orange">
          Fallback engaged: {String(report.fallback_reason).slice(0, 160)}
        </p>
      ) : null}

      {findings.length === 0 ? (
        <div className="rounded-2xl border border-status-green/30 bg-status-green/5 px-4 py-5 text-center text-xs text-status-green">
          No security issues detected on this node.
        </div>
      ) : (
        <div className="space-y-2">
          {findings.map((finding, index) => {
            const severity = String(finding.severity || "low").toLowerCase();
            const badgeColor = SEVERITY_BADGE[severity] || "#8E8E93";
            return (
            <div
              key={`${finding.category}-${finding.line}-${index}`}
              className="rounded-2xl border border-border-subtle bg-bg-input p-3.5"
            >
              <div className="flex items-center gap-2">
                <span
                  className="rounded-full border px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider"
                  style={{
                    borderColor: `${badgeColor}66`,
                    backgroundColor: `${badgeColor}14`,
                    color: badgeColor,
                  }}
                >
                  {severity}
                </span>
                <span className="rounded bg-bg-card px-1.5 py-px font-mono text-[9px] uppercase text-accent-blue">
                  {String(finding.category || "issue").replace(/_/g, " ")}
                </span>
                {finding.line ? (
                  <span className="ml-auto font-mono text-[9px] text-text-dimmed">
                    L{finding.line}
                  </span>
                ) : null}
              </div>
              <p className="mt-1.5 text-[11px] leading-relaxed text-text-primary">
                {finding.message}
              </p>
              {finding.exploit ? (
                <p className="mt-1 text-[10px] leading-relaxed text-text-secondary">
                  <span className="font-semibold text-text-dimmed">Exploit: </span>
                  {finding.exploit}
                </p>
              ) : null}
              {finding.suggestion ? (
                <p className="mt-1 text-[10px] leading-relaxed text-text-secondary">
                  {finding.suggestion}
                </p>
              ) : null}
              {finding.patch ? (
                <pre className="mt-2 overflow-auto whitespace-pre-wrap break-all rounded-xl border border-border-subtle bg-white p-2.5 font-mono text-[10px] leading-relaxed text-text-primary">
                  {finding.patch}
                </pre>
              ) : null}
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
