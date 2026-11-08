"use client";

/**
 * Interactive data-flow tracer engine.
 *
 * The tracer never executes code - it walks the parsed graph, derives a
 * deterministic topological order from the edges, and simulates the payload
 * that would flow into each node. Every value is plain, JSON-serialisable
 * data so the Inspector can render the exact step state.
 */

const MAX_TEXT = 120;

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function text(value) {
  if (value === undefined || value === null) return "";
  return typeof value === "string" ? value : String(value);
}

function clip(value, limit = MAX_TEXT) {
  const flat = text(value).replace(/\s+/g, " ").trim();
  if (flat.length <= limit) return flat;
  return `${flat.slice(0, limit - 1).trimEnd()}…`;
}

function idOf(node) {
  return text(node && node.id);
}

function sortKey(node) {
  const data = (node && node.data) || {};
  const entry = data.is_entry || data.type === "entrypoint" ? 0 : 1;
  const rawLine = Number(data.line);
  const line =
    Number.isFinite(rawLine) && rawLine > 0 ? rawLine : Number.MAX_SAFE_INTEGER;
  return [entry, line, idOf(node)];
}

function compareNodes(a, b) {
  const left = sortKey(a);
  const right = sortKey(b);
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] < right[index]) return -1;
    if (left[index] > right[index]) return 1;
  }
  return 0;
}

/**
 * Deterministic topological order of the graph's nodes.
 *
 * Kahn's algorithm is used so dependencies always run before their consumers.
 * Ties are broken by entry-point status, source line then id, which keeps the
 * simulated sequence stable across renders. Nodes inside a cycle are appended
 * afterwards instead of being dropped.
 *
 * @param {Array<Record<string, unknown>>} nodes
 * @param {Array<Record<string, unknown>>} edges
 * @returns {Array<Record<string, unknown>>}
 */
export function buildTraceOrder(nodes = [], edges = []) {
  const safeNodes = asArray(nodes).filter((node) => node && idOf(node));
  const byId = new Map(safeNodes.map((node) => [idOf(node), node]));
  const indegree = new Map();
  const adjacency = new Map();

  for (const node of safeNodes) {
    const id = idOf(node);
    indegree.set(id, 0);
    adjacency.set(id, new Set());
  }

  for (const edge of asArray(edges)) {
    if (!edge) continue;
    const source = text(edge.source);
    const target = text(edge.target);
    if (!byId.has(source) || !byId.has(target) || source === target) continue;
    const targets = adjacency.get(source);
    if (!targets || targets.has(target)) continue;
    targets.add(target);
    indegree.set(target, (indegree.get(target) || 0) + 1);
  }

  const ready = safeNodes
    .filter((node) => indegree.get(idOf(node)) === 0)
    .sort(compareNodes);
  const ordered = [];

  while (ready.length > 0) {
    const current = ready.shift();
    ordered.push(current);
    const next = [...(adjacency.get(idOf(current)) || [])]
      .map((id) => byId.get(id))
      .filter(Boolean)
      .sort(compareNodes);

    for (const target of next) {
      const id = idOf(target);
      const remaining = (indegree.get(id) || 0) - 1;
      indegree.set(id, remaining);
      if (remaining !== 0) continue;

      let insertAt = ready.length;
      for (let index = 0; index < ready.length; index += 1) {
        if (compareNodes(target, ready[index]) < 0) {
          insertAt = index;
          break;
        }
      }
      ready.splice(insertAt, 0, target);
    }
  }

  if (ordered.length < safeNodes.length) {
    const seen = new Set(ordered.map(idOf));
    ordered.push(
      ...safeNodes.filter((node) => !seen.has(idOf(node))).sort(compareNodes)
    );
  }

  return ordered;
}

function sampleValue(name, index) {
  const key = text(name).toLowerCase();
  if (/(id|index|page|limit|count|size|port|quantity|qty)$/.test(key)) {
    return index + 1;
  }
  if (/^(is|has|can|should|enable|verbose|debug|dry)/.test(key)) {
    return false;
  }
  if (/(list|items|rows|users|results|tags|names|values|data)$/.test(key)) {
    return [];
  }
  if (/(price|amount|total|rate|score|balance|cost)$/.test(key)) {
    return 0;
  }
  return `<${text(name)}>`;
}

function extractRoute(data) {
  const decorators = asArray(data.decorators).map(text);
  const expression = decorators.find((decorator) =>
    /(route|api_route|get|post|put|patch|delete|websocket)\s*\(/i.test(decorator)
  );
  const match = text(expression).match(/['"`]([^'"`]+)['"`]/);
  return match ? match[1] : null;
}

function extractQuery(code) {
  const line = text(code)
    .split(/\r?\n/)
    .map((entry) => entry.trim())
    .find((entry) =>
      /\b(select|insert|update|delete|from|query|execute)\b/i.test(entry)
    );
  return line ? clip(line) : null;
}

function simulateOutput(node, input, index) {
  const data = node.data || {};
  const type = text(data.type || node.type || "action");
  const label = text(data.label || idOf(node));
  const code = text(data.code);

  if (type === "database") {
    return {
      status: "ok",
      query: extractQuery(code),
      rows: [],
      row_count: 0,
    };
  }

  if (
    /\b(select|insert|update|delete)\b/i.test(code) &&
    /\b(from|into|set|where)\b/i.test(code)
  ) {
    return {
      status: "ok",
      query: extractQuery(code),
      rows: [],
      row_count: 0,
    };
  }

  if (type === "condition") {
    return {
      evaluated: Object.keys(input).length > 0,
      branch: index % 2 === 0 ? "then" : "else",
    };
  }

  if (type === "loop") {
    return { iteration: 1, items: [] };
  }

  if (type === "entrypoint") {
    return {
      request: {
        route: extractRoute(data) || label,
        method: "GET",
        authenticated: false,
      },
      accepted: true,
    };
  }

  if (type === "error_handler") {
    return { handled: false, error: null };
  }

  return {
    status: "ok",
    value: `${label} result`,
    input_keys: Object.keys(input),
  };
}

/**
 * Build the playback script for the current graph.
 *
 * Each step carries the active node id, the edge ids that connect it to its
 * neighbours, and a simulated `{ input, output }` payload. Upstream outputs are
 * propagated into downstream inputs so the Inspector can show data moving
 * across the graph rather than a static placeholder.
 *
 * @param {Array<Record<string, unknown>>} nodes
 * @param {Array<Record<string, unknown>>} edges
 * @returns {Array<Record<string, unknown>>}
 */
export function buildTraceSteps(nodes = [], edges = []) {
  const order = buildTraceOrder(nodes, edges);
  const byId = new Map(order.map((node) => [idOf(node), node]));
  const inbound = new Map();
  const outbound = new Map();

  for (const edge of asArray(edges)) {
    if (!edge) continue;
    const source = text(edge.source);
    const target = text(edge.target);
    if (!byId.has(source) || !byId.has(target) || source === target) continue;

    const edgeId = text(edge.id) || `${source}->${target}`;
    if (!inbound.has(target)) inbound.set(target, []);
    inbound.get(target).push({ id: edgeId, source, target });
    if (!outbound.has(source)) outbound.set(source, []);
    outbound.get(source).push({ id: edgeId, source, target });
  }

  const outputs = new Map();

  return order.map((node, index) => {
    const id = idOf(node);
    const data = node.data || {};
    // Parallel edges are valid in the canvas, but the simulated payload must
    // merge each upstream node exactly once.
    const seenParents = new Set();
    const upstream = (inbound.get(id) || [])
      .filter((edge) => {
        if (seenParents.has(edge.source)) return false;
        seenParents.add(edge.source);
        return true;
      })
      .map((edge) => byId.get(edge.source))
      .filter(Boolean);

    const input = {};
    const usedKeys = new Set();

    for (const parent of upstream) {
      const parentId = idOf(parent);
      const parentLabel = text((parent.data || {}).label || parentId);
      let key =
        parentLabel
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "_")
          .replace(/^_+|_+$/g, "") || `node_${parentId}`;
      while (usedKeys.has(key)) key = `${key}_${parentId}`;
      usedKeys.add(key);
      input[key] = outputs.get(parentId);
    }

    for (const raw of asArray(data.parameters)) {
      const name = typeof raw === "string" ? raw : raw && raw.name;
      const safeName = text(name);
      if (!safeName || safeName in input) continue;
      input[safeName] = sampleValue(safeName, index);
    }

    const output = simulateOutput(node, input, index);
    outputs.set(id, output);

    const edgeIds = [
      ...new Set([
        ...(inbound.get(id) || []).map((edge) => edge.id),
        ...(outbound.get(id) || []).map((edge) => edge.id),
      ]),
    ];

    return {
      index,
      nodeId: id,
      label: text(data.label || id),
      type: text(data.type || node.type || "action"),
      line: data.line ?? null,
      edgeIds,
      payload: {
        step: index + 1,
        total_steps: order.length,
        node: {
          id,
          label: text(data.label || id),
          type: text(data.type || node.type || "action"),
        },
        upstream: upstream.map((parent) =>
          text((parent.data || {}).label || idOf(parent))
        ),
        input,
        output,
      },
    };
  });
}

/**
 * Return the currently active trace step, or null when the trace is idle.
 *
 * @param {{ active?: boolean, index?: number, steps?: unknown }} tracer
 * @returns {Record<string, unknown> | null}
 */
export function traceStepAt(tracer) {
  if (!tracer || !tracer.active) return null;
  const steps = asArray(tracer.steps);
  const index = Number.isInteger(tracer.index) ? tracer.index : -1;
  if (index < 0 || index >= steps.length) return null;
  return steps[index];
}
