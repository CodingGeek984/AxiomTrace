"use client";

import { useCallback } from "react";
import dagre from "dagre";

import { LAYOUT, TIER_BY_ID, tierForGroup } from "@/config/theme";
import { toNumber } from "@/lib/utils";

export const TIER_ORDER = [1, 2, 3, 4];

/**
 * Measure a React Flow node's footprint, honouring explicit width/height so
 * custom node sizes still lay out correctly.
 *
 * @param {{ width?: number, height?: number, measured?: { width?: number, height?: number }, style?: { width?: number, height?: number } }} node
 * @returns {{ width: number, height: number }}
 */
function measureNode(node) {
  if (!node) {
    return { width: LAYOUT.nodeWidth, height: LAYOUT.nodeHeight };
  }
  const width =
    toNumber(node.measured?.width, 0) ||
    toNumber(node.width, 0) ||
    toNumber(node.style?.width, 0) ||
    LAYOUT.nodeWidth;
  const height =
    toNumber(node.measured?.height, 0) ||
    toNumber(node.height, 0) ||
    toNumber(node.style?.height, 0) ||
    LAYOUT.nodeHeight;
  return { width, height };
}

/**
 * Resolve the enterprise tier (1-4) for a node. Prefers the node tier set by
 * the graph mapper; falls back to the node's domain/group so legacy payloads
 * (saved before tiers existed) still band correctly.
 *
 * @param {Record<string, unknown>} node
 * @returns {number}
 */
function tierOf(node) {
  const tier = Number(node?.data?.tier);
  if (TIER_BY_ID[tier]) return tier;
  return tierForGroup(node?.data?.group);
}

/**
 * Lay out a node/edge set as four stacked enterprise swimlane bands.
 *
 * Grouping engine: nodes are partitioned into 4 architectural zones
 * (Presentation/UI, API & Gateway, Business Logic, Data & Storage) and each
 * zone is laid out as its own top-to-bottom dagre subgraph (hierarchical
 * sub-graphs with rankSep 160, nodeSep 100). Ranks wider than
 * `LAYOUT.maxColumns` wrap into additional rows so a dense zone can never
 * collapse into one flat horizontal row, then zones are stacked vertically
 * with a full rankSep gap between bands. This guarantees strict
 * TB flow, zero node overlap, and a clean
 * `fitView({ duration: 800, padding: 0.25 })` target - no 15% crush.
 *
 * Self-referential edges are skipped to avoid dagre cycle warnings, mirroring
 * how the AST builder never emits a node pointing back at itself.
 *
 * @param {Array<Record<string, unknown>>} nodes
 * @param {Array<Record<string, unknown>>} edges
 * @returns {Array<Record<string, unknown>>}
 */
export function layoutWithDagre(nodes, edges) {
  const safeNodes = Array.isArray(nodes) ? nodes : [];
  const safeEdges = Array.isArray(edges) ? edges : [];
  if (safeNodes.length === 0) return safeNodes;

  const sizes = new Map();
  for (const node of safeNodes) {
    sizes.set(node.id, measureNode(node));
  }

  // Partition into tiers, ordered by domain layer then label so same-rank
  // nodes stay grouped by role instead of drifting with input order.
  const byTier = new Map(TIER_ORDER.map((tier) => [tier, []]));
  const ordered = safeNodes
    .map((node, index) => ({ node, index }))
    .sort((a, b) => {
      const tierA = tierOf(a.node);
      const tierB = tierOf(b.node);
      if (tierA !== tierB) return tierA - tierB;
      const layerA = Number(a.node?.data?.layer ?? 99);
      const layerB = Number(b.node?.data?.layer ?? 99);
      if (layerA !== layerB) return layerA - layerB;
      const labelA = String(a.node?.data?.label || a.node?.id || "");
      const labelB = String(b.node?.data?.label || b.node?.id || "");
      return labelA.localeCompare(labelB) || a.index - b.index;
    });
  for (const { node } of ordered) {
    byTier.get(tierOf(node)).push(node);
  }

  const RANK_SEP = LAYOUT.rankSeparation; // 160px between ranks / tier bands
  const NODE_SEP = LAYOUT.nodeSeparation; // 100px between sibling nodes
  const positions = new Map();
  let cursorY = 0;

  for (const tier of TIER_ORDER) {
    const tierNodes = byTier.get(tier) || [];
    if (tierNodes.length === 0) continue;

    if (tierNodes.length === 1) {
      const node = tierNodes[0];
      const size = sizes.get(node.id) || measureNode(node);
      positions.set(node.id, { x: 0, y: cursorY });
      cursorY += size.height + RANK_SEP;
      continue;
    }

    const graphlib = new dagre.graphlib.Graph();
    graphlib.setDefaultEdgeLabel(() => ({}));
    graphlib.setGraph({
      rankdir: LAYOUT.rankDirection,
      nodesep: NODE_SEP,
      ranksep: RANK_SEP,
      marginx: 0,
      marginy: 0,
      ranker: "network-simplex",
    });

    const ids = new Set(tierNodes.map((node) => node.id));
    for (const node of tierNodes) {
      graphlib.setNode(node.id, sizes.get(node.id) || measureNode(node));
    }
    for (const edge of safeEdges) {
      if (!edge || edge.source === edge.target) continue;
      if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
      graphlib.setEdge(String(edge.source), String(edge.target));
    }

    dagre.layout(graphlib);

    // Dagre packs every same-rank card side by side, so a dense tier with
    // few intra-tier edges degenerates into one very wide horizontal row.
    // Reflow the band: keep dagre's rank order, chunk each rank into rows of
    // at most LAYOUT.maxColumns, centre the rows inside the band and stack
    // them with the rankSeparation gap - strict TB flow, bounded width and
    // exactly the nodeSeparation/rankSeparation the backend scanner emits.
    const maxColumns = Math.max(1, Number(LAYOUT.maxColumns) || 6);
    const placed = tierNodes
      .map((node) => {
        const laid = graphlib.node(node.id);
        const size = sizes.get(node.id) || measureNode(node);
        return {
          node,
          size,
          x: laid ? laid.x - size.width / 2 : 0,
          y: laid ? laid.y - size.height / 2 : 0,
        };
      })
      .sort((a, b) => a.y - b.y || a.x - b.x);

    const ranks = [];
    for (const entry of placed) {
      const rank = ranks[ranks.length - 1];
      if (!rank || Math.abs(entry.y - rank.y) > 1) {
        ranks.push({ y: entry.y, entries: [entry] });
      } else {
        rank.entries.push(entry);
      }
    }

    const rows = [];
    for (const rank of ranks) {
      const inRank = rank.entries.sort((a, b) => a.x - b.x);
      for (let i = 0; i < inRank.length; i += maxColumns) {
        rows.push(inRank.slice(i, i + maxColumns));
      }
    }

    const rowWidth = (row) =>
      row.reduce((total, entry) => total + entry.size.width, 0) +
      Math.max(0, row.length - 1) * NODE_SEP;
    const bandWidth = rows.reduce(
      (widest, row) => Math.max(widest, rowWidth(row)),
      0
    );

    let tierBottom = 0;
    let tierY = cursorY;
    for (const row of rows) {
      const rowHeight = row.reduce(
        (height, entry) => Math.max(height, entry.size.height),
        0
      );
      let x = Math.round((bandWidth - rowWidth(row)) / 2);
      for (const entry of row) {
        positions.set(entry.node.id, { x, y: Math.round(tierY) });
        x += entry.size.width + NODE_SEP;
      }
      tierY += rowHeight + RANK_SEP;
      tierBottom = tierY - RANK_SEP;
    }
    cursorY = tierBottom + RANK_SEP;
  }

  return safeNodes.map((node) => {
    const position = positions.get(node.id);
    if (!position) return node;
    return { ...node, position };
  });
}

/**
 * Hook exposing the auto-layout routine plus bulk fit helpers.
 *
 * @returns {{
 *   layoutNodes: (nodes: Array<Record<string, unknown>>, edges: Array<Record<string, unknown>>) => Array<Record<string, unknown>>,
 * }}
 */
export function useGraphLayout() {
  const layoutNodes = useCallback((nodes, edges) => layoutWithDagre(nodes, edges), []);

  return { layoutNodes };
}

export default useGraphLayout;
