"use client";

import {
  DEFAULT_TYPE,
  GROUP_META,
  SEMANTIC_TYPES,
  TIERS,
  TIER_BY_ID,
  TYPE_META,
  groupMeta,
  tierMeta,
  typeMeta,
} from "@/config/theme";
import { layoutWithDagre } from "@/hooks/useGraphLayout";

export {
  SEMANTIC_TYPES,
  TYPE_META,
  GROUP_META,
  TIERS,
  TIER_BY_ID,
  DEFAULT_TYPE,
  groupMeta,
  tierMeta,
  typeMeta,
};

function firstDefined(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

export function toFlowNode(node) {
  const data = node.data || {};
  const semanticType = SEMANTIC_TYPES.includes(data.type)
    ? data.type
    : SEMANTIC_TYPES.includes(node.type)
      ? node.type
      : DEFAULT_TYPE;

  // The parser flags recovered regions with `data.diagnostic` (boolean) and
  // puts the human explanation in `data.error`; both shapes are normalised
  // into a single string so the canvas can render it directly.
  const flagged = data.diagnostic ?? node.diagnostic ?? null;
  const diagnostic = flagged
    ? typeof flagged === "string"
      ? flagged
      : String(data.error || node.error || "Recovered from a syntax error.")
    : null;

  const meta = typeMeta(semanticType);
  const group = firstDefined(data.group, node.group, null);
  const groupInfo = group ? groupMeta(group) : null;
  const tier = data.tier ?? node.tier ?? groupInfo?.tier ?? 3;
  const tierInfo = tierMeta(tier);
  const parameters = Array.isArray(data.parameters)
    ? data.parameters
    : Array.isArray(node.parameters)
      ? node.parameters
      : [];
  // Source binding: `code` and `source` are kept in sync from whichever the
  // payload carries, so the Inspector, the sandbox and the transpiler all see
  // the same non-empty text no matter which shape the backend returned.
  const boundSource = firstDefined(
    data.code,
    node.code,
    data.source,
    node.source,
    ""
  );
  const filePath = firstDefined(data.file, node.file, data.path, node.path, "");

  return {
    id: String(node.id),
    type: "axiom",
    position: node.position
      ? { x: Number(node.position.x) || 0, y: Number(node.position.y) || 0 }
      : { x: Number(node.x) || 0, y: Number(node.y) || 0 },
    data: {
      ...data,
      id: String(node.id),
      type: semanticType,
      semantic_type: firstDefined(
        data.semantic_type,
        node.semantic_type,
        semanticType
      ),
      tag: firstDefined(data.tag, node.tag, null),
      label: data.label || node.label || String(node.id),
      code: boundSource,
      source: firstDefined(data.source, node.source, boundSource, ""),
      file: filePath,
      path: filePath,
      module: firstDefined(data.module, node.module, ""),
      preview: firstDefined(data.preview, node.preview, ""),
      line: data.line ?? node.line ?? null,
      end_line: data.end_line ?? node.end_line ?? null,
      language: data.language || node.language || "python",
      kind: data.kind || node.kind || semanticType,
      async: Boolean(data.async ?? node.async),
      status: data.status || "idle",
      diagnostic,
      category: firstDefined(data.category, node.category, meta.label),
      group,
      group_label: firstDefined(
        data.group_label,
        node.group_label,
        groupInfo?.label
      ),
      tier,
      tier_label: firstDefined(data.tier_label, node.tier_label, tierInfo?.label),
      layer: firstDefined(data.layer, node.layer, groupInfo?.order),
      description: firstDefined(data.description, node.description, ""),
      node_description: firstDefined(
        data.node_description,
        node.node_description,
        null
      ),
      complexity: firstDefined(data.complexity, node.complexity, "O(1)"),
      parameters,
      returns: firstDefined(data.returns, node.returns, null),
      parent_id: firstDefined(data.parent_id, node.parent_id, null),
      parent_label: firstDefined(data.parent_label, node.parent_label, null),
      decorators: Array.isArray(data.decorators)
        ? data.decorators
        : Array.isArray(node.decorators)
          ? node.decorators
          : [],
      is_entry: Boolean(data.is_entry ?? node.is_entry),
      is_vulnerable: Boolean(data.is_vulnerable ?? node.is_vulnerable),
      severity: firstDefined(data.severity, node.severity, null),
      vulnerability_type: firstDefined(
        data.vulnerability_type,
        node.vulnerability_type,
        null
      ),
      mitigation: firstDefined(data.mitigation, node.mitigation, null),
      security_description: firstDefined(
        data.security_description,
        node.security_description,
        null
      ),
      security_findings: Array.isArray(data.security_findings)
        ? data.security_findings
        : Array.isArray(node.security_findings)
          ? node.security_findings
          : [],
      security: data.security || node.security || null,
    },
    draggable: true,
    selectable: true,
  };
}

export function toFlowEdge(edge) {
  return {
    id: String(edge.id || `${edge.source}-${edge.target}`),
    source: String(edge.source),
    target: String(edge.target),
    type: "smoothstep",
    animated: Boolean(edge.animated),
    label: edge.label || undefined,
    markerEnd: {
      type: "arrowclosed",
      color: "#5E6473",
      width: 18,
      height: 18,
    },
    style: { stroke: "#D0D2D8", strokeWidth: 1.6 },
    labelStyle: { fill: "#5E6473", fontSize: 10, fontWeight: 600 },
    labelBgStyle: { fill: "#FFFFFF", fillOpacity: 1 },
    labelBgPadding: [6, 3],
    labelBgBorderRadius: 4,
    data: { kind: edge.kind || "sequence" },
  };
}

export function toFlowGraph(graph = {}) {
  const rawNodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const rawEdges = Array.isArray(graph.edges) ? graph.edges : [];
  const nodes = rawNodes.filter(Boolean).map(toFlowNode);
  const known = new Set(nodes.map((node) => node.id));
  const edges = rawEdges
    .filter(
      (edge) =>
        edge &&
        known.has(String(edge.source)) &&
        known.has(String(edge.target))
    )
    .map(toFlowEdge);
  return {
    nodes,
    edges,
    language: graph.language || "python",
    stats: graph.stats || {},
    groups: normaliseGroups(nodes, graph.groups),
    tiers: normaliseTiers(nodes, graph.tiers),
  };
}

/**
 * Keep the parser-provided domains, or rebuild them from node metadata so a
 * graph saved before groups existed still renders the same ordered bands.
 */
export function normaliseGroups(nodes, rawGroups) {
  if (Array.isArray(rawGroups) && rawGroups.length > 0) {
    return rawGroups
      .filter((group) => group && group.id)
      .map((group) => ({
        ...group,
        nodeIds: Array.isArray(group.nodeIds) ? group.nodeIds : [],
      }))
      .sort(
        (a, b) =>
          (a.order ?? GROUP_META[a.id]?.order ?? 99) -
          (b.order ?? GROUP_META[b.id]?.order ?? 99)
      );
  }

  const buckets = new Map();
  for (const node of Array.isArray(nodes) ? nodes : []) {
    const groupId = node?.data?.group;
    if (!groupId) continue;
    if (!buckets.has(groupId)) buckets.set(groupId, []);
    buckets.get(groupId).push(node.id);
  }

  return [...buckets.entries()]
    .map(([id, nodeIds]) => {
      const meta = groupMeta(id);
      return {
        id,
        label: meta.label,
        short: meta.short,
        description: meta.description || "",
        order: meta.order,
        count: nodeIds.length,
        nodeIds,
      };
    })
    .sort((a, b) => a.order - b.order);
}

/**
 * Normalise the four enterprise tiers, preferring backend-provided payloads
 * and otherwise deriving counts from node metadata.
 */
export function normaliseTiers(nodes, rawTiers) {
  if (Array.isArray(rawTiers) && rawTiers.length > 0) {
    return rawTiers
      .filter((tier) => tier && tier.id)
      .map((tier) => {
        const meta = tierMeta(tier.id);
        return {
          ...tier,
          id: Number(tier.id),
          label: tier.label || meta.label,
          short: tier.short || meta.short,
          description: tier.description || meta.description || "",
          color: tier.color || meta.color,
          count:
            typeof tier.count === "number"
              ? tier.count
              : Array.isArray(tier.nodeIds)
                ? tier.nodeIds.length
                : 0,
          nodeIds: Array.isArray(tier.nodeIds) ? tier.nodeIds : [],
        };
      })
      .sort((a, b) => a.id - b.id);
  }

  const buckets = new Map();
  for (const node of Array.isArray(nodes) ? nodes : []) {
    if (!node || !node.id) continue;
    if (String(node.id).startsWith("swimlane-")) continue;
    const tier = Number(node?.data?.tier) || 3;
    if (!buckets.has(tier)) buckets.set(tier, []);
    buckets.get(tier).push(node.id);
  }

  return TIERS.filter((meta) => buckets.has(meta.id)).map((meta) => {
    const nodeIds = buckets.get(meta.id) || [];
    return {
      id: meta.id,
      label: meta.label,
      short: meta.short,
      description: meta.description,
      color: meta.color,
      count: nodeIds.length,
      nodeIds,
    };
  });
}

function swimNodeSize(node) {
  const width =
    Number(node?.measured?.width) ||
    Number(node?.width) ||
    Number(node?.style?.width) ||
    300;
  const height =
    Number(node?.measured?.height) ||
    Number(node?.height) ||
    Number(node?.style?.height) ||
    150;
  return { width, height };
}

/**
 * Build React Flow swimlane background nodes - one horizontal band per
 * enterprise tier, derived from laid-out node bounds with header space.
 * Bands sit behind cards (zIndex -1), are non-interactive, and never enter
 * the store (they are recomputed by the canvas on every render).
 */
export function buildSwimlanes(nodes) {
  const list = (Array.isArray(nodes) ? nodes : []).filter(
    (node) => node && node.id && !String(node.id).startsWith("swimlane-")
  );
  if (list.length === 0) return [];

  let globalMinX = Number.POSITIVE_INFINITY;
  let globalMaxX = Number.NEGATIVE_INFINITY;
  const byTier = new Map();
  for (const node of list) {
    const size = swimNodeSize(node);
    const x = Number(node.position?.x) || 0;
    const y = Number(node.position?.y) || 0;
    globalMinX = Math.min(globalMinX, x);
    globalMaxX = Math.max(globalMaxX, x + size.width);
    const tier = Number(node?.data?.tier) || 3;
    if (!byTier.has(tier)) {
      byTier.set(tier, { minY: y, maxY: y + size.height });
    } else {
      const band = byTier.get(tier);
      band.minY = Math.min(band.minY, y);
      band.maxY = Math.max(band.maxY, y + size.height);
    }
  }

  const PAD_X = 28;
  const PAD_TOP = 44;
  const PAD_BOTTOM = 28;
  const bandX = globalMinX - PAD_X;
  const bandWidth = globalMaxX - globalMinX + PAD_X * 2;

  return TIERS.filter((meta) => byTier.has(meta.id)).map((meta) => {
    const band = byTier.get(meta.id);
    const y = band.minY - PAD_TOP;
    const height = band.maxY - band.minY + PAD_TOP + PAD_BOTTOM;
    const members = list.filter((node) => (Number(node?.data?.tier) || 3) === meta.id);
    return {
      id: `swimlane-${meta.id}`,
      type: "swimlane",
      position: { x: bandX, y },
      width: bandWidth,
      height,
      style: { width: bandWidth, height },
      draggable: false,
      selectable: false,
      deletable: false,
      connectable: false,
      zIndex: -1,
      data: {
        tier: meta.id,
        tierLabel: meta.label,
        tierShort: meta.short,
        tierDescription: meta.description || "",
        tierColor: meta.color,
        count: members.length,
      },
    };
  });
}

export function layoutGraph(nodes, edges) {
  return layoutWithDagre(nodes, edges);
}

export function selectionSubset(nodes, edges, selectedIds) {
  const selected = new Set(selectedIds || []);
  if (selected.size === 0) {
    return { nodes, edges, isPartial: false };
  }
  const subsetNodes = nodes.filter((node) => selected.has(node.id));
  const subsetEdges = edges.filter(
    (edge) => selected.has(edge.source) && selected.has(edge.target)
  );
  return { nodes: subsetNodes, edges: subsetEdges, isPartial: true };
}

export function fileLabelFromPath(filePath) {
  if (!filePath) return null;
  const parts = String(filePath).split(/[\\/]/);
  return parts[parts.length - 1] || String(filePath);
}

export function lineLabel(data) {
  const start = data.line;
  const end = data.end_line;
  if (start && end && end !== start) return `L${start}-${end}`;
  if (start) return `L${start}`;
  return "L?";
}
