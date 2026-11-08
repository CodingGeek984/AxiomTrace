import assert from "node:assert/strict";
import test from "node:test";

import { traceStepAt } from "../src/lib/tracer.js";
import { useGraphStore } from "../src/store/useGraphStore.js";

const NODES = [
  {
    id: "entry",
    data: {
      label: "Entrypoint",
      type: "entrypoint",
      is_entry: true,
      parameters: ["request_id"],
    },
  },
  {
    id: "service",
    data: {
      label: "Service Logic",
      type: "service",
      parameters: ["user_id"],
    },
  },
  {
    id: "db",
    data: {
      label: "Database",
      type: "database",
      parameters: ["query"],
    },
  },
];

const EDGES = [
  { id: "e-entry-service", source: "entry", target: "service" },
  { id: "e-service-db", source: "service", target: "db" },
];

function resetStore() {
  useGraphStore.setState({
    nodes: NODES.map((node) => ({ ...node, data: { ...node.data } })),
    edges: EDGES.map((edge) => ({ ...edge })),
    selectedIds: [],
    selectedNode: null,
    graphEpoch: 7,
    history: { past: [], future: [] },
    executionLogs: [],
    tracer: {
      active: false,
      playing: false,
      index: -1,
      steps: [],
      epoch: null,
      speed: 900,
    },
  });
}

test("Step Forward starts a trace at step 0 and selects the active node", () => {
  resetStore();

  const moved = useGraphStore.getState().stepTraceForward();
  const state = useGraphStore.getState();

  assert.equal(moved, true);
  assert.equal(state.tracer.active, true);
  assert.equal(state.tracer.playing, false);
  assert.equal(state.tracer.index, 0);
  assert.deepEqual(state.selectedIds, ["entry"]);
  assert.equal(traceStepAt(state.tracer).nodeId, "entry");
});

test("Step Forward advances the active node and Inspector payload", () => {
  resetStore();

  useGraphStore.getState().stepTraceForward();
  const moved = useGraphStore.getState().stepTraceForward();
  const state = useGraphStore.getState();
  const step = traceStepAt(state.tracer);

  assert.equal(moved, true);
  assert.equal(state.tracer.index, 1);
  assert.deepEqual(state.selectedIds, ["service"]);
  assert.equal(step.nodeId, "service");
  assert.equal(step.payload.step, 2);
  assert.equal(step.payload.total_steps, 3);
  assert.equal(step.payload.input.entrypoint.accepted, true);
});

test("Step Back rewinds one step and pauses playback", () => {
  resetStore();

  useGraphStore.getState().stepTraceForward();
  useGraphStore.getState().stepTraceForward();
  useGraphStore.setState((state) => ({
    tracer: { ...state.tracer, playing: true },
  }));

  const moved = useGraphStore.getState().stepTraceBack();
  const state = useGraphStore.getState();

  assert.equal(moved, true);
  assert.equal(state.tracer.index, 0);
  assert.equal(state.tracer.playing, false);
  assert.deepEqual(state.selectedIds, ["entry"]);
});

test("Play/Pause toggles without rebuilding the trace script", () => {
  resetStore();

  const playing = useGraphStore.getState().toggleTracePlayback();
  const started = useGraphStore.getState();
  assert.equal(playing, true);
  assert.equal(started.tracer.playing, true);
  assert.equal(started.tracer.index, 0);
  assert.equal(started.tracer.steps.length, 3);

  const paused = useGraphStore.getState().toggleTracePlayback();
  const state = useGraphStore.getState();
  assert.equal(paused, false);
  assert.equal(state.tracer.playing, false);
  assert.equal(state.tracer.index, 0);
  assert.equal(state.tracer.steps, started.tracer.steps);
});

test("Play at the end of a trace restarts from step 0", () => {
  resetStore();

  useGraphStore.getState().startTrace();
  useGraphStore.getState().stepTraceForward();
  useGraphStore.getState().stepTraceForward();

  const ended = useGraphStore.getState().tracer;
  assert.equal(ended.index, 2);
  assert.equal(ended.playing, false);

  const replayed = useGraphStore.getState().toggleTracePlayback();
  const state = useGraphStore.getState();
  assert.equal(replayed, true);
  assert.equal(state.tracer.index, 0);
  assert.equal(state.tracer.playing, true);
  assert.deepEqual(state.selectedIds, ["entry"]);
});

test("Reset clears playback state but keeps the graph", () => {
  resetStore();

  useGraphStore.getState().startTrace();
  useGraphStore.getState().stepTraceForward();
  useGraphStore.getState().resetTrace();
  const state = useGraphStore.getState();

  assert.equal(state.tracer.active, false);
  assert.equal(state.tracer.playing, false);
  assert.equal(state.tracer.index, -1);
  assert.deepEqual(state.tracer.steps, []);
  assert.equal(state.nodes.length, 3);
  assert.equal(state.edges.length, 2);
});

test("Connecting a new edge invalidates the cached trace script", () => {
  resetStore();

  useGraphStore.getState().startTrace();
  assert.equal(useGraphStore.getState().tracer.active, true);

  useGraphStore.getState().onConnect({ source: "entry", target: "db" });
  const state = useGraphStore.getState();

  assert.equal(state.tracer.active, false);
  assert.equal(state.tracer.steps.length, 0);
  assert.equal(state.edges.length, 3);
});

test("Removing an edge invalidates the cached trace script", () => {
  resetStore();

  useGraphStore.getState().startTrace();
  useGraphStore.getState().onEdgesChange([{ id: "e-service-db", type: "remove" }]);
  const state = useGraphStore.getState();

  assert.equal(state.tracer.active, false);
  assert.equal(state.edges.length, 1);
});

test("Removing a node invalidates the cached trace script", () => {
  resetStore();

  useGraphStore.getState().startTrace();
  useGraphStore.getState().onNodesChange([{ id: "db", type: "remove" }]);
  const state = useGraphStore.getState();

  assert.equal(state.tracer.active, false);
  assert.equal(state.nodes.length, 2);
});

test("Dragging a node does not interrupt an active trace", () => {
  resetStore();

  useGraphStore.getState().startTrace();
  useGraphStore.getState().onNodesChange([
    {
      id: "entry",
      type: "position",
      position: { x: 12, y: 34 },
      dragging: false,
    },
  ]);
  const state = useGraphStore.getState();

  assert.equal(state.tracer.active, true);
  assert.equal(state.tracer.index, 0);
  assert.deepEqual(state.nodes[0].position, { x: 12, y: 34 });
});
