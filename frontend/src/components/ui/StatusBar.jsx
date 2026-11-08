"use client";

import { GitBranch } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";
import { useGraphStore } from "@/store/useGraphStore";
import { useReactFlow } from "@xyflow/react";

function bytesToMb(bytes) {
  if (typeof bytes !== "number") return null;
  return (bytes / (1024 * 1024)).toFixed(1);
}

/**
 * IDE-style status bar pinned to the bottom of the workspace.
 * Height 24px, light background, minimal labels.
 */
export default function StatusBar() {
  const nodes = useGraphStore((state) =>
    Array.isArray(state.nodes) ? state.nodes : []
  );
  const edges = useGraphStore((state) =>
    Array.isArray(state.edges) ? state.edges : []
  );
  const busy = useGraphStore((state) =>
    state.busy && typeof state.busy === "object" ? state.busy : {}
  );
  const executionLogs = useGraphStore((state) =>
    Array.isArray(state.executionLogs) ? state.executionLogs : []
  );
  const memoryEpoch = useGraphStore((state) => state.memoryEpoch || 0);

  const { getZoom } = useReactFlow();
  const [zoomPct, setZoomPct] = useState(100);
  const [memMb, setMemMb] = useState(null);

  useEffect(() => {
    const updateZoom = () => {
      if (typeof getZoom === "function") {
        const zoom = getZoom() || 1;
        setZoomPct(Math.round(zoom * 100));
      }
    };
    updateZoom();
    const interval = setInterval(updateZoom, 250);
    return () => clearInterval(interval);
  }, [getZoom]);

  useEffect(() => {
    if (typeof performance === "undefined" || !performance.memory) {
      return undefined;
    }
    const updateMem = () => {
      const mem = performance.memory;
      setMemMb(bytesToMb(mem.usedJSHeapSize));
    };
    // Re-sample immediately after a flush (GC has already been requested)
    // and then keep the IDE-style live counter ticking.
    updateMem();
    const timer = setInterval(updateMem, 1000);
    return () => clearInterval(timer);
  }, [memoryEpoch]);

  const busyCount = Object.values(busy).filter(Boolean).length;
  const logCount = executionLogs.length;

  return (
    <footer className="flex h-6 items-center justify-between border-t border-[#E3E5E9] bg-revolut-bg px-4 text-[11px] text-revolut-textMuted z-10">
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            "flex h-1.5 w-1.5 rounded-full bg-revolut-accentGreen",
            busyCount > 0 ? "animate-led-pulse" : ""
          )}
        />
        <span className="uppercase tracking-[0.16em] text-revolut-textMain">
          {busyCount > 0 ? `SANDBOX EXECUTING · ${busyCount}` : "SANDBOX IDLE"}
        </span>
      </div>

      <div className="mx-1 h-3 w-px bg-revolut-border" />

      <div className="flex items-center gap-1.5">
        <GitBranch size={10} />
        <span className="tabular-nums text-revolut-textMain">
          {nodes.length}N · {edges.length}E
        </span>
      </div>

      <div className="flex items-center gap-2">
        {memMb ? (
          <span className="tabular-nums">Memory Usage: {memMb} MB</span>
        ) : (
          <span className="tabular-nums">Memory Usage: -- MB</span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <span className="tabular-nums">Zoom: {zoomPct}%</span>
        <span>|</span>
        <span className="tabular-nums">Node Count: {nodes.length}</span>
        <span>|</span>
        <span className="tabular-nums">Logs: {logCount}</span>
      </div>
    </footer>
  );
}
