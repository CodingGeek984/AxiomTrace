"use client";

import { useEffect, useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import {
  Eraser,
  FlaskConical,
  LayoutTemplate,
  Loader2,
  Maximize2,
  Minus,
  MousePointer2,
  Play,
  Plus,
  Save,
  Scan,
  Sparkles,
  Trash2,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { SAMPLES } from "@/lib/samples";
import { useGraphStore } from "@/store/useGraphStore";

function ToolButton({ label, icon: Icon, active, disabled, danger, onClick }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "group relative flex h-10 w-10 items-center justify-center rounded-full transition-all duration-150 ease-in-out active:scale-95",
        active
          ? "bg-revolut-accentBlack text-white shadow-pill-light"
          : "text-text-secondary hover:bg-revolut-surface hover:text-revolut-textMain",
        danger && "hover:text-status-red",
        disabled && "pointer-events-none opacity-35"
      )}
    >
      <Icon size={16} />
      <span className="pointer-events-none absolute bottom-[calc(100%+14px)] left-1/2 z-20 hidden -translate-x-1/2 whitespace-nowrap rounded-full bg-revolut-accentBlack px-3 py-1.5 text-[10px] font-medium tracking-tight text-white shadow-panel group-hover:block">
        {label}
      </span>
    </button>
  );
}

function SampleMenu({ open, onOpenChange }) {
  const loadSample = useGraphStore((state) => state.loadSample);
  const notify = useGraphStore((state) => state.notify);
  const wrapRef = useRef(null);

  // Close on outside click or Escape so the popover cannot strand focus.
  useEffect(() => {
    if (!open) return undefined;

    const onPointerDown = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) {
        onOpenChange(false);
      }
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") onOpenChange(false);
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onOpenChange]);

  const handlePick = (sample) => {
    onOpenChange(false);
    loadSample(sample).catch(() => {
      // loadSample already notified the failure
    });
  };

  return (
    <div ref={wrapRef} className="relative">
      <ToolButton
        label="Load sample"
        icon={Sparkles}
        active={open}
        onClick={() => onOpenChange(!open)}
      />

      {open ? (
        <div
          role="menu"
          aria-label="Demo samples"
          className="absolute bottom-[calc(100%+16px)] left-1/2 z-30 w-80 -translate-x-1/2 overflow-hidden rounded-3xl border border-border-subtle bg-bg-elevated/95 p-2.5 shadow-panel backdrop-blur-2xl animate-fade-in-up"
        >
          <p className="px-3 pb-2 pt-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-text-dimmed">
            Demo sources
          </p>
          {SAMPLES.map((sample) => (
            <button
              key={sample.id}
              type="button"
              role="menuitem"
              onClick={() => handlePick(sample)}
              className="group flex w-full items-start gap-3 rounded-2xl px-3 py-2.5 text-left transition-colors hover:bg-revolut-surface"
            >
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-border-subtle bg-bg-input text-accent-blue transition-colors group-hover:border-accent-blue/40 group-hover:text-accent-blue">
                <FlaskConical size={13} />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-semibold text-revolut-textMain">
                  {sample.label}
                </span>
                <span className="mt-0.5 block truncate text-[11px] leading-relaxed text-text-secondary">
                  {sample.hint}
                </span>
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function Toolbar() {
  const { fitView, zoomIn, zoomOut, zoom } = useReactFlow();

  const lassoMode = useGraphStore((state) => state.lassoMode);
  const toggleLasso = useGraphStore((state) => state.toggleLasso);
  const autoLayout = useGraphStore((state) => state.autoLayout);
  const deleteSelected = useGraphStore((state) => state.deleteSelected);
  const runNode = useGraphStore((state) => state.runNode);
  const persistGraph = useGraphStore((state) => state.persistGraph);
  const notify = useGraphStore((state) => state.notify);
  const resetCanvas = useGraphStore((state) => state.resetCanvas);

  const nodes = useGraphStore((state) =>
    Array.isArray(state.nodes) ? state.nodes : []
  );
  const edges = useGraphStore((state) =>
    Array.isArray(state.edges) ? state.edges : []
  );
  const selectedIds = useGraphStore((state) =>
    Array.isArray(state.selectedIds) ? state.selectedIds : []
  );
  const busy = useGraphStore((state) =>
    state.busy && typeof state.busy === "object" ? state.busy : {}
  );
  const project = useGraphStore((state) => state.project);

  const [sampleOpen, setSampleOpen] = useState(false);

  const hasGraph = nodes.length > 0;
  const hasSelection = selectedIds.length > 0;
  const selectedId = selectedIds[0] || null;
  const isBusy = selectedId ? Boolean(busy[selectedId]) : false;
  const zoomPct = Math.round((zoom || 1) * 100);

  const handleRun = () => {
    if (selectedId) runNode(selectedId).catch(() => {});
  };

  const handleSave = async () => {
    if (!project) {
      notify("Parse code first - a project is created on first parse.", "error");
      return;
    }
    const saved = await persistGraph();
    if (saved === false) {
      notify("Save failed - the workspace could not be persisted.", "error");
      return;
    }
    notify(`Saved ${nodes.length} nodes · ${edges.length} edges`, "ok");
  };

  const handleClear = () => {
    if (resetCanvas()) notify("Canvas cleared.", "ok");
  };

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-6 z-20 flex justify-center px-4">
      <div className="pointer-events-auto flex items-center gap-0.5 rounded-full border border-border-subtle bg-bg-elevated/95 p-1.5 shadow-dock backdrop-blur-2xl">
        <ToolButton
          label={lassoMode ? "Pan mode" : "Lasso select"}
          icon={lassoMode ? MousePointer2 : Scan}
          active={lassoMode}
          onClick={toggleLasso}
        />
        <SampleMenu open={sampleOpen} onOpenChange={setSampleOpen} />
        <div className="mx-1 h-6 w-px bg-border-subtle" />
        <ToolButton
          label="Zoom out"
          icon={Minus}
          disabled={!hasGraph}
          onClick={() => zoomOut({ duration: 160 })}
        />
        <button
          type="button"
          title="Reset zoom to 100%"
          aria-label="Reset zoom to 100%"
          onClick={() => fitView({ padding: 0.25, duration: 350 })}
          disabled={!hasGraph}
          className="h-10 min-w-[52px] rounded-full px-2 font-mono text-[10px] font-semibold text-text-secondary transition-colors hover:bg-revolut-surface hover:text-revolut-textMain disabled:pointer-events-none disabled:opacity-35"
        >
          {zoomPct}%
        </button>
        <ToolButton
          label="Zoom in"
          icon={Plus}
          disabled={!hasGraph}
          onClick={() => zoomIn({ duration: 160 })}
        />
        <div className="mx-1 h-6 w-px bg-border-subtle" />
        <ToolButton
          label="Fit view"
          icon={Maximize2}
          disabled={!hasGraph}
          onClick={() => fitView({ padding: 0.25, duration: 350 })}
        />
        <ToolButton
          label="Auto layout"
          icon={LayoutTemplate}
          disabled={!hasGraph}
          onClick={autoLayout}
        />
        <ToolButton
          label="Run selected"
          icon={isBusy ? Loader2 : Play}
          disabled={!hasSelection || isBusy}
          onClick={handleRun}
        />
        <div className="mx-1 h-6 w-px bg-border-subtle" />
        <ToolButton
          label="Save graph"
          icon={Save}
          disabled={!hasGraph}
          onClick={handleSave}
        />
        <ToolButton
          label="Delete selected"
          icon={Trash2}
          disabled={!hasSelection}
          danger
          onClick={deleteSelected}
        />
        <ToolButton
          label="Clear canvas"
          icon={Eraser}
          disabled={!hasGraph}
          danger
          onClick={handleClear}
        />
        <div className="axiom-numeric ml-1 rounded-full border border-border-subtle bg-bg-input px-3 py-1.5 font-mono text-[10px] text-text-secondary">
          {edges.length}e / {nodes.length}n
        </div>
      </div>
    </div>
  );
}
