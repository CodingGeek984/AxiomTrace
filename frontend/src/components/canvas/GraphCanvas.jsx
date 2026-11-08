"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  SelectionMode,
  useReactFlow,
} from "@xyflow/react";
import { Network, ScanSearch } from "lucide-react";

import CustomNode from "@/components/canvas/CustomNode";
import DataTracer from "@/components/canvas/DataTracer";
import SwimlaneNode from "@/components/canvas/SwimlaneNode";
import { COLORS, FIT_VIEW, TIERS, typeMeta } from "@/config/theme";
import { buildSwimlanes } from "@/lib/graph";
import { cn } from "@/lib/cn";
import { SAMPLES } from "@/lib/samples";
import { createTraceStream } from "@/lib/stream";
import { traceStepAt } from "@/lib/tracer";
import { useGraphStore } from "@/store/useGraphStore";

const TRACE_STREAM_URL = process.env.NEXT_PUBLIC_TRACE_STREAM_URL || "";

export default function GraphCanvas() {
  const nodes = useGraphStore((state) => (Array.isArray(state.nodes) ? state.nodes : []));
  const edges = useGraphStore((state) => (Array.isArray(state.edges) ? state.edges : []));
  const lassoMode = useGraphStore((state) => Boolean(state.lassoMode));
  const parseStatus = useGraphStore((state) => state.parseStatus || "idle");
  const project = useGraphStore((state) => state.project);
  const graphEpoch = useGraphStore((state) => state.graphEpoch || 0);
  const fitViewRequest = useGraphStore((state) => state.fitViewRequest);
  const tracer = useGraphStore((state) => state.tracer);
  // Groups remain in the store for the Inspector/legend metadata; the
  // canvas itself renders tiers + swimlanes derived from node metadata.
  const impactScore = useGraphStore((state) => state.impactScore || 0);
  const toggleImpact = useGraphStore((state) => state.toggleImpact);

  const onNodesChange = useGraphStore((state) => state.onNodesChange);
  const onEdgesChange = useGraphStore((state) => state.onEdgesChange);
  const onConnect = useGraphStore((state) => state.onConnect);
  const setSelection = useGraphStore((state) => state.setSelection);
  const setInspectorTab = useGraphStore((state) => state.setInspectorTab);
  const undo = useGraphStore((state) => state.undo);
  const redo = useGraphStore((state) => state.redo);
  const persistGraph = useGraphStore((state) => state.persistGraph);
  const loadSample = useGraphStore((state) => state.loadSample);
  const deleteSelected = useGraphStore((state) => state.deleteSelected);

  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const canvasRef = useRef(null);

  const nodeTypes = useMemo(
    () => ({ axiom: CustomNode, swimlane: SwimlaneNode }),
    []
  );

  // Swimlane bands are derived render-only nodes (never stored), so deselect
  // clicks on empty band space behave exactly like canvas clicks.
  const renderNodes = useMemo(
    () => [...buildSwimlanes(nodes), ...nodes],
    [nodes]
  );

  const handleNodesChange = useCallback(
    (changes) => {
      const filtered = (Array.isArray(changes) ? changes : []).filter(
        (change) => !String(change?.id || "").startsWith("swimlane-")
      );
      if (filtered.length > 0) onNodesChange(filtered);
    },
    [onNodesChange]
  );

  // Clean label tags: keep only meaningful cross-tier connectors, drop the
  // noisy generic "imports" label and same-tier clutter.
  const renderEdges = useMemo(() => {
    const tierById = new Map(
      nodes.map((node) => [String(node.id), Number(node.data?.tier) || 3])
    );
    return edges.map((edge) => {
      const sourceTier = tierById.get(String(edge.source));
      const targetTier = tierById.get(String(edge.target));
      const crossTier = Boolean(sourceTier && targetTier && sourceTier !== targetTier);
      const generic =
        !edge.label || edge.label === "imports" || edge.label === "import";
      return {
        ...edge,
        type: "smoothstep",
        markerEnd: {
          type: "arrowclosed",
          color: "#5E6473",
          width: 18,
          height: 18,
        },
        label: crossTier
          ? generic
            ? `Tier ${sourceTier} → ${targetTier}`
            : edge.label
          : generic
            ? undefined
            : edge.label,
      };
    });
  }, [edges, nodes]);

  const tierCounts = useMemo(() => {
    const counts = new Map();
    for (const node of nodes) {
      const tier = Number(node?.data?.tier) || 3;
      counts.set(tier, (counts.get(tier) || 0) + 1);
    }
    return counts;
  }, [nodes]);

  const defaultEdgeOptions = useMemo(
    () => ({
      type: "smoothstep",
      animated: false,
      style: { stroke: COLORS.borderActive, strokeWidth: 1.6 },
      markerEnd: {
        type: "arrowclosed",
        color: "#5E6473",
        width: 18,
        height: 18,
      },
      labelStyle: { fill: COLORS.textSecondary, fontSize: 10, fontWeight: 600 },
      labelBgStyle: { fill: "#FFFFFF", fillOpacity: 1 },
      labelBgPadding: [6, 3],
      labelBgBorderRadius: 4,
    }),
    []
  );

  const onSelectionChange = useCallback(
    ({ nodes: selected }) => {
      setSelection(selected.map((node) => node.id));
    },
    [setSelection]
  );

  const onNodeDoubleClick = useCallback(() => {
    setInspectorTab("code");
  }, [setInspectorTab]);

  const isValidConnection = useCallback(
    (connection) =>
      Boolean(connection.source) &&
      Boolean(connection.target) &&
      connection.source !== connection.target,
    []
  );

  // ------------------------------------------------------------------
  // Impact scoring for what-if modifications
  // ------------------------------------------------------------------
  const calculateImpactScore = useCallback((oldEdges, newEdges, movedNodeId) => {
    // Simple impact calculation: count affected downstream nodes via BFS
    const affectedNodes = new Set();

    // Build adjacency map from new edges
    const adjacency = {};
    for (const edge of newEdges) {
      adjacency[edge.source] = adjacency[edge.source] || [];
      adjacency[edge.source].push(edge.target);
    }

    // BFS from moved node through edges
    const startId = movedNodeId || "";
    const queue = [startId];
    while (queue.length > 0) {
      const current = queue.shift();
      if (!current || affectedNodes.has(current)) continue;
      affectedNodes.add(current);
      for (const next of (adjacency[current] || [])) {
        if (!affectedNodes.has(next)) {
          queue.push(next);
        }
      }
    }

    // Impact score: number of affected nodes, capped at 100
    return Math.min(affectedNodes.size * 10, 100);
  }, []);

  // ------------------------------------------------------------------
  // Workspace switching: drop the cached viewport transform and the
  // measured node cache of the previous workspace, then re-fit the new
  // graph. The pending frame is cancelled on cleanup so a superseded
  // fit animation cannot land after the workspace moved on.
  // ------------------------------------------------------------------
  const workspaceKey = project ? String(project.id || project.name) : "";

  // Single auto-fit entrypoint: reactFlowInstance.fitView({ duration: 800,
  // padding: 0.25 }) centers the swimlane blueprint at a readable zoom.
  const fitBlueprint = useCallback(
    () => fitView({ duration: FIT_VIEW.duration, padding: FIT_VIEW.padding, maxZoom: FIT_VIEW.maxZoom }),
    [fitView]
  );

  useEffect(() => {
    const currentNodes = useGraphStore.getState().nodes;
    if (!Array.isArray(currentNodes) || currentNodes.length === 0) {
      return undefined;
    }
    // A dedicated Auto-Architect/merge request owns this viewport change.
    if (fitViewRequest && fitViewRequest.graphEpoch === graphEpoch) {
      return undefined;
    }

    let frame = 0;
    let timer = 0;

    // Wait for React Flow to mount and measure the fresh nodes before
    // fitting; a single animation frame can still run before dimensions are
    // available on slower machines, so a short timer acts as a fallback.
    // Auto-fit centers at an optimal ~85% zoom (never crushed to 15%).
    frame = requestAnimationFrame(() => {
      timer = window.setTimeout(() => {
        fitBlueprint();
      }, 40);
    });

    return () => {
      if (frame) cancelAnimationFrame(frame);
      if (timer) window.clearTimeout(timer);
    };
  }, [workspaceKey, graphEpoch, fitBlueprint, fitViewRequest]);

  // ------------------------------------------------------------------
  // Auto-Architect / incremental-merge fit request. React Flow measures
  // nodes asynchronously, so wait one frame plus a short timer before
  // animating the combined graph into view.
  // ------------------------------------------------------------------
  useEffect(() => {
    if (!fitViewRequest || !fitViewRequest.id) return undefined;

    let frame = 0;
    let timer = 0;
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        timer = window.setTimeout(() => {
          fitView({
            duration: fitViewRequest.duration ?? FIT_VIEW.duration,
            padding: fitViewRequest.padding ?? FIT_VIEW.padding,
            maxZoom: FIT_VIEW.maxZoom,
          });
        }, 50);
      });
    });

    return () => {
      if (frame) cancelAnimationFrame(frame);
      if (timer) window.clearTimeout(timer);
    };
  }, [fitViewRequest, fitView]);

  // ------------------------------------------------------------------
  // Keyboard shortcuts. The listener is removed on unmount so no stale
  // closure keeps the graph alive.
  // ------------------------------------------------------------------
  useEffect(() => {
    const onKeyDown = (event) => {
      const target = event.target;
      const tag = target && target.tagName ? target.tagName.toLowerCase() : "";
      const isTyping =
        tag === "input" ||
        tag === "textarea" ||
        tag === "select" ||
        Boolean(target && target.isContentEditable);

      // Delete/Backspace removes the selection, but never while a field has
      // focus - the editor owns those keys in that case.
      if (!isTyping && !event.metaKey && !event.ctrlKey) {
        if (event.key === "Delete" || event.key === "Backspace") {
          if ((useGraphStore.getState().selectedIds || []).length > 0) {
            event.preventDefault();
            deleteSelected();
          }
          return;
        }
      }

      if (isTyping) return;

      const meta = event.metaKey || event.ctrlKey;
      if (!meta) return;

      const key = event.key.toLowerCase();

      if (key === "z") {
        event.preventDefault();
        if (event.shiftKey) {
          redo();
        } else {
          undo();
        }
        return;
      }

      if (key === "y") {
        event.preventDefault();
        redo();
        return;
      }

      if (key === "s") {
        event.preventDefault();
        persistGraph().catch(() => {});
        return;
      }

      if (key === "=" || key === "+") {
        event.preventDefault();
        zoomIn({ duration: 160 });
        return;
      }

      if (key === "-" || key === "_") {
        event.preventDefault();
        zoomOut({ duration: 160 });
        return;
      }

      if (key === "0") {
        event.preventDefault();
        fitBlueprint();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [undo, redo, persistGraph, deleteSelected, zoomIn, zoomOut, fitBlueprint]);

  // ------------------------------------------------------------------
  // Canvas click handler. Registered through an AbortController so the
  // listener is detached together with its signal on unmount.
  // ------------------------------------------------------------------
  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return undefined;

    const controller = new AbortController();

    const onCanvasPointerDown = (event) => {
      const target = event.target;
      if (target && typeof target.closest === "function") {
        // Swimlane bands are background decoration: clicks on them deselect,
        // just like empty canvas space.
        if (!target.closest(".swimlane-band")) {
          if (
            target.closest(
              ".react-flow__node, .react-flow__minimap, .react-flow__controls, .react-flow__panel"
            )
          ) {
            return;
          }
        }
      }
      setSelection([]);
    };

    element.addEventListener("pointerdown", onCanvasPointerDown, {
      signal: controller.signal,
    });

    return () => {
      controller.abort();
    };
  }, [setSelection]);

  // ------------------------------------------------------------------
  // Optional live trace stream (SSE / WebSocket). The transport is
  // closed explicitly when the workspace changes or the canvas unmounts,
  // so no socket keeps the session alive.
  // ------------------------------------------------------------------
  useEffect(() => {
    if (!TRACE_STREAM_URL || !workspaceKey) return undefined;

    const stream = createTraceStream({
      url: TRACE_STREAM_URL,
      onMessage: (payload) => {
        useGraphStore.getState().appendExecutionLog({
          level: "info",
          text: typeof payload === "string" ? payload : JSON.stringify(payload),
        });
      },
    });

    return () => {
      stream.close();
    };
  }, [workspaceKey]);

  const miniMapColor = useCallback((node) => {
    const data =
      node && node.data && typeof node.data === "object" ? node.data : {};
    return typeMeta(data.type || data.semantic_type || "action").color;
  }, []);

  const isEmpty = (Array.isArray(nodes) ? nodes.length : 0) === 0;

  return (
    <div ref={canvasRef} className="relative min-w-0 flex-1 overflow-hidden bg-revolut-bg">
      <ReactFlow
        nodes={renderNodes}
        edges={renderEdges}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onSelectionChange={onSelectionChange}
        onNodeDoubleClick={onNodeDoubleClick}
        nodeTypes={nodeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        isValidConnection={isValidConnection}
        fitView
        fitViewOptions={{ padding: FIT_VIEW.padding, duration: FIT_VIEW.duration, maxZoom: FIT_VIEW.maxZoom }}
        minZoom={0.25}
        maxZoom={2.5}
        selectionOnDrag={lassoMode}
        panOnDrag={lassoMode ? [1, 2] : true}
        selectionMode={SelectionMode.Partial}
        multiSelectionKeyCode={["Meta", "Control", "Shift"]}
        deleteKeyCode={null}
        proOptions={{ hideAttribution: true }}
        className={cn(lassoMode && "axiom-lasso")}
        aria-label="AST graph canvas"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={18}
          size={1}
          color="#E3E5E9"
        />
        <Controls
          showInteractive={false}
          position="bottom-center"
          className="axiom-controls !bottom-6 !left-1/2 !-translate-x-1/2 !hidden sm:!flex rounded-full border border-[#E3E5E9] bg-[#F4F5F7] shadow-sm"
        />
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          nodeColor={miniMapColor}
          nodeStrokeWidth={3}
          maskColor="rgba(244, 245, 247, 0.9)"
          style={{ backgroundColor: "#F4F5F7" }}
          className="axiom-minimap hidden xl:block !bottom-6 !right-6 w-40 h-28 rounded-xl shadow-sm border border-[#E3E5E9] overflow-hidden"
        />
      </ReactFlow>

      {/* Architecture tiers legend - click a tier to select its nodes */}
      {tierCounts.size > 0 && (
        <div className="absolute left-3 top-3 z-10 hidden w-52 flex-col gap-0.5 rounded-lg border border-[#E3E5E9] bg-white/90 p-2 shadow-sm backdrop-blur md:flex">
          <p className="px-1.5 pb-1 text-[9px] font-semibold uppercase tracking-[0.18em] text-text-dimmed">
            Architecture tiers
          </p>
          {TIERS.map((tier) => {
            const count = tierCounts.get(tier.id) || 0;
            if (count === 0) return null;
            return (
              <button
                key={tier.id}
                type="button"
                title={tier.description}
                onClick={() =>
                  setSelection(
                    nodes
                      .filter((node) => (Number(node.data?.tier) || 3) === tier.id)
                      .map((node) => node.id)
                  )
                }
                className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-left transition-colors hover:bg-[#F4F5F7]"
              >
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md border border-[#E3E5E9] bg-white font-mono text-[9px] font-bold text-[#0B0C0E]">
                  T{tier.id}
                </span>
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: tier.color }}
                />
                <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-revolut-textMain">
                  {tier.short}
                </span>
                <span className="axiom-numeric shrink-0 font-mono text-[9px] text-text-dimmed">
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      )}

      <DataTracer />

      {isEmpty ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="pointer-events-auto flex flex-col items-center gap-2 text-center">
            <Network size={18} className="text-revolut-textMuted" />
            <p className="text-xs text-revolut-textMain">No graph loaded</p>
            <p className="text-[11px] text-revolut-textMuted">
              Parse Code from the header to build the workspace
            </p>
          </div>
        </div>
      ) : null}

      {/* Impact score status pill for what-if modifications */}
      {impactScore > 0 && (
        <div
          className="pointer-events-none absolute inset-x-0 bottom-14 z-20 flex items-center justify-center px-4"
        >
          <div
            className={cn(
              "rounded-full border px-4 py-2 text-xs font-semibold uppercase tracking-wider",
              impactScore > 50
                ? "border-status-red/40 bg-status-red/10 text-status-red"
                : impactScore > 25
                  ? "border-status-orange/40 bg-status-orange/10 text-status-orange"
                  : "border-status-green/40 bg-status-green/10 text-status-green"
            )}
          >
            Impact: {impactScore}
          </div>
        </div>
      )}
    </div>
  );
}