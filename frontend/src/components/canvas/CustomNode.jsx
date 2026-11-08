"use client";

import { Handle, Position } from "@xyflow/react";
import {
  Activity,
  Braces,
  Database,
  DoorOpen,
  GitBranch,
  Loader2,
  Repeat,
  Settings,
  ShieldAlert,
  TriangleAlert,
  Wrench,
  Zap,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { groupMeta, lineLabel, tierMeta, typeMeta } from "@/lib/graph";
import { traceStepAt } from "@/lib/tracer";
import { BUSY_GLOW, STATUS_GLOW, busyMeta, statusMeta } from "@/config/theme";
import { useGraphStore } from "@/store/useGraphStore";

const ICONS = {
  entrypoint: DoorOpen,
  service: Settings,
  database: Database,
  utility: Wrench,
  error_handler: ShieldAlert,
  function: Braces,
  condition: GitBranch,
  loop: Repeat,
  action: Zap,
};

const HANDLE_CLASS =
  "!h-2.5 !w-2.5 !border-2 !border-white !bg-[#5E6473] transition-all duration-150 ease-in-out hover:!scale-125 hover:!bg-accent-blue";

export default function CustomNode({ id, data, selected }) {
  const output = useGraphStore((state) =>
    state.outputs && typeof state.outputs === "object" ? state.outputs[id] : null
  );
  const busy = useGraphStore((state) =>
    state.busy && typeof state.busy === "object" ? state.busy[id] : null
  );
  const traceNodeId = useGraphStore((state) => {
    const step = traceStepAt(state.tracer);
    return step ? step.nodeId : null;
  });
  const isTracing = traceNodeId === id;

  const safeData = data && typeof data === "object" ? data : {};
  const semanticType = safeData.type || safeData.semantic_type || "action";
  const meta = typeMeta(semanticType);
  const groupInfo = safeData.group ? groupMeta(safeData.group) : null;
  const tierInfo = tierMeta(Number(safeData.tier) || 3);
  const Icon = ICONS[semanticType] || ICONS[safeData.semantic_type] || Zap;
  const isVulnerable = Boolean(safeData.is_vulnerable);
  const severity = String(safeData.severity || "HIGH").toUpperCase();
  const vulnerabilityType = safeData.vulnerability_type || "SECURITY_ISSUE";
  const securityDescription =
    safeData.security_description || safeData.description || "";
  const securityTip = `${vulnerabilityType}: ${securityDescription}`;
  const securityIndex =
    typeof safeData.security_index === "number"
      ? safeData.security_index
      : typeof safeData.security?.security_index === "number"
        ? safeData.security.security_index
        : null;
  const futureRisk = String(
    safeData.future_risk_rating || safeData.exploit_risk || safeData.security?.future_risk_rating || ""
  ).toUpperCase();

  const busyInfo = busy ? busyMeta(busy) : null;
  const status = busy ? "running" : safeData.status || "idle";
  const statusInfo = busyInfo
    ? { label: busyInfo.label, color: busyInfo.color, tone: busyInfo.tone }
    : statusMeta(status);

  const ledColor = busyInfo ? busyInfo.color : statusInfo.color;
  const ledGlow = busyInfo
    ? BUSY_GLOW[busy] || BUSY_GLOW.exec
    : STATUS_GLOW[status] || "transparent";

  const lastKind = output ? output.type : null;
  const previewLines = (safeData.code || safeData.source || "")
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.trim().length > 0)
    .slice(0, 2);
  const description = String(
    safeData.node_description ||
      (!isVulnerable ? safeData.description : "") ||
      meta.description ||
      ""
  ).trim();
  const complexity = safeData.complexity || "O(1)";

  return (
    <div
      className={cn(
        "group relative w-[280px] rounded-[8px] border border-[#E3E5E9] bg-[#FFFFFF] p-4",
        "shadow-card-subtle transition-all duration-200 hover:-translate-y-0.5 hover:shadow-card-hover",
        selected || isTracing
          ? "border-accent-blue shadow-glow-blue-strong"
          : isVulnerable
            ? "border-status-red/50 hover:border-status-red/70"
            : "hover:border-border-active"
      )}
    >
      {isTracing ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -inset-1 rounded-lg border-2 border-accent-blue/60 animate-trace-pulse"
        />
      ) : null}
      {isVulnerable && (severity === "CRITICAL" || severity === "HIGH") ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -inset-1 rounded-lg border-2 border-[#FF3B30]/60 animate-pulse"
          style={{ borderColor: "#FF3B30" }}
        />
      ) : null}
      {isVulnerable && severity === "MEDIUM" ? (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -inset-1 rounded-lg border-2 border-[#FFCC00]/70 animate-pulse"
          style={{ borderColor: "#FFCC00" }}
        />
      ) : null}
      <div
        className="absolute inset-x-0 top-0 h-[3px] rounded-t-lg"
        style={{
          background: isTracing
            ? "linear-gradient(90deg, #007AFF, transparent 88%)"
            : isVulnerable
              ? "linear-gradient(90deg, #FF3B30, transparent 88%)"
              : `linear-gradient(90deg, ${tierInfo?.color || meta.color}, transparent 88%)`,
        }}
      />

      <Handle
        type="target"
        position={Position.Top}
        className={HANDLE_CLASS}
        isConnectable={false}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        className={HANDLE_CLASS}
        isConnectable={false}
      />

      <div className="flex items-start gap-3">
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px] border border-[#E3E5E9] bg-[#F4F5F7]"
          style={{ color: meta.color }}
        >
          <Icon size={15} strokeWidth={2.2} />
        </span>

        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-bold tracking-tight text-[#0B0C0E]">
            {safeData.label ?? "Untitled node"}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <span
              className="rounded-full border px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider"
              style={{
                borderColor: `${meta.color}33`,
                backgroundColor: `${meta.color}0f`,
                color: meta.color,
              }}
            >
              {safeData.category || meta.label}
            </span>
            {isTracing ? (
              <span className="flex items-center gap-1 rounded-full border border-accent-blue/40 bg-accent-blue/10 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-accent-blue">
                <Activity size={10} />
                trace
              </span>
            ) : null}
            {isVulnerable ? (
              <span
                title={securityTip}
                className="flex items-center gap-1 rounded-full border border-status-red/40 bg-status-red/10 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-status-red"
              >
                <TriangleAlert size={10} />
                {severity}
              </span>
            ) : null}
            {isVulnerable && futureRisk ? (
              <span
                title={`Future exploit risk: ${futureRisk}`}
                className="flex items-center gap-1 rounded-full border border-[#FF3B30]/30 bg-white px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-[#FF3B30]"
              >
                risk: {futureRisk}
              </span>
            ) : null}
            {isVulnerable && securityIndex !== null ? (
              <span
                title={`Security index: ${securityIndex}/100`}
                className="rounded-full border border-revolut-border bg-white px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-revolut-textMain"
              >
                idx {securityIndex}
              </span>
            ) : null}
            <span className="rounded-full border border-revolut-border bg-white px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-text-secondary">
              {complexity}
            </span>
            {safeData.async ? (
              <span className="rounded-full border border-revolut-border bg-revolut-surface px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-text-secondary">
                async
              </span>
            ) : null}
            {safeData.diagnostic ? (
              <span
                title={String(safeData.diagnostic)}
                className="flex items-center rounded-full border border-status-orange/30 bg-status-orange/10 px-1.5 py-0.5 text-status-orange"
              >
                <TriangleAlert size={10} />
              </span>
            ) : null}
          </div>
        </div>

        <span className="flex shrink-0 items-center gap-1.5 pt-1">
          <span className="relative flex h-2 w-2 items-center justify-center">
            <span
              className="absolute inset-0 rounded-full opacity-60"
              style={{ backgroundColor: ledColor, boxShadow: `0 0 10px ${ledGlow}` }}
            />
            <span
              className={cn(
                "relative h-2 w-2 rounded-full",
                (busy || status === "running") && "animate-led-pulse"
              )}
              style={{ backgroundColor: ledColor }}
            />
          </span>
          {busy ? (
            <Loader2 size={11} className={cn("animate-spin", busyInfo.tone)} />
          ) : null}
          <span
            className={cn(
              "font-mono text-[9px] font-medium uppercase tracking-wider",
              statusInfo.tone
            )}
          >
            {statusInfo.label}
          </span>
        </span>
      </div>

      {safeData.file || safeData.module ? (
        <p
          title={String(safeData.file || safeData.module)}
          className="mt-2.5 truncate font-mono text-[9px] tracking-wide text-text-dimmed"
        >
          {safeData.module ? `${safeData.module} · ` : ""}
          {safeData.file || ""}
        </p>
      ) : null}

      {description ? (
        <p className="mt-3 line-clamp-2 text-[11px] leading-relaxed text-text-secondary">
          {description}
        </p>
      ) : null}

      {previewLines.length > 0 ? (
        <div className="mt-3 rounded-[8px] border border-[#E3E5E9] bg-[#F4F5F7] px-3 py-2">
          <pre className="max-h-[44px] overflow-hidden whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-text-secondary">
            {previewLines.join("\n")}
          </pre>
        </div>
      ) : null}

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: tierInfo?.color || "#5E6473" }}
          />
          <span className="truncate font-mono text-[9px] font-semibold uppercase tracking-wider text-[#0B0C0E]">
            T{tierInfo?.id || 3} · {tierInfo?.short || "Logic"}
          </span>
          <span className="truncate font-mono text-[9px] uppercase tracking-wider text-text-dimmed">
            {groupInfo?.short || lineLabel(safeData)}
          </span>
        </span>
        <span className="axiom-numeric shrink-0 truncate font-mono text-[9px] uppercase tracking-wider text-text-dimmed">
          {lastKind === "execute" && output
            ? `${output.report?.execution_time ?? "?"} ms`
            : lastKind === "mutate" && output
              ? output.report?.verdict ?? "n/a"
              : safeData.language || "unknown"}
        </span>
      </div>
    </div>
  );
}
