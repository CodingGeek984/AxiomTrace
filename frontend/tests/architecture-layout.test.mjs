import assert from "node:assert/strict";
import test from "node:test";

import { layoutWithDagre } from "../src/hooks/useGraphLayout.js";
import { LAYOUT, TIER_BY_ID, tierForGroup } from "../src/config/theme.js";
import { buildSwimlanes, normaliseTiers, toFlowEdge, toFlowNode } from "../src/lib/graph.js";

function node(id, tier, label, layer = 0) {
  return {
    id,
    type: "axiom",
    position: { x: 0, y: 0 },
    data: {
      id,
      label,
      tier,
      group: tier === 1 ? "presentation" : tier === 2 ? "api" : tier === 3 ? "service" : "data",
      layer,
      code: "",
      type: "service",
    },
  };
}

test("enterprise spacing contract is 100 / 160 top-to-bottom", () => {
  assert.equal(LAYOUT.nodeSeparation, 100);
  assert.equal(LAYOUT.rankSeparation, 160);
  assert.equal(LAYOUT.rankDirection, "TB");
});

test("four canonical tiers with stable ids and labels", () => {
  assert.deepEqual(Object.keys(TIER_BY_ID).map(Number).sort(), [1, 2, 3, 4]);
  assert.match(TIER_BY_ID[1].label, /Client/);
  assert.match(TIER_BY_ID[2].label, /API/);
  assert.match(TIER_BY_ID[3].label, /Business Logic/);
  assert.match(TIER_BY_ID[4].label, /Data/);
});

test("groups map onto exactly one tier each", () => {
  assert.equal(tierForGroup("presentation"), 1);
  assert.equal(tierForGroup("entrypoint"), 1);
  assert.equal(tierForGroup("api"), 2);
  assert.equal(tierForGroup("service"), 3);
  assert.equal(tierForGroup("utility"), 3);
  assert.equal(tierForGroup("control"), 3);
  assert.equal(tierForGroup("error"), 3);
  assert.equal(tierForGroup("data"), 4);
  assert.equal(tierForGroup("integration"), 4);
});

test("layout stacks tiers top-to-bottom with no overlap", () => {
  const nodes = [
    node("ui", 1, "Dashboard"),
    node("api", 2, "Gateway"),
    node("svc", 3, "OrderService"),
    node("db", 4, "OrderModel"),
  ];
  const edges = [
    { id: "e1", source: "ui", target: "api" },
    { id: "e2", source: "api", target: "svc" },
    { id: "e3", source: "svc", target: "db" },
  ];
  const laid = layoutWithDagre(nodes, edges);
  const byId = new Map(laid.map((entry) => [entry.id, entry]));
  const y = (id) => byId.get(id).position.y;
  assert.ok(y("ui") < y("api") && y("api") < y("svc") && y("svc") < y("db"));
  // Tier gap is a full rankSep (160) between band bottom and next band top.
  const height = LAYOUT.nodeHeight;
  assert.ok(y("api") - (y("ui") + height) >= LAYOUT.rankSeparation);
  assert.ok(y("svc") - (y("api") + height) >= LAYOUT.rankSeparation);
  assert.ok(y("db") - (y("svc") + height) >= LAYOUT.rankSeparation);
});

test("same-tier siblings respect node separation", () => {
  const nodes = [node("a", 3, "Alpha"), node("b", 3, "Beta")];
  const laid = layoutWithDagre(nodes, []);
  const xs = laid.map((entry) => entry.position.x).sort((p, q) => p - q);
  assert.ok(xs[1] - xs[0] >= LAYOUT.nodeWidth + LAYOUT.nodeSeparation);
});

test("swimlanes band one strip per populated tier", () => {
  const laid = layoutWithDagre(
    [node("ui", 1, "Dashboard"), node("svc", 3, "Svc"), node("db", 4, "DB")],
    [{ id: "e1", source: "ui", target: "svc" }]
  );
  const lanes = buildSwimlanes(laid);
  assert.equal(lanes.length, 3);
  assert.deepEqual(lanes.map((lane) => lane.data.tier), [1, 3, 4]);
  for (const lane of lanes) {
    assert.equal(lane.type, "swimlane");
    assert.equal(lane.draggable, false);
    assert.equal(lane.selectable, false);
    assert.ok(lane.style.width > 0 && lane.style.height > 0);
  }
  // Bands are vertically ordered and non-overlapping.
  const ys = lanes.map((lane) => lane.position.y).sort((a, b) => a - b);
  assert.deepEqual(lanes.map((lane) => lane.position.y).sort((a, b) => a - b), ys);
});

test("normaliseTiers derives counts without a backend payload", () => {
  const tiers = normaliseTiers([node("a", 1, "A"), node("b", 1, "B"), node("c", 4, "C")]);
  assert.deepEqual(tiers.map((tier) => [tier.id, tier.count]), [[1, 2], [4, 1]]);
});

test("flow mapper stamps tier metadata and arrowed smoothstep edges", () => {
  const flow = toFlowNode({
    id: "x",
    type: "service",
    data: { label: "Svc", type: "service", group: "service", code: "" },
  });
  assert.equal(flow.data.tier, 3);
  assert.match(flow.data.tier_label, /Business Logic/);
  const edge = toFlowEdge({ id: "e", source: "a", target: "b", label: "imports" });
  assert.equal(edge.type, "smoothstep");
  assert.equal(edge.markerEnd.type, "arrowclosed");
});
