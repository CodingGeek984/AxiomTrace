"use client";

import { useEffect } from "react";
import {
  Activity,
  Pause,
  Play,
  RotateCcw,
  SkipBack,
  SkipForward,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { traceStepAt } from "@/lib/tracer";
import { useGraphStore } from "@/store/useGraphStore";

const FALLBACK_INTERVAL_MS = 900;

function DockButton({ label, icon: Icon, onClick, disabled }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex h-8 w-8 items-center justify-center rounded-full text-text-secondary transition-all duration-150 ease-in-out",
        "hover:bg-revolut-surface hover:text-revolut-textMain active:scale-95",
        disabled && "pointer-events-none opacity-30"
      )}
    >
      <Icon size={13} />
    </button>
  );
}

/**
 * Floating data-flow dock. Playback is driven by a single interval that reads
 * the latest tracer state through the store action, so advancing a step never
 * re-creates the timer or triggers a render loop.
 */
export default function DataTracer() {
  const nodeCount = useGraphStore((state) =>
    Array.isArray(state.nodes) ? state.nodes.length : 0
  );
  const tracer = useGraphStore((state) => state.tracer);
  const togglePlayback = useGraphStore((state) => state.toggleTracePlayback);
  const stepForward = useGraphStore((state) => state.stepTraceForward);
  const stepBack = useGraphStore((state) => state.stepTraceBack);
  const resetTrace = useGraphStore((state) => state.resetTrace);

  const active = Boolean(tracer && tracer.active);
  const playing = Boolean(tracer && tracer.playing);
  const steps = Array.isArray(tracer?.steps) ? tracer.steps : [];
  const total = steps.length;
  const index = Number.isInteger(tracer?.index) ? tracer.index : -1;
  const current = traceStepAt(tracer);
  const atEnd = total > 0 && index >= total - 1;
  const speed =
    tracer && Number(tracer.speed) > 0 ? Number(tracer.speed) : FALLBACK_INTERVAL_MS;
  const progress = active && total > 0 ? ((index + 1) / total) * 100 : 0;

  useEffect(() => {
    if (!playing || !active) return undefined;
    const timer = window.setInterval(() => {
      stepForward();
    }, speed);
    return () => window.clearInterval(timer);
  }, [playing, active, speed, stepForward]);

  if (nodeCount === 0) return null;

  const playLabel = playing
    ? "Pause trace"
    : active && atEnd
      ? "Replay trace"
      : "Play trace";
  const traceState = playing ? "playing" : active ? "paused" : "idle";

  return (
    <div className="pointer-events-none absolute inset-x-0 top-4 z-30 flex justify-center px-4">
      <div
        data-trace-state={traceState}
        data-trace-step={active ? index : -1}
        data-trace-total={total}
        className="pointer-events-auto relative flex items-center gap-1 rounded-full border border-revolut-border bg-white/95 p-1.5 pl-2.5 shadow-dock backdrop-blur-xl"
      >
        <div className="flex items-center gap-1.5 pr-1">
          <Activity
            size={12}
            className={cn(
              "shrink-0 transition-colors",
              playing ? "text-accent-blue" : "text-text-dimmed"
            )}
          />
          <div className="min-w-[72px]">
            <p className="font-mono text-[8px] font-semibold uppercase tracking-[0.18em] text-text-dimmed">
              data flow
            </p>
            <p
              aria-live="polite"
              className="axiom-numeric text-[11px] font-semibold leading-tight text-revolut-textMain"
            >
              {active && current ? `Step ${index + 1}/${total}` : "Ready"}
            </p>
          </div>
        </div>

        <span className="h-6 w-px shrink-0 bg-revolut-border" />

        <DockButton
          label="Step back"
          icon={SkipBack}
          onClick={stepBack}
          disabled={!active || index <= 0}
        />

        <button
          type="button"
          title={playLabel}
          aria-label={playLabel}
          aria-pressed={playing}
          onClick={togglePlayback}
          className={cn(
            "flex h-9 w-9 items-center justify-center rounded-full text-white transition-all duration-150 ease-in-out active:scale-95",
            playing
              ? "bg-accent-blue shadow-glow-blue hover:bg-[#0066d6]"
              : "bg-revolut-accentBlack hover:bg-black"
          )}
        >
          {playing ? (
            <Pause size={14} />
          ) : (
            <Play size={14} className="translate-x-px" />
          )}
        </button>

        <DockButton
          label="Step forward"
          icon={SkipForward}
          onClick={stepForward}
          disabled={total > 0 && atEnd && active}
        />

        <span className="mx-0.5 h-6 w-px shrink-0 bg-revolut-border" />

        <DockButton
          label="Reset trace"
          icon={RotateCcw}
          onClick={resetTrace}
          disabled={!active}
        />

        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-5 bottom-0.5 h-0.5 overflow-hidden rounded-full bg-revolut-surface"
        >
          <span
            className="block h-full rounded-full bg-accent-blue transition-[width] duration-300 ease-out"
            style={{ width: `${progress}%` }}
          />
        </span>
      </div>
    </div>
  );
}
