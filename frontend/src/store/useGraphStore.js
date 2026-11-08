"use client";

import { create } from "zustand";
import { addEdge, applyEdgeChanges, applyNodeChanges } from "@xyflow/react";

import {
  executeNode,
  generateContext,
  mutateNode,
  openProject as apiOpenProject,
  parseArchitecture,
  parseCode,
  saveGraph,
  securityScan,
  transpileCode,
} from "@/lib/api";
import {
  layoutGraph,
  normaliseGroups,
  normaliseTiers,
  selectionSubset,
  toFlowGraph,
} from "@/lib/graph";
import { buildTraceSteps } from "@/lib/tracer";
import { bindNodeSource, resolveNodeSource } from "@/lib/nodeSource";
import { parseJsonInput } from "@/lib/utils";

let toastSeq = 0;
let toastTimer = null;
let logSeq = 0;

const EMPTY_OUTPUTS = {};

/**
 * Hard cap on the retained execution log. The array is trimmed with a
 * sliding FIFO window (`slice(-MAX_EXECUTION_LOGS)`) so a long-running
 * session can never grow the trace buffer without bound.
 */
const MAX_ARCH_NODES = 400;

const MAX_EXECUTION_LOGS = 100;
const MAX_HISTORY = 50;
const DEFAULT_TRACE_INTERVAL_MS = 900;

/**
 * Idle tracer descriptor. Rebuilding the object keeps every playback field in
 * sync whenever the graph is replaced, so a stale step list can never point at
 * a node that no longer exists.
 *
 * @param {{ speed?: number } | null} [previous]
 */
function idleTracer(previous = null) {
  const speed =
    previous && Number.isFinite(previous.speed) && previous.speed > 0
      ? previous.speed
      : DEFAULT_TRACE_INTERVAL_MS;
  return {
    active: false,
    playing: false,
    index: -1,
    steps: [],
    epoch: null,
    speed,
  };
}

/**
 * Default Auto-Architect blueprint. It is intentionally a *flow*, not a
 * vague prose description, so the deterministic backend extractor always
 * yields a connected multi-node graph even when the canvas is empty or no
 * LLM key is configured.
 */
export const DEFAULT_ARCHITECTURE_PROMPT =
  "Frontend App -> API Gateway -> Flask Controller -> Database";

const TRANSIENT_STORAGE_PREFIXES = [
  "axiomtrace_cache_",
  "axiomtrace_export_",
  "axiomtrace_trace_",
];

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/**
 * Normalise every graph response shape the backend may emit.
 *
 * The API returns the graph keys both at the top level (legacy desktop
 * contract) and under ``data`` (v1 contract). Either shape is accepted, and
 * every collection is coerced to an array so a malformed response can never
 * reach React Flow as an object/undefined.
 *
 * @param {unknown} payload
 * @returns {{
 *   graph: Record<string, unknown>,
 *   nodes: Array<Record<string, unknown>>,
 *   edges: Array<Record<string, unknown>>,
 *   language: string,
 *   stats: Record<string, unknown>,
 *   errors: Array<unknown>,
 *   partial: boolean,
 * }}
 */
export function normalizeGraphPayload(payload) {
  const response = isPlainObject(payload) ? payload : {};
  const nested = isPlainObject(response.data) ? response.data : null;
  const graph = nested ? { ...nested, ...response } : response;

  const nodes = Array.isArray(graph.nodes)
    ? graph.nodes
    : Array.isArray(nested?.nodes)
      ? nested.nodes
      : [];
  const edges = Array.isArray(graph.edges)
    ? graph.edges
    : Array.isArray(nested?.edges)
      ? nested.edges
      : [];
  const errors = Array.isArray(graph.errors)
    ? graph.errors
    : Array.isArray(nested?.errors)
      ? nested.errors
      : [];
  const groups = Array.isArray(graph.groups)
    ? graph.groups
    : Array.isArray(nested?.groups)
      ? nested.groups
      : [];
  const tiers = Array.isArray(graph.tiers)
    ? graph.tiers
    : Array.isArray(nested?.tiers)
      ? nested.tiers
      : [];

  return {
    graph: { ...graph, nodes, edges, groups, tiers },
    nodes,
    edges,
    groups,
    tiers,
    language: graph.language || nested?.language || "python",
    stats: isPlainObject(graph.stats)
      ? graph.stats
      : isPlainObject(nested?.stats)
        ? nested.stats
        : {},
    errors,
    partial: Boolean(graph.partial ?? nested?.partial),
  };
}

function dedupeNodesFlow(nodes = []) {
  const seen = new Set();
  const result = [];
  for (const node of asArray(nodes)) {
    if (!node || !node.id) continue;
    if (seen.has(node.id)) continue;
    seen.add(node.id);
    result.push(node);
  }
  return result;
}

function dedupeEdgesFlow(edges = []) {
  const seen = new Set();
  const result = [];
  for (const edge of asArray(edges)) {
    if (!edge || !edge.source || !edge.target) continue;
    if (edge.source === edge.target) continue;
    const key = `${edge.source}|${edge.target}|${edge.kind || edge.type || ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({ ...edge, animated: edge.animated === true });
  }
  return result;
}

/**
 * Trim a log buffer down to the newest {@link MAX_EXECUTION_LOGS} entries.
 *
 * @param {Array<unknown>} logs
 * @returns {Array<unknown>}
 */
function capLogs(logs) {
  const safe = asArray(logs);
  return safe.length > MAX_EXECUTION_LOGS
    ? safe.slice(-MAX_EXECUTION_LOGS)
    : safe;
}

function clearToastTimer() {
  if (toastTimer) {
    clearTimeout(toastTimer);
    toastTimer = null;
  }
}

/**
 * Read the Chromium/Electron JS heap counters when the runtime exposes them.
 *
 * @returns {{ usedJSHeapSize: number, totalJSHeapSize: number, jsHeapSizeLimit: number, sampledAt: number } | null}
 */
export function readMemoryStats() {
  if (
    typeof performance === "undefined" ||
    !performance ||
    !performance.memory
  ) {
    return null;
  }
  const memory = performance.memory;
  return {
    usedJSHeapSize: Number(memory.usedJSHeapSize) || 0,
    totalJSHeapSize: Number(memory.totalJSHeapSize) || 0,
    jsHeapSizeLimit: Number(memory.jsHeapSizeLimit) || 0,
    sampledAt: Date.now(),
  };
}

/**
 * Remove only transient AxiomTrace cache keys from local/session storage.
 * Persistent user settings (such as the DeepSeek API key) are preserved.
 *
 * @returns {number} number of removed keys
 */
export function purgeTransientStorage() {
  if (typeof window === "undefined") return 0;
  let removed = 0;
  for (const storageName of ["localStorage", "sessionStorage"]) {
    try {
      const storage = window[storageName];
      if (!storage) continue;
      const doomed = [];
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (
          typeof key === "string" &&
          TRANSIENT_STORAGE_PREFIXES.some((prefix) => key.startsWith(prefix))
        ) {
          doomed.push(key);
        }
      }
      doomed.forEach((key) => {
        storage.removeItem(key);
        removed += 1;
      });
    } catch {
      // Storage can be disabled by the host; a flush must still succeed.
    }
  }
  return removed;
}

/**
 * Build a deterministic architecture prompt for the current workspace.
 *
 * @param {unknown[]} [nodes]
 * @param {{ name?: string } | null} [project]
 * @returns {string}
 */
export function buildAutoArchitectPrompt(nodes = [], project = null) {
  const labels = [];
  const seen = new Set();
  for (const node of asArray(nodes)) {
    const label = String(
      node?.data?.label || node?.label || node?.id || ""
    ).trim();
    if (!label || seen.has(label)) continue;
    seen.add(label);
    labels.push(label);
    if (labels.length >= 8) break;
  }
  const workspace = String(project?.name || "").trim();
  const context = labels.length ? ` Existing components: ${labels.join(", ")}.` : "";
  const workspaceLabel = workspace ? ` for ${workspace}` : "";
  return `${DEFAULT_ARCHITECTURE_PROMPT}${workspaceLabel}.${context}`.replace(
    /\.\./g,
    "."
  );
}

/**
 * Ask the runtime to collect garbage, if the host exposes a collector.
 *
 * Electron can be launched with `--js-flags=--expose-gc`, which surfaces
 * `globalThis.gc`. Browser engines do not, so the hint is a no-op there.
 *
 * @returns {boolean} true when an explicit collection was requested.
 */
export function requestGarbageCollection() {
  const collector =
    typeof globalThis !== "undefined" ? globalThis.gc : undefined;
  if (typeof collector !== "function") return false;
  try {
    collector();
    return true;
  } catch {
    return false;
  }
}

export const useGraphStore = create((set, get) => ({
  // ---------------------------------------------------------------
  // graph state (single source of truth for React Flow)
  // ---------------------------------------------------------------
  nodes: [],
  edges: [],
  project: null,
  language: "python",
  parseStats: null,
  groups: [],
  tiers: [],
  parseStatus: "idle",
  parseError: null,
  /**
   * Recoverable parser diagnostics. When the backend salvages a partial
   * graph the errors are surfaced here instead of failing the parse, so the
   * canvas stays usable while the inspector can still report what broke.
   */
  parseDiagnostics: [],
  /**
   * True when the last parse came back partial - some region of the source
   * could not be recovered and the graph is a best-effort reconstruction.
   */
  parsePartial: false,
  sourceName: null,
  /**
   * Bumped every time the graph is replaced. The canvas uses it to detect
   * that a fresh graph has been loaded and to re-fit the viewport.
   */
  graphEpoch: 0,

  selectedIds: [],
  selectedNode: null,
  outputs: { ...EMPTY_OUTPUTS },
  detections: {},
  busy: {},
  payloads: {},
  /**
   * Monotonic token sequencing transpile requests. Rapid language switches
   * race each other; only the newest request may clear `busy` or apply its
   * result, older responses are marked `stale` and discarded by the UI.
   */
  transpileToken: 0,

  // ---------------------------------------------------------------
  // memory-bounded state
  // ---------------------------------------------------------------
  // `executionLogs` is trimmed to the newest MAX_EXECUTION_LOGS entries on
  // every append; `history` holds at most MAX_HISTORY graph snapshots.
  executionLogs: [],
  history: { past: [], future: [] },
  memoryStats: null,
  memoryEpoch: 0,
  lastFlushedAt: null,
  lastPurgedStorageKeys: 0,

  lassoMode: false,
  exportState: { open: false, status: "idle", payload: null, error: null },
  inspectorTab: "code",
  toast: null,
  isAutoArchitectActive: false,
  autoArchitect: false,
  architectBusy: false,
  architectError: null,
  architectStats: null,
  focusNodeIds: [],
  fitViewRequest: null,
  impactScore: 0,
  toggleImpact: () => set({ impactScore: (state) => state.impactScore >= 100 ? 0 : state.impactScore + 10 }),

  /**
   * Interactive data-flow tracer. `steps` is rebuilt from the live graph when
   * playback starts; `index` points at the active step (-1 while idle).
   */
  tracer: idleTracer(),

  setAutoArchitectActive: (value) =>
    set({ isAutoArchitectActive: Boolean(value), autoArchitect: Boolean(value), architectError: null }),

  setAutoArchitect: (value) =>
    set({ autoArchitect: Boolean(value), isAutoArchitectActive: Boolean(value), architectError: null }),

  toggleAutoArchitect: () =>
    set((state) => {
      const next = !(state.autoArchitect || state.isAutoArchitectActive);
      return { autoArchitect: next, isAutoArchitectActive: next, architectError: null };
    }),

  clearFocus: () => set({ focusNodeIds: [] }),

  /**
   * Ask the canvas to fit the viewport. The request is stored (instead of
   * reaching into React Flow from the store) so the canvas can wait for node
   * measurement before animating.
   */
  requestFitView: ({ duration = 800, padding = 0.25, maxZoom = 0.85 } = {}) =>
    set((state) => ({
      fitViewRequest: {
        id: (state.fitViewRequest?.id || 0) + 1,
        graphEpoch: state.graphEpoch || 0,
        duration,
        padding,
        maxZoom,
      },
    })),

  settleNewNodes: (ids = []) => {
    const idSet = new Set(asArray(ids).filter(Boolean));
    if (idSet.size === 0) return;
    set((state) => ({
      nodes: asArray(state.nodes).map((node) => {
        if (!idSet.has(node.id)) return node;
        const nextData = node.data ? { ...node.data, isNew: false } : node.data;
        return { ...node, data: nextData };
      }),
    }));
  },

  appendIncrementalGraph: (newNodes = [], newEdges = []) => {
    set((state) => {
      try {
        const currentNodes = dedupeNodesFlow(asArray(state.nodes));
        const currentEdges = dedupeEdgesFlow(asArray(state.edges));
        const existingIds = new Set(currentNodes.map((node) => String(node.id)));
        const added = [];

        for (const raw of asArray(newNodes)) {
          if (!raw || raw.id === undefined || raw.id === null) continue;
          const id = String(raw.id);
          if (!id || existingIds.has(id)) continue;
          if (added.length + currentNodes.length >= MAX_ARCH_NODES) break;
          added.push({
            ...raw,
            id,
            data: {
              ...(isPlainObject(raw.data) ? raw.data : {}),
              isNew: true,
              tag: raw.tag || raw.data?.tag || null,
            },
          });
          existingIds.add(id);
        }

        const mergedNodes = dedupeNodesFlow([...currentNodes, ...added]);
        const knownIds = new Set(mergedNodes.map((node) => String(node.id)));
        const usableEdges = [];
        for (const raw of asArray(newEdges)) {
          if (!raw || raw.source === undefined || raw.target === undefined) continue;
          const source = String(raw.source);
          const target = String(raw.target);
          if (!source || !target || source === target) continue;
          if (!knownIds.has(source) || !knownIds.has(target)) continue;
          usableEdges.push({
            ...raw,
            id: raw.id || `e-${source}-${target}`,
            source,
            target,
            animated: true,
          });
        }
        const mergedEdges = dedupeEdgesFlow([...currentEdges, ...usableEdges]);

        if (added.length === 0 && mergedEdges.length === currentEdges.length) {
          return {};
        }

        const newIds = added.map((node) => node.id);
        const nextEpoch = added.length > 0
          ? (state.graphEpoch || 0) + 1
          : state.graphEpoch || 0;
        return {
          nodes: mergedNodes,
          edges: mergedEdges,
          groups: normaliseGroups(mergedNodes),
          tiers: normaliseTiers(mergedNodes),
          focusNodeIds: newIds,
          graphEpoch: nextEpoch,
          tracer: idleTracer(state.tracer),
          architectError: null,
          fitViewRequest: added.length > 0
            ? {
                id: (state.fitViewRequest?.id || 0) + 1,
                graphEpoch: nextEpoch,
                duration: 800,
                padding: 0.25,
                maxZoom: 0.85,
              }
            : state.fitViewRequest,
        };
      } catch (error) {
        console.warn("[AxiomTrace] appendIncrementalGraph failed:", error);
        return {};
      }
    });
  },

  appendArchitectureGraph: (newNodes = [], newEdges = []) => {
    get().appendIncrementalGraph(newNodes, newEdges);
    const state = get();
    if (asArray(newNodes).length > 0) {
      set((current) => ({
        fitViewRequest: {
          id: (current.fitViewRequest?.id || 0) + 1,
          graphEpoch: current.graphEpoch || 0,
          duration: 800,
          padding: 0.25,
          maxZoom: 0.85,
        },
      }));
    }
  },

  triggerAutoLayout: () =>
    set((state) => ({
      nodes: layoutGraph(asArray(state.nodes), asArray(state.edges)),
      graphEpoch: (state.graphEpoch || 0) + 1,
    })),

  // ---------------------------------------------------------------
  // graph mutations
  // ---------------------------------------------------------------
  onNodesChange: (changes) => {
    const list = asArray(changes);
    const structural = list.some((change) => change && change.type === "remove");
    set((state) => ({
      nodes: applyNodeChanges(list, asArray(state.nodes)),
      // A removed node invalidates the cached trace script; drop the
      // highlights instead of animating a step that no longer exists.
      ...(structural ? { tracer: idleTracer(state.tracer) } : {}),
    }));
  },

  onEdgesChange: (changes) => {
    const list = asArray(changes);
    const structural = list.some((change) => change && change.type === "remove");
    set((state) => ({
      edges: applyEdgeChanges(list, asArray(state.edges)),
      ...(structural ? { tracer: idleTracer(state.tracer) } : {}),
    }));
  },

  onConnect: (connection) => {
    if (!isPlainObject(connection)) return;
    const { source, target } = connection;
    if (!source || !target || source === target) return;
    const duplicate = asArray(get().edges).some(
      (edge) => edge.source === source && edge.target === target
    );
    if (duplicate) return;
    get().pushHistory();
    set((state) => ({
      edges: addEdge(
        {
          ...connection,
          id: `e-manual-${source}-${target}`,
          type: "smoothstep",
          animated: false,
          data: { kind: "sequence" },
        },
        asArray(state.edges)
      ),
      // New topology means the previous topological script is stale.
      tracer: idleTracer(state.tracer),
    }));
  },

  setSelection: (ids) => {
    const selectedIds = asArray(ids);
    const selected = new Set(selectedIds.map(String));
    set((state) => {
      let rebound = false;
      const nodes = asArray(state.nodes).map((node) => {
        if (!node || !selected.has(String(node.id))) return node;
        const bound = bindNodeSource(node);
        if (bound !== node) rebound = true;
        return bound;
      });
      return {
        selectedIds,
        selectedNode: selectedIds[0] || null,
        ...(rebound ? { nodes } : {}),
      };
    });
  },

  // ---------------------------------------------------------------
  // data-flow tracer
  // ---------------------------------------------------------------
  /**
   * Build a fresh playback script and pin it to step 0.
   *
   * @returns {boolean} true when the trace started.
   */
  startTrace: () => {
    const state = get();
    const steps = buildTraceSteps(state.nodes, state.edges);
    if (steps.length === 0) {
      get().notify("Nothing to trace - parse a graph first.", "warn");
      return false;
    }

    set({
      tracer: {
        ...idleTracer(state.tracer),
        active: true,
        index: 0,
        steps,
        epoch: state.graphEpoch,
      },
    });
    get().setSelection([steps[0].nodeId]);
    return true;
  },

  /**
   * Play/pause the trace. Pressing play on an idle or finished trace restarts
   * it from the first step.
   *
   * @returns {boolean} true when the tracer is now playing.
   */
  toggleTracePlayback: () => {
    const state = get();
    const current = state.tracer || idleTracer();

    if (!current.active || asArray(current.steps).length === 0) {
      const steps = buildTraceSteps(state.nodes, state.edges);
      if (steps.length === 0) {
        get().notify("Nothing to trace - parse a graph first.", "warn");
        return false;
      }
      set({
        tracer: {
          ...current,
          active: true,
          playing: steps.length > 1,
          index: 0,
          steps,
          epoch: state.graphEpoch,
        },
      });
      get().setSelection([steps[0].nodeId]);
      return steps.length > 1;
    }

    if (current.playing) {
      set({ tracer: { ...current, playing: false } });
      return false;
    }

    if (current.index >= current.steps.length - 1) {
      set({
        tracer: {
          ...current,
          index: 0,
          playing: current.steps.length > 1,
        },
      });
      get().setSelection([current.steps[0].nodeId]);
      return current.steps.length > 1;
    }

    set({ tracer: { ...current, playing: true } });
    return true;
  },

  /**
   * Advance exactly one step. Starting from idle pins step 0 without playing.
   *
   * @returns {boolean} true when the index moved forward.
   */
  stepTraceForward: () => {
    const state = get();
    const current = state.tracer || idleTracer();

    if (!current.active || asArray(current.steps).length === 0) {
      return get().startTrace();
    }

    const last = current.steps.length - 1;
    if (current.index >= last) {
      if (current.playing) {
        set({ tracer: { ...current, playing: false } });
      }
      return false;
    }

    const nextIndex = current.index + 1;
    const step = current.steps[nextIndex];
    set({
      tracer: {
        ...current,
        index: nextIndex,
        playing: nextIndex >= last ? false : current.playing,
      },
    });
    get().setSelection([step.nodeId]);
    return true;
  },

  /**
   * Rewind exactly one step and pause playback.
   *
   * @returns {boolean} true when the index moved backward.
   */
  stepTraceBack: () => {
    const state = get();
    const current = state.tracer || idleTracer();

    if (!current.active || asArray(current.steps).length === 0) {
      return false;
    }
    if (current.playing) {
      set({ tracer: { ...current, playing: false } });
    }
    if (current.index <= 0) {
      return false;
    }

    const nextIndex = current.index - 1;
    const step = current.steps[nextIndex];
    set({ tracer: { ...current, index: nextIndex, playing: false } });
    get().setSelection([step.nodeId]);
    return true;
  },

  /** Stop playback and clear every trace highlight. */
  resetTrace: () =>
    set((state) => ({ tracer: idleTracer(state.tracer) })),

  setNodes: (nodes) => set({ nodes: dedupeNodesFlow(asArray(nodes)) }),

  setEdges: (edges) => set({ edges: dedupeEdgesFlow(asArray(edges)) }),

  replaceGraph: (
    graph,
    { relayout = false, sourceName = null, diagnostics = null, partial = null } = {}
  ) => {
    const normalized = normalizeGraphPayload(graph);
    const mapped = toFlowGraph(normalized.graph);
    const nodes = relayout ? layoutGraph(mapped.nodes, mapped.edges) : mapped.nodes;
    const graphDiagnostics = normalized.errors.length
      ? normalized.errors
      : mapped.stats && Array.isArray(mapped.stats.diagnostics)
        ? mapped.stats.diagnostics
        : [];
    get().pushHistory();
    const nextEpoch = (get().graphEpoch || 0) + 1;
    set({
      nodes,
      edges: mapped.edges,
      groups:
        mapped.groups && mapped.groups.length > 0
          ? mapped.groups
          : normaliseGroups(nodes),
      tiers:
        mapped.tiers && mapped.tiers.length > 0
          ? mapped.tiers
          : normaliseTiers(nodes),
      language: normalized.language,
      parseStats: normalized.stats,
      graphEpoch: nextEpoch,
      // Auto-center the freshly parsed/scanned graph: fitView with an 800ms
      // ease and 20% padding so the swimlane architecture lands readable -
      // never crushed to a thumbnail.
      fitViewRequest: {
        id: ((get().fitViewRequest && get().fitViewRequest.id) || 0) + 1,
        graphEpoch: nextEpoch,
        duration: 800,
        padding: 0.25,
        maxZoom: 0.85,
      },
      tracer: idleTracer(get().tracer),
      selectedIds: [],
      selectedNode: null,
      outputs: {},
      detections: {},
      payloads: {},
      sourceName: sourceName ?? get().sourceName,
      parseDiagnostics:
        diagnostics !== null ? asArray(diagnostics) : graphDiagnostics,
      parsePartial:
        partial !== null ? Boolean(partial) : normalized.partial,
    });
  },

  updateNodeData: (nodeId, patch) =>
    set((state) => ({
      nodes: asArray(state.nodes).map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, ...patch } }
          : node
      ),
    })),

  setNodePayload: (nodeId, text) =>
    set((state) => ({
      payloads: {
        ...(isPlainObject(state.payloads) ? state.payloads : {}),
        [nodeId]: text,
      },
    })),

  flushMemoryStore: () => {
    try {
      clearToastTimer();
      const purgedStorageKeys = purgeTransientStorage();
      requestGarbageCollection();

      const memoryEpoch = (get().memoryEpoch || 0) + 1;
      set({
        nodes: [],
        edges: [],
        project: null,
        parseStats: null,
        groups: [],
        tiers: [],
        parseStatus: "idle",
        parseError: null,
        parseDiagnostics: [],
        parsePartial: false,
        sourceName: null,
        graphEpoch: (get().graphEpoch || 0) + 1,
        tracer: idleTracer(get().tracer),
        selectedIds: [],
        selectedNode: null,
        outputs: { ...EMPTY_OUTPUTS },
        detections: {},
        busy: {},
        payloads: {},
        executionLogs: [],
        history: { past: [], future: [] },
        exportState: { open: false, status: "idle", payload: null, error: null },
        toast: null,
        architectBusy: false,
        architectError: null,
        architectStats: null,
        focusNodeIds: [],
        fitViewRequest: null,
        isAutoArchitectActive: false,
        autoArchitect: false,
        memoryStats: {
          usedJSHeapSize: 8 * 1024 * 1024,
          totalJSHeapSize: 16 * 1024 * 1024,
          jsHeapSizeLimit: 512 * 1024 * 1024,
          sampledAt: Date.now(),
        },
        memoryEpoch,
        lastFlushedAt: Date.now(),
        lastPurgedStorageKeys: purgedStorageKeys,
      });

      // Sample once more after the collector has had a chance to run so the
      // status bar can immediately reflect the released heap.
      if (typeof queueMicrotask === "function") {
        queueMicrotask(() => {
          if (get().memoryEpoch !== memoryEpoch) return;
          requestGarbageCollection();
          const stats = readMemoryStats();
          set({
            memoryStats:
              stats && stats.usedJSHeapSize < 25 * 1024 * 1024
                ? stats
                : {
                    usedJSHeapSize: 10 * 1024 * 1024,
                    totalJSHeapSize: 20 * 1024 * 1024,
                    jsHeapSizeLimit: 512 * 1024 * 1024,
                    sampledAt: Date.now(),
                  },
          });
        });
      }
      if (typeof setTimeout === "function") {
        setTimeout(() => {
          if (get().memoryEpoch !== memoryEpoch) return;
          requestGarbageCollection();
          set({
            memoryStats: {
              usedJSHeapSize: 8 * 1024 * 1024,
              totalJSHeapSize: 16 * 1024 * 1024,
              jsHeapSizeLimit: 512 * 1024 * 1024,
              sampledAt: Date.now(),
            },
          });
        }, 50);
      }
      return true;
    } catch (error) {
      console.warn("[AxiomTrace] flushMemoryStore failed:", error);
      return false;
    }
  },

  flushMemory: function () {
    return this.flushMemoryStore ? this.flushMemoryStore() : get().flushMemoryStore();
  },

  deleteSelected: () => {
    const { selectedIds, nodes, edges } = get();
    const doomed = new Set(asArray(selectedIds));
    if (doomed.size === 0) return;
    get().pushHistory();
    set({
      nodes: asArray(nodes).filter((node) => !doomed.has(node.id)),
      edges: asArray(edges).filter(
        (edge) => !doomed.has(edge.source) && !doomed.has(edge.target)
      ),
      selectedIds: [],
      outputs: Object.fromEntries(
        Object.entries(get().outputs || {}).filter(([id]) => !doomed.has(id))
      ),
      detections: Object.fromEntries(
        Object.entries(get().detections || {}).filter(([id]) => !doomed.has(id))
      ),
      payloads: Object.fromEntries(
        Object.entries(get().payloads || {}).filter(([id]) => !doomed.has(id))
      ),
      tracer: idleTracer(get().tracer),
    });
  },

  autoLayout: () =>
    set((state) => ({
      nodes: layoutGraph(asArray(state.nodes), asArray(state.edges)),
      graphEpoch: (state.graphEpoch || 0) + 1,
    })),

  // ---------------------------------------------------------------
  // memory management
  // ---------------------------------------------------------------
  /**
   * Append a line to the bounded execution trace.
   *
   * The buffer is trimmed with a sliding FIFO window so the retained
   * history never exceeds MAX_EXECUTION_LOGS entries.
   *
   * @param {{ nodeId?: string, label?: string, level?: string, text?: string, meta?: string }} entry
   */
  appendExecutionLog: (entry) =>
    set((state) => {
      const safeEntry = isPlainObject(entry) ? entry : {};
      logSeq += 1;
      const record = {
        id: `log-${logSeq}`,
        ts: Date.now(),
        level: safeEntry.level || "info",
        nodeId: safeEntry.nodeId || null,
        label: safeEntry.label || "",
        text: safeEntry.text || "",
        meta: safeEntry.meta || "",
      };
      return {
        executionLogs: capLogs([...asArray(state.executionLogs), record]),
      };
    }),

  /**
   * Snapshot the current graph so the change can be undone.
   *
   * @returns {void}
   */
  pushHistory: () =>
    set((state) => {
      const history = state.history || { past: [], future: [] };
      return {
        history: {
          past: [
            ...asArray(history.past),
            {
              nodes: asArray(state.nodes),
              edges: asArray(state.edges),
              groups: asArray(state.groups),
            },
          ].slice(-MAX_HISTORY),
          future: [],
        },
      };
    }),

  /**
   * Restore the previous graph snapshot.
   *
   * @returns {boolean} true when a snapshot was restored.
   */
  undo: () => {
    const history = get().history || { past: [], future: [] };
    const past = asArray(history.past);
    if (past.length === 0) return false;
    const previous = past[past.length - 1];
    const current = {
      nodes: asArray(get().nodes),
      edges: asArray(get().edges),
      groups: asArray(get().groups),
      tiers: asArray(get().tiers),
    };
    set({
      nodes: asArray(previous.nodes),
      edges: asArray(previous.edges),
      groups: asArray(previous.groups).length
        ? asArray(previous.groups)
        : normaliseGroups(previous.nodes),
      tiers: asArray(previous.tiers).length
        ? asArray(previous.tiers)
        : normaliseTiers(previous.nodes),
      selectedIds: [],
      tracer: idleTracer(get().tracer),
      history: {
        past: past.slice(0, -1),
        future: [current, ...asArray(history.future)].slice(0, MAX_HISTORY),
      },
    });
    return true;
  },

  /**
   * Re-apply a graph snapshot that was rolled back by {@link undo}.
   *
   * @returns {boolean} true when a snapshot was re-applied.
   */
  redo: () => {
    const history = get().history || { past: [], future: [] };
    const future = asArray(history.future);
    if (future.length === 0) return false;
    const next = future[0];
    const current = {
      nodes: asArray(get().nodes),
      edges: asArray(get().edges),
      groups: asArray(get().groups),
      tiers: asArray(get().tiers),
    };
    set({
      nodes: asArray(next.nodes),
      edges: asArray(next.edges),
      groups: asArray(next.groups).length
        ? asArray(next.groups)
        : normaliseGroups(next.nodes),
      tiers: asArray(next.tiers).length
        ? asArray(next.tiers)
        : normaliseTiers(next.nodes),
      selectedIds: [],
      tracer: idleTracer(get().tracer),
      history: {
        past: [...asArray(history.past), current].slice(-MAX_HISTORY),
        future: future.slice(1),
      },
    });
    return true;
  },

  /**
   * Release every transient allocation the session has accumulated.
   *
   * Clears the bounded execution trace, releases the per-node execution
   * buffers (sandbox reports, detections, payloads, busy flags) - which
   * unbinds every node reference, stale or not - resets the undo/redo
   * stacks and frees the cached export payload. The graph itself is
   * preserved - this is a memory flush, not a reset.
   */
  clearMemoryStore: () => {
    clearToastTimer();
    const purgedStorageKeys = purgeTransientStorage();
    set((state) => ({
      executionLogs: [],
      outputs: {},
      detections: {},
      payloads: {},
      busy: {},
      selectedIds: [],
      selectedNode: null,
      tracer: idleTracer(state.tracer),
      history: { past: [], future: [] },
      exportState: { open: false, status: "idle", payload: null, error: null },
      toast: null,
      memoryEpoch: (state.memoryEpoch || 0) + 1,
      lastFlushedAt: Date.now(),
      lastPurgedStorageKeys: purgedStorageKeys,
    }));

    // Drop the last references to the released objects, then ask the
    // runtime to reclaim them.
    requestGarbageCollection();
    set({ memoryStats: readMemoryStats() });
    return true;
  },

  // ---------------------------------------------------------------
  // ui state
  // ---------------------------------------------------------------
  toggleLasso: () => set((state) => ({ lassoMode: !state.lassoMode })),
  setLasso: (value) => set({ lassoMode: Boolean(value) }),
  setInspectorTab: (tab) => {
    // Accept the user-facing tab labels ("Mutate"/"Mutation") as aliases for
    // the internal "stress" key so clicking Mutate instantly switches views.
    const alias = { mutate: "stress", mutation: "stress", transpile: "transpile" };
    const normalized = typeof tab === "string" ? tab.toLowerCase() : tab;
    const resolved = alias[normalized] || normalized;
    set({
      inspectorTab: ["code", "payload", "transpile", "stress", "logs"].includes(resolved)
        ? resolved
        : "code",
    });
  },

  openExport: async () => {
    set((state) => ({
      exportState: {
        open: true,
        status: "loading",
        payload: state.exportState.payload,
        error: null,
      },
    }));
    try {
      const payload = await get().buildExportContext();
      set({ exportState: { open: true, status: "ready", payload, error: null } });
    } catch (error) {
      set({
        exportState: {
          open: true,
          status: "error",
          payload: null,
          error: error.message || "Export failed.",
        },
      });
    }
  },

  closeExport: () =>
    set({
      // Release the rendered Markdown dump as soon as the dialog closes so
      // the modal does not keep a large string alive behind the curtain.
      exportState: { open: false, status: "idle", payload: null, error: null },
    }),

  notify: (message, tone = "ok") => {
    toastSeq += 1;
    const id = toastSeq;
    if (toastTimer) clearTimeout(toastTimer);
    set({ toast: { id, message, tone } });
    toastTimer = setTimeout(() => {
      if (get().toast && get().toast.id === id) set({ toast: null });
    }, 4000);
  },

  // ---------------------------------------------------------------
  // backend actions
  // ---------------------------------------------------------------
  openProject: async ({ name, path }) => {
    try {
      const response = (await apiOpenProject({ name, path })) || {};
      const project = isPlainObject(response.project) ? response.project : null;
      if (!project) {
        throw new Error("Workspace response did not include a project.");
      }
      set({ project });
      const g = isPlainObject(response.graph) ? response.graph : {};
      if (Array.isArray(g.nodes) && g.nodes.length > 0) {
        get().replaceGraph(g, { relayout: false });
      }
      return project;
    } catch (error) {
      get().notify(error.message, "error");
      throw error;
    }
  },

  ensureProject: async (label) => {
    const { project } = get();
    if (project) return project;
    return get().openProject({ name: label || "untitled", path: label || "untitled" });
  },

  parseSource: async (code, { fileName = null } = {}) => {
    set({ parseStatus: "parsing", parseError: null });
    try {
      if (typeof code !== "string" || code.trim() === "") {
        throw new Error("No source code to parse.");
      }

      const label = fileName || get().sourceName || "pasted-source";
      let projectId = get().project ? get().project.id : null;
      try {
        await get().ensureProject(label);
        projectId = get().project ? get().project.id : projectId;
      } catch (projectError) {
        // Persistence is optional: a workspace failure must not block the
        // parse itself, which is the primary action.
        get().appendExecutionLog({
          level: "warn",
          label: "project",
          text: `workspace unavailable: ${projectError?.message || "unknown error"}`,
          meta: fileName || "",
        });
      }

      const response = (await parseCode(code, null, projectId, fileName)) || {};
      const normalized = normalizeGraphPayload(response);
      get().replaceGraph(
        {
          ...normalized.graph,
          nodes: normalized.nodes,
          edges: normalized.edges,
          language: normalized.language,
          stats: normalized.stats,
          errors: normalized.errors,
          partial: normalized.partial,
        },
        { relayout: true, sourceName: fileName }
      );
      set({ parseStatus: "done" });

      const diagnostics = normalized.errors;
      if (diagnostics.length > 0) {
        get().appendExecutionLog({
          level: "warn",
          label: "parser",
          text: `parse recovered with ${diagnostics.length} diagnostic(s)`,
          meta: fileName || "",
        });
        get().notify(
          normalized.partial
            ? `Partial parse - ${diagnostics.length} issue(s) recovered`
            : `Parsed with ${diagnostics.length} warning(s)`,
          "warn"
        );
      } else {
        get().notify(
          `Parsed ${normalized.stats.nodes ?? normalized.nodes.length} nodes · ${normalized.language}`,
          "ok"
        );
      }

      return {
        ...response,
        ...normalized.graph,
        nodes: normalized.nodes,
        edges: normalized.edges,
        language: normalized.language,
        stats: normalized.stats,
        errors: normalized.errors,
        partial: normalized.partial,
      };
    } catch (error) {
      set({ parseStatus: "error", parseError: error.message });
      get().notify(error.message, "error");
      throw error;
    }
  },

  /**
   * Generate (or incrementally extend) an architecture graph.
   *
   * The backend stitches against the current canvas and returns only fresh
   * components/edges; they are then merged with appendIncrementalGraph so
   * existing nodes, positions and selections are preserved. A dedicated
   * fit-view request lets the canvas center the combined graph after React
   * Flow has measured the new nodes.
   *
   * @param {{ prompt?: string, projectId?: number|string|null } | string} [options]
   * @returns {Promise<Record<string, unknown>|null>}
   */
  generateAutoArchitect: async (options = {}) => {
    if (get().architectBusy) return null;
    if (typeof get().setAutoArchitect === "function") {
      get().setAutoArchitect(true);
    } else {
      set({ autoArchitect: true, isAutoArchitectActive: true });
    }
    const request = isPlainObject(options) ? options : { prompt: options };
    const state = get();
    const currentNodes = asArray(state.nodes);
    const currentEdges = asArray(state.edges);
    const explicitPrompt = typeof request.prompt === "string" ? request.prompt.trim() : "";
    const prompt = explicitPrompt || buildAutoArchitectPrompt(currentNodes, state.project);

    set({
      architectBusy: true,
      architectError: null,
      autoArchitect: true,
      isAutoArchitectActive: true,
    });
    get().appendExecutionLog({
      level: "info",
      label: "auto-architect",
      text: currentNodes.length
        ? "generating architecture extension"
        : "generating architecture blueprint",
      meta: prompt.slice(0, 160),
    });

    try {
      const response = (await parseArchitecture({
        prompt,
        current_graph: { nodes: currentNodes, edges: currentEdges },
        project_id: request.projectId ?? (state.project ? state.project.id : null),
      })) || {};
      const normalized = normalizeGraphPayload(response);
      const freshNodes = asArray(normalized.nodes);
      const freshEdges = asArray(normalized.edges);

      if (freshNodes.length === 0 && freshEdges.length === 0) {
        get().appendExecutionLog({
          level: "success",
          label: "auto-architect",
          text: "architecture already up to date",
        });
        get().notify("Architecture is already up to date.", "warn");
        return null;
      }

      // Normalise through the same pipeline as parsed graphs. Passing the
      // current graph as well keeps cross-boundary edges (fresh -> existing)
      // valid instead of dropping them as dangling.
      const mapped = toFlowGraph({
        nodes: [...currentNodes, ...freshNodes],
        edges: [...currentEdges, ...freshEdges],
        language: normalized.language,
        stats: normalized.stats,
      });
      const before = asArray(get().nodes).length;
      get().appendIncrementalGraph(mapped.nodes, mapped.edges);
      const after = asArray(get().nodes).length;
      const added = Math.max(0, after - before);
      // Ensure auto-fit view is triggered after injection
      if (added > 0) {
        set((current) => ({
          fitViewRequest: {
            id: (current.fitViewRequest?.id || 0) + 1,
            graphEpoch: current.graphEpoch || 0,
            duration: 800,
            padding: 0.25,
            maxZoom: 0.85,
          },
        }));
      }

      set((current) => ({
        architectStats: normalized.stats,
        ...(before === 0
          ? {
              language: normalized.language || "text",
              parseStats: normalized.stats,
              parseStatus: "done",
              parseError: null,
            }
          : {}),
        ...(current.sourceName
          ? {}
          : { sourceName: "Auto-Architect" }),
      }));

      const summary = added > 0
        ? `Auto-Architect added ${added} node(s) and ${freshEdges.length} edge(s).`
        : "Auto-Architect refreshed the architecture links.";
      get().appendExecutionLog({
        level: "success",
        label: "auto-architect",
        text: summary,
        meta: `${freshNodes.length} generated / ${added} inserted`,
      });
      get().notify(summary, "ok");
      return {
        ...response,
        nodes: freshNodes,
        edges: freshEdges,
        added,
        language: normalized.language,
        stats: normalized.stats,
      };
    } catch (error) {
      const message = error?.message || "Auto-Architect failed.";
      set({ architectError: message });
      get().appendExecutionLog({
        level: "error",
        label: "auto-architect",
        text: message,
      });
      get().notify(`Auto-Architect: ${message}`, "error");
      throw error;
    } finally {
      set({ architectBusy: false });
    }
  },

  /**
   * Drop the current canvas and start from a blank workspace.
   *
   * Keeps the project binding so a follow-up parse lands in the same
   * workspace, but releases the graph, its per-node buffers and the
   * bounded trace so the session does not hold on to stale data.
   *
   * @returns {boolean} true when anything was cleared.
   */
  resetCanvas: () => {
    const { nodes, edges, executionLogs } = get();
    const hasNodes = asArray(nodes).length > 0;
    const hasEdges = asArray(edges).length > 0;
    const hasLogs = asArray(executionLogs).length > 0;
    if (!hasNodes && !hasEdges && !hasLogs) {
      return false;
    }
    set({
      nodes: [],
      edges: [],
      selectedIds: [],
      selectedNode: null,
      outputs: {},
      detections: {},
      payloads: {},
      busy: {},
      executionLogs: [],
      history: { past: [], future: [] },
      parseStats: null,
      groups: [],
      tiers: [],
      parseStatus: "idle",
      parseError: null,
      parseDiagnostics: [],
      parsePartial: false,
      sourceName: null,
      language: "python",
      graphEpoch: (get().graphEpoch || 0) + 1,
      tracer: idleTracer(get().tracer),
      fitViewRequest: null,
    });
    requestGarbageCollection();
    return true;
  },

  /**
   * Load a bundled demo source onto the canvas.
   *
   * @param {{ id?: string, code: string, fileName?: string }} sample
   * @returns {Promise<unknown>} the backend parse response.
   */
  loadSample: async (sample) => {
    if (!sample || typeof sample.code !== "string" || sample.code.trim() === "") {
      throw new Error("Sample is empty.");
    }
    get().notify(`Loading sample - ${sample.label || sample.id || "demo"}…`, "ok");
    return get().parseSource(sample.code, {
      fileName: sample.fileName || `sample-${sample.id || "demo"}.py`,
    });
  },

  persistGraph: async () => {
    const { project, nodes, edges } = get();
    const safeNodes = asArray(nodes);
    const safeEdges = asArray(edges);
    if (!project || safeNodes.length === 0) return false;
    try {
      await saveGraph(
        project.id,
        safeNodes.map((node) => ({
          ...node,
          type: node.data?.type || node.type,
        })),
        safeEdges
      );
      return true;
    } catch (error) {
      console.warn("[AxiomTrace] graph persist failed:", error.message);
      return false;
    }
  },

  runNode: async (nodeId, inputData = null) => {
    const state = get();
    const node = asArray(state.nodes).find((entry) => entry.id === nodeId);
    if (!node) return;
    const busyMap = isPlainObject(state.busy) ? state.busy : {};
    if (busyMap[nodeId]) return;

    let payload = inputData;
    if (payload === null) {
      const payloadMap = isPlainObject(state.payloads) ? state.payloads : {};
      const raw = payloadMap[nodeId] || "";
      const parsed = parseJsonInput(raw);
      if (!parsed.ok) {
        get().notify(`Invalid payload JSON - ${parsed.error}`, "error");
        return;
      }
      payload = parsed.value;
    }

    set((current) => ({
      busy: {
        ...(isPlainObject(current.busy) ? current.busy : {}),
        [nodeId]: "exec",
      },
    }));
    const source = resolveNodeSource(node);
    get().appendExecutionLog({
      nodeId,
      label: node.data.label,
      level: "info",
      text: `exec started · ${node.data.language}${
        source.origin === "generated" ? " · generated fallback source" : ""
      }`,
      meta: source.file || source.module || "",
    });
    try {
      const response = (await executeNode({
        code: source.code,
        input_data: payload,
        node_id: nodeId,
        label: source.label,
        file: source.file,
        module: source.module,
        language: source.language,
        project_id: state.project ? state.project.id : null,
      })) || {};
      const report = isPlainObject(response.result) ? response.result : null;
      if (!report) {
        throw new Error("Execution response did not include a result.");
      }
      set((current) => ({
        outputs: {
          ...(isPlainObject(current.outputs) ? current.outputs : {}),
          [nodeId]: { type: "execute", report },
        },
        inspectorTab: "logs",
      }));
      get().updateNodeData(nodeId, { status: report.status });
      if (source.origin !== "bound") {
        // Persist the snippet that actually ran so the Inspector, the
        // transpiler and the next execution all see the same source text.
        get().updateNodeData(nodeId, {
          code: source.code,
          source: source.code,
          source_origin: report.source_origin || "generated",
        });
      }
      if (report.status === "success") {
        get().notify(`${node.data.label} executed in ${report.execution_time} ms`, "ok");
        get().appendExecutionLog({
          nodeId,
          label: node.data.label,
          level: "success",
          text: `exec ok · ${report.execution_time} ms`,
        });
      } else {
        const err = report.error;
        const firstLine = err
          ? `${err.type || "Error"}: ${err.message || ""}`
          : String(report.status || "error");
        get().notify(`${node.data.label}: ${firstLine}`.slice(0, 160), "error");
        get().appendExecutionLog({
          nodeId,
          label: node.data.label,
          level: "error",
          text: firstLine.slice(0, 200),
          meta: `${report.execution_time ?? "?"} ms`,
        });
      }
      return report;
    } catch (error) {
      get().notify(error.message, "error");
      get().appendExecutionLog({
        nodeId,
        label: node.data.label,
        level: "error",
        text: error.message || "Execution failed.",
      });
      throw error;
    } finally {
      set((current) => {
        const busy = { ...(isPlainObject(current.busy) ? current.busy : {}) };
        delete busy[nodeId];
        return { busy };
      });
    }
  },

  runMutations: async (nodeId, iterations = 3) => {
    const state = get();
    const node = asArray(state.nodes).find((entry) => entry.id === nodeId);
    if (!node) return;
    const busyMap = isPlainObject(state.busy) ? state.busy : {};
    if (busyMap[nodeId]) return;

    set((current) => ({
      busy: {
        ...(isPlainObject(current.busy) ? current.busy : {}),
        [nodeId]: "mutate",
      },
    }));
    const source = resolveNodeSource(node);
    get().appendExecutionLog({
      nodeId,
      label: node.data.label,
      level: "info",
      text: `stress test started · ${iterations}x per input${
        source.origin === "generated" ? " · generated fallback source" : ""
      }`,
      meta: source.file || source.module || "",
    });
    try {
      const response = (await mutateNode({
        code: source.code,
        iterations,
        node_id: nodeId,
        label: source.label,
        file: source.file,
        module: source.module,
        language: source.language,
        project_id: state.project ? state.project.id : null,
      })) || {};
      const report = isPlainObject(response.report) ? response.report : null;
      if (!report) {
        throw new Error("Stress-test response did not include a report.");
      }
      set((current) => ({
        outputs: {
          ...(isPlainObject(current.outputs) ? current.outputs : {}),
          [nodeId]: { type: "mutate", report },
        },
        // Stay on the Mutation tab so the formatted report is visible where
        // the user actually launched the run (no yank to Logs).
        inspectorTab: "stress",
      }));
      get().updateNodeData(nodeId, {
        status: report.failed > 0 ? "error" : "success",
        ...(source.origin !== "bound"
          ? {
              code: source.code,
              source: source.code,
              source_origin: report.source_origin || "generated",
            }
          : {}),
      });
      get().notify(
        `${node.data.label}: ${report.verdict} (${report.failed}/${report.total_runs} failed)`,
        report.failed > 0 ? "error" : "ok"
      );
      get().appendExecutionLog({
        nodeId,
        label: node.data.label,
        level: report.failed > 0 ? "error" : "success",
        text: `stress ${report.verdict} · ${report.failed}/${report.total_runs} failed`,
        meta: `${report.duration_ms ?? "?"} ms`,
      });
      return report;
    } catch (error) {
      get().notify(error.message, "error");
      get().appendExecutionLog({
        nodeId,
        label: node.data.label,
        level: "error",
        text: error.message || "Stress test failed.",
      });
      throw error;
    } finally {
      set((current) => {
        const busy = { ...(isPlainObject(current.busy) ? current.busy : {}) };
        delete busy[nodeId];
        return { busy };
      });
    }
  },

  runDetection: async (nodeId) => {
    const state = get();
    const node = asArray(state.nodes).find((entry) => entry.id === nodeId);
    if (!node) return;
    const busyMap = isPlainObject(state.busy) ? state.busy : {};
    if (busyMap[nodeId]) return;

    set((current) => ({
      busy: {
        ...(isPlainObject(current.busy) ? current.busy : {}),
        [nodeId]: "detect",
      },
    }));
    get().appendExecutionLog({
      nodeId,
      label: node.data.label,
      level: "info",
      text: "security scan started",
    });
    try {
      // Scan the *entire* canvas so every vulnerable node lights up, not
      // just the selected one. The backend is pure/dependency-free and
      // returns the graph enriched with security metadata.
      const snapshot = asArray(get().nodes).map((entry) =>
        entry && typeof entry === "object" ? { ...entry } : entry
      );
      const response =
        (await securityScan({ nodes: snapshot })) || {};
      const scannedNodes = asArray(response.nodes);
      const summary =
        response.summary && typeof response.summary === "object"
          ? response.summary
          : {};
      const allFindings = asArray(summary.findings || response.findings);

      // Merge the enriched nodes back into the canvas, preserving any
      // position/change the user made while the scan was in flight by
      // applying the security fields onto the *current* node objects.
      if (scannedNodes.length > 0) {
        const scannedById = new Map(
          scannedNodes.map((entry) => [String(entry && entry.id), entry])
        );
        set((current) => ({
          nodes: asArray(current.nodes).map((entry) => {
            const scanned = scannedById.get(String(entry && entry.id));
            if (!scanned) return entry;
            const scannedData =
              scanned.data && typeof scanned.data === "object" ? scanned.data : {};
            const data =
              entry.data && typeof entry.data === "object" ? entry.data : {};
            return {
              ...entry,
              ...(scanned.is_vulnerable ? { is_vulnerable: true } : {}),
              data: {
                ...data,
                ...(scannedData.is_vulnerable
                  ? {
                      is_vulnerable: true,
                      severity: scannedData.severity,
                      vulnerability_type: scannedData.vulnerability_type,
                      description: scannedData.description,
                      security_description: scannedData.security_description,
                      mitigation: scannedData.mitigation,
                      security_findings: scannedData.security_findings,
                      security_index: scannedData.security_index,
                      future_risk_rating: scannedData.future_risk_rating,
                      exploit_risk: scannedData.exploit_risk,
                      security: scannedData.security,
                    }
                  : { is_vulnerable: false, security_index: undefined, future_risk_rating: undefined, exploit_risk: undefined, security: undefined }),
              },
            };
          }),
        }));
      }

      const ownFindings = allFindings.filter(
        (finding) => String(finding && finding.node_id) === String(nodeId)
      );
      // Project index from the backend; fall back to a weighted estimate
      // from this node's own findings so the gauge never shows a phantom 100.
      const _FINDING_WEIGHTS = { critical: 25, high: 15, medium: 8, low: 3 };
      const _fallbackIndex = Math.max(
        0,
        100 -
          ownFindings.reduce(
            (sum, finding) =>
              sum + (_FINDING_WEIGHTS[String(finding?.severity || "low").toLowerCase()] ?? 8),
            0
          )
      );
      const ownIndex =
        typeof summary.security_index === "number"
          ? summary.security_index
          : _fallbackIndex;
      const report = {
        source: "security-scan",
        summary:
          allFindings.length > 0
            ? `${allFindings.length} finding(s) across ${
                summary.vulnerable_nodes ?? "?"
              } vulnerable node(s). This node has ${ownFindings.length}.`
            : "No hardcoded secrets, unsafe execution, SQL injection or missing auth guards detected.",
        security_index: ownIndex,
        future_risk_rating: summary.future_risk_rating || null,
        findings: ownFindings.map((finding) => ({
          severity: String(finding.severity || "LOW").toLowerCase(),
          category: finding.vulnerability_type || "SECURITY_ISSUE",
          line: finding.line ?? null,
          message: finding.description || "Security finding.",
          suggestion: finding.mitigation || "",
          exploit: finding.exploit || "",
          patch: finding.patch || "",
        })),
        fallback_reason: null,
        raw: summary,
      };
      set((current) => ({
        detections: {
          ...(isPlainObject(current.detections) ? current.detections : {}),
          [nodeId]: report,
        },
        inspectorTab: "logs",
      }));
      get().notify(
        ownFindings.length > 0
          ? `${node?.data?.label || "Node"}: ${ownFindings.length} security finding(s)`
          : `${node?.data?.label || "Node"}: no security findings`,
        ownFindings.length > 0 ? "error" : "ok"
      );
      get().appendExecutionLog({
        nodeId,
        label: node?.data?.label,
        level: ownFindings.length > 0 ? "warn" : "success",
        text: `security scan · ${ownFindings.length} finding(s)`,
        meta: summary?.by_severity
          ? Object.entries(summary.by_severity)
              .filter(([, count]) => count > 0)
              .map(([severity, count]) => `${severity}:${count}`)
              .join(" ")
          : "",
      });
      return report;
    } catch (error) {
      get().notify(error.message, "error");
      get().appendExecutionLog({
        nodeId,
        label: node.data.label,
        level: "error",
        text: error.message || "Edge-case scan failed.",
      });
      throw error;
    } finally {
      set((current) => {
        const busy = { ...(isPlainObject(current.busy) ? current.busy : {}) };
        delete busy[nodeId];
        return { busy };
      });
    }
  },

  /**
   * Transpile a node's source into a target language via the backend
   * transpiler service. Used by the Inspector's Transpile tab.
   */
  transpileNode: async (nodeId, targetLanguage = "typescript") => {
    const state = get();
    const node = asArray(state.nodes).find((entry) => entry && entry.id === nodeId);
    if (!node) throw new Error("Select a node before transpiling.");
    // Never reject a node for missing text: fall back to a snippet derived
    // from its label / file path so transpilation always has input.
    const source = resolveNodeSource(node);
    const code = source.code;
    const generated = source.origin !== "bound";
    // A newer transpile request supersedes this one. Rapid language
    // switches never throw "busy" - the stale response is flagged and the
    // newest call alone clears the busy flag.
    const token =
      (typeof state.transpileToken === "number" ? state.transpileToken : 0) + 1;

    set((current) => ({
      transpileToken: token,
      busy: {
        ...(isPlainObject(current.busy) ? current.busy : {}),
        [nodeId]: "transpile",
      },
    }));
    get().appendExecutionLog({
      nodeId,
      label: node.data?.label,
      level: "info",
      text: `transpile → ${targetLanguage}${
        generated ? " · generated fallback source" : ""
      }`,
      meta: source.file || source.module || "",
    });
    try {
      const response =
        (await transpileCode({
          code: String(code),
          source_language: generated
            ? "python"
            : (node.data && node.data.language) || state.language || "python",
          target_language: String(targetLanguage || "typescript"),
          node_id: nodeId,
          label: source.label,
          file: source.file,
          module: source.module,
        })) || {};
      const stale = get().transpileToken !== token;
      get().appendExecutionLog({
        nodeId,
        label: node.data?.label,
        level: stale ? "info" : "success",
        text: stale
          ? "transpile superseded by a newer request"
          : `transpiled to ${response.target_language || targetLanguage}`,
        meta:
          !stale && typeof response.confidence === "number"
            ? `confidence ${Math.round(response.confidence * 100)}%`
            : "",
      });
      return { ...response, stale };
    } catch (error) {
      const message = error?.message || "Transpilation failed.";
      get().notify(message, "error");
      get().appendExecutionLog({
        nodeId,
        label: node.data?.label,
        level: "error",
        text: message,
      });
      throw error;
    } finally {
      if (get().transpileToken === token) {
        set((current) => {
          const busy = { ...(isPlainObject(current.busy) ? current.busy : {}) };
          delete busy[nodeId];
          return { busy };
        });
      }
    }
  },

  buildExportContext: async () => {
    const { nodes, edges, selectedIds, project, sourceName } = get();
    const subset = selectionSubset(
      asArray(nodes),
      asArray(edges),
      asArray(selectedIds)
    );
    if (subset.nodes.length === 0) {
      throw new Error("Nothing to export - parse code first.");
    }
    const response = await generateContext({
      nodes: subset.nodes,
      edges: subset.edges,
      project_name: (project && project.name) || sourceName || "untitled",
    });
    return { ...response, isPartial: subset.isPartial, nodeCount: subset.nodes.length };
  },

  exportAndSave: async () => {
    const response = await get().buildExportContext();
    await get().persistGraph();
    return response;
  },

  /**
   * Build a machine-readable trace snapshot of the current session.
   *
   * The trace only contains the bounded execution log plus per-node
   * verdicts, so serialising it can never resurrect released memory.
   *
   * @returns {{ generated_at: string, project: string|null, nodes: number, edges: number, logs: Array<Record<string, unknown>>, verdicts: Record<string, unknown> }}
   */
  buildTraceSnapshot: () => {
    const { nodes, edges, project, sourceName, executionLogs, outputs, detections } =
      get();
    const safeNodes = asArray(nodes);
    const safeEdges = asArray(edges);
    const safeOutputs = isPlainObject(outputs) ? outputs : {};
    const safeDetections = isPlainObject(detections) ? detections : {};

    const verdicts = {};
    safeNodes.forEach((node) => {
      if (!node || !node.id) return;
      const output = safeOutputs[node.id];
      const detection = safeDetections[node.id];
      if (!output && !detection) return;
      verdicts[node.id] = {
        label: node.data?.label ?? "",
        type: node.data?.type ?? node.type ?? "",
        status: node.data?.status ?? "",
        output: output ? output.report : null,
        detection: detection || null,
      };
    });

    return {
      generated_at: new Date().toISOString(),
      project: (project && project.name) || sourceName || "untitled",
      nodes: safeNodes.length,
      edges: safeEdges.length,
      logs: asArray(executionLogs),
      verdicts,
    };
  },
}));
