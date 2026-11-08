import assert from "node:assert/strict";
import test from "node:test";

import {
  buildTraceOrder,
  buildTraceSteps,
  traceStepAt,
} from "../src/lib/tracer.js";

const NODES = [
  {
    id: "db",
    data: {
      label: "Database",
      type: "database",
      parameters: ["query"],
    },
  },
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
];

const EDGES = [
  { id: "e-entry-service", source: "entry", target: "service" },
  { id: "e-service-db", source: "service", target: "db" },
];

test("buildTraceOrder returns a stable topological sequence", () => {
  const order = buildTraceOrder(NODES, EDGES);
  assert.deepEqual(
    order.map((node) => node.id),
    ["entry", "service", "db"]
  );
});

test("buildTraceOrder keeps cyclic nodes instead of dropping them", () => {
  const nodes = [
    { id: "a", data: { label: "A", type: "service" } },
    { id: "b", data: { label: "B", type: "service" } },
  ];
  const edges = [
    { id: "ab", source: "a", target: "b" },
    { id: "ba", source: "b", target: "a" },
  ];

  const order = buildTraceOrder(nodes, edges);
  assert.equal(order.length, 2);
  assert.deepEqual(
    order.map((node) => node.id).sort(),
    ["a", "b"]
  );
});

test("buildTraceSteps propagates simulated output into downstream input", () => {
  const steps = buildTraceSteps(NODES, EDGES);
  assert.equal(steps.length, 3);

  const [entry, service, database] = steps;
  assert.equal(entry.nodeId, "entry");
  assert.equal(service.nodeId, "service");
  assert.equal(database.nodeId, "db");

  assert.deepEqual(entry.payload.input, { request_id: 1 });
  assert.equal(entry.payload.output.accepted, true);

  assert.deepEqual(service.payload.input.entrypoint, entry.payload.output);
  assert.equal(service.payload.input.user_id, 2);

  assert.deepEqual(database.payload.input.service_logic, service.payload.output);
  assert.equal(database.payload.input.query, "<query>");
  assert.equal(database.payload.output.row_count, 0);
});

test("each step exposes the edge ids touching its node", () => {
  const steps = buildTraceSteps(NODES, EDGES);
  assert.deepEqual(steps[0].edgeIds, ["e-entry-service"]);
  assert.deepEqual(steps[1].edgeIds, ["e-entry-service", "e-service-db"]);
  assert.deepEqual(steps[2].edgeIds, ["e-service-db"]);
});

test("buildTraceSteps ignores dangling/self edges and merges parallel parents once", () => {
  const edges = [
    { id: "e-entry-service", source: "entry", target: "service" },
    { id: "e-entry-service-2", source: "entry", target: "service" },
    { id: "e-missing", source: "entry", target: "ghost" },
    { id: "e-self", source: "entry", target: "entry" },
  ];

  const steps = buildTraceSteps(NODES, edges);
  const entryStep = steps.find((step) => step.nodeId === "entry");
  const serviceStep = steps.find((step) => step.nodeId === "service");

  assert.deepEqual(entryStep.edgeIds, ["e-entry-service", "e-entry-service-2"]);
  assert.deepEqual(serviceStep.edgeIds, ["e-entry-service", "e-entry-service-2"]);
  assert.deepEqual(serviceStep.payload.input.entrypoint, entryStep.payload.output);
  assert.equal(serviceStep.payload.input.entrypoint_1, undefined);
  assert.equal(
    Object.keys(serviceStep.payload.input).filter((key) =>
      key.startsWith("entrypoint")
    ).length,
    1
  );
});

test("buildTraceSteps falls back to a generated edge id", () => {
  const steps = buildTraceSteps(NODES, [
    { source: "entry", target: "service" },
  ]);
  assert.deepEqual(steps[0].edgeIds, ["entry->service"]);
});

test("traceStepAt returns the active step and null when idle or out of range", () => {
  const steps = buildTraceSteps(NODES, EDGES);

  assert.equal(traceStepAt({ active: false, index: 0, steps }), null);
  assert.equal(traceStepAt({ active: true, index: -1, steps }), null);
  assert.equal(traceStepAt({ active: true, index: 99, steps }), null);
  assert.equal(traceStepAt({ active: true, index: 1, steps }), steps[1]);
});

test("empty graphs produce an empty trace script", () => {
  assert.deepEqual(buildTraceOrder([], []), []);
  assert.deepEqual(buildTraceSteps([], []), []);
});
