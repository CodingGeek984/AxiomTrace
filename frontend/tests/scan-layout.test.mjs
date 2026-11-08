import assert from "node:assert/strict";
import test from "node:test";

import { FIT_VIEW, LAYOUT } from "../src/config/theme.js";
import { buildSwimlanes, layoutGraph, toFlowGraph } from "../src/lib/graph.js";
import { layoutWithDagre } from "../src/hooks/useGraphLayout.js";
import { normalizeGraphPayload } from "../src/store/useGraphStore.js";

/**
 * Synthetic SCAN PROJECT payload carrying the legacy flat coordinates the
 * scanner used to emit: four horizontal rows at y = 0 / 150 / 300 / 450 with
 * cards spread along x. The unified pipeline must reflow this into the same
 * structured multi-tier TB layout a single-file PARSE CODE run produces.
 */
function scanPayload() {
  const specs = [
    { tier: 1, group: "presentation", layer: 0, prefix: "ui", count: 8 },
    { tier: 2, group: "api", layer: 1, prefix: "api", count: 6 },
    { tier: 3, group: "service", layer: 2, prefix: "svc", count: 14 },
    { tier: 4, group: "data", layer: 3, prefix: "db", count: 5 },
  ];
  const flatY = { 1: 0, 2: 150, 3: 300, 4: 450 };
  const nodes = [];
  const edges = [];
  const byTier = {};
  let seq = 0;

  for (const spec of specs) {
    byTier[spec.tier] = [];
    for (let i = 0; i < spec.count; i += 1) {
      seq += 1;
      const id = `p${seq}`;
      byTier[spec.tier].push(id);
      const code = `def main(payload=None):\n    return {"node": "${id}"}\n`;
      nodes.push({
        id,
        type: "service",
        position: { x: 80 + i * 400, y: flatY[spec.tier] },
        data: {
          id,
          label: `${spec.prefix}-${i}.py`,
          type: "service",
          tier: spec.tier,
          group: spec.group,
          layer: spec.layer,
          code,
          source: code,
          file: `src/${spec.prefix}/${spec.prefix}-${i}.py`,
          module: `src.${spec.prefix}.${spec.prefix}_${i}`,
          language: "python",
        },
      });
    }
  }

  // Cross-tier flow (UI -> API -> Logic -> Data) plus one intra-tier edge so
  // dagre still runs with dependencies inside a band.
  const [ui, api, svc, db] = [byTier[1], byTier[2], byTier[3], byTier[4]];
  ui.forEach((id, i) => edges.push({ id: `eu${i}`, source: id, target: api[i % api.length] }));
  api.forEach((id, i) => edges.push({ id: `ea${i}`, source: id, target: svc[i % svc.length] }));
  svc.slice(0, 5).forEach((id, i) => edges.push({ id: `es${i}`, source: id, target: db[i % db.length] }));
  edges.push({ id: "eui", source: ui[0], target: ui[1] });

  return {
    nodes,
    edges,
    groups: [],
    tiers: [],
    language: "mixed",
    stats: {},
    errors: [],
    partial: false,
  };
}

/** The exact core of `replaceGraph` (store) used by both PARSE and SCAN. */
function runUnifiedPipeline(payload) {
  const normalized = normalizeGraphPayload(payload);
  const mapped = toFlowGraph(normalized.graph);
  return { mapped, nodes: layoutGraph(mapped.nodes, mapped.edges) };
}

function boxesOverlap(a, b) {
  const aw = LAYOUT.nodeWidth;
  const ah = LAYOUT.nodeHeight;
  const bw = LAYOUT.nodeWidth;
  const bh = LAYOUT.nodeHeight;
  return (
    a.position.x < b.position.x + bw &&
    b.position.x < a.position.x + aw &&
    a.position.y < b.position.y + bh &&
    b.position.y < a.position.y + ah
  );
}

test("auto-fit viewport contract is 800ms / 0.25 padding", () => {
  assert.equal(FIT_VIEW.duration, 800);
  assert.equal(FIT_VIEW.padding, 0.25);
  assert.equal(LAYOUT.rankDirection, "TB");
  assert.equal(LAYOUT.maxColumns, 6);
});

test("SCAN PROJECT and PARSE CODE share one layout processor", () => {
  const { mapped } = runUnifiedPipeline(scanPayload());
  const viaStore = layoutGraph(mapped.nodes, mapped.edges);
  const viaDagre = layoutWithDagre(mapped.nodes, mapped.edges);
  assert.deepEqual(
    viaStore.map((node) => node.position),
    viaDagre.map((node) => node.position)
  );
  // The flat payload rows (y = 0/150/300/450) are gone: the graph now spans
  // well beyond the legacy 600px-tall dump.
  const maxY = Math.max(...viaStore.map((node) => node.position.y));
  assert.ok(maxY > 450, `expected stacked bands, got maxY=${maxY}`);
});

test("scan output stacks the four tiers top-to-bottom with rankSep gaps", () => {
  const { nodes } = runUnifiedPipeline(scanPayload());
  const byTier = new Map();
  for (const node of nodes) {
    const tier = Number(node.data.tier);
    if (!byTier.has(tier)) byTier.set(tier, []);
    byTier.get(tier).push(node);
  }
  assert.deepEqual([...byTier.keys()].sort(), [1, 2, 3, 4]);
  assert.deepEqual([...byTier.values()].map((list) => list.length), [8, 6, 14, 5]);

  for (const tier of [1, 2, 3]) {
    const current = byTier.get(tier);
    const next = byTier.get(tier + 1);
    const bottom = Math.max(
      ...current.map((node) => node.position.y + LAYOUT.nodeHeight)
    );
    const top = Math.min(...next.map((node) => node.position.y));
    assert.ok(
      top - bottom >= LAYOUT.rankSeparation,
      `tier ${tier + 1} starts ${top - bottom}px after tier ${tier} (need ${LAYOUT.rankSeparation})`
    );
  }
});

test("wrapped rows never overlap and respect node separation", () => {
  const { nodes } = runUnifiedPipeline(scanPayload());
  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      assert.ok(
        !boxesOverlap(nodes[i], nodes[j]),
        `nodes ${nodes[i].id} and ${nodes[j].id} overlap`
      );
    }
  }
  // Same-row siblings keep the full nodeSep gap.
  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const a = nodes[i].position;
      const b = nodes[j].position;
      if (a.y !== b.y) continue;
      const gap = Math.abs(a.x - b.x) - LAYOUT.nodeWidth;
      assert.ok(gap >= LAYOUT.nodeSeparation, `row gap ${gap}px < nodeSep`);
    }
  }
  // Dense bands wrap at maxColumns instead of one endless horizontal row.
  const maxRight = Math.max(
    ...nodes.map((node) => node.position.x + LAYOUT.nodeWidth)
  );
  const maxWidth =
    LAYOUT.maxColumns * LAYOUT.nodeWidth +
    (LAYOUT.maxColumns - 1) * LAYOUT.nodeSeparation;
  assert.ok(maxRight <= maxWidth, `graph ${maxRight}px wide > ${maxWidth}px cap`);
});

test("every scanned node keeps bound source for the inspector and sandbox", () => {
  const { mapped } = runUnifiedPipeline(scanPayload());
  for (const node of mapped.nodes) {
    assert.ok(String(node.data.code || "").trim().length > 0, `${node.id} has no code`);
    assert.ok(String(node.data.source || "").trim().length > 0, `${node.id} has no source`);
    assert.ok(node.data.file, `${node.id} has no file path`);
    assert.ok(node.data.module, `${node.id} has no module name`);
  }
});

test("swimlane bands render one ordered strip per populated tier", () => {
  const { nodes } = runUnifiedPipeline(scanPayload());
  const bands = buildSwimlanes(nodes);
  assert.deepEqual(bands.map((band) => band.data.tier), [1, 2, 3, 4]);
  for (let i = 0; i < bands.length - 1; i += 1) {
    const bottom = bands[i].position.y + bands[i].height;
    assert.ok(
      bottom < bands[i + 1].position.y,
      `band T${bands[i].data.tier} collides with T${bands[i + 1].data.tier}`
    );
  }
});
