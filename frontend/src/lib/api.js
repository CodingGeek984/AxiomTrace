"use client";

import axios from "axios";

import { getDeepSeekKey } from "@/lib/aiKey";

/**
 * Base URL for the Flask backend.
 *
 * The legacy desktop client targeted `/api`; the v1 REST surface lives under
 * `/api/v1`. The backend exposes the same handlers on both prefixes, so a
 * single build-time env var configures the whole client.
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000/api/v1";

export const API_ORIGIN = API_BASE.replace(/\/api(\/v1)?\/?$/, "");

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  if (!config || !config.data) return config;
  if (typeof config.data === "string") return config;
  if (config.data instanceof FormData || config.data instanceof URLSearchParams) {
    return config;
  }
  if (typeof config.data === "object" && !Array.isArray(config.data)) {
    const key = getDeepSeekKey();
    if (!Object.prototype.hasOwnProperty.call(config.data, "api_key")) {
      if (key) {
        config.data = { ...config.data, api_key: key };
      }
    }
  }
  // Also expose the key as a header so non-JSON endpoints (and proxies)
  // can authenticate without sniffing the body.
  const headerKey = getDeepSeekKey();
  if (headerKey && config && config.headers && !config.headers["X-API-Key"]) {
    config.headers["X-API-Key"] = headerKey;
  }
  return config;
});

/**
 * Unwrap an axios promise into the backend envelope, normalising every failure
 * mode into a plain Error with a human-readable message.
 *
 * Server responses vary by endpoint (``{"ok": false, "error"}`` vs AI
 * ``{"status": "error", "message"}`` vs schema ``details`` payloads), so the
 * message extractor fans across all known shapes and flattens validation
 * detail objects instead of surfacing ``[object Object]`` or crashing.
 *
 * @param {Promise<import("axios").AxiosResponse>} promise
 * @returns {Promise<Record<string, unknown>>}
 */
function describeBackendError(payload) {
  if (!payload || typeof payload !== "object") return "Backend returned an error.";
  const details = payload.details;
  if (details && typeof details === "object") {
    const flat = Object.entries(details)
      .map(([field, messages]) => {
        const text = Array.isArray(messages)
          ? messages.join(", ")
          : String(messages);
        return field && field !== "_schema" ? `${field}: ${text}` : text;
      })
      .filter(Boolean)
      .join("; ");
    if (flat) return flat;
  }
  if (typeof payload.error === "string" && payload.error) return payload.error;
  if (typeof payload.message === "string" && payload.message) return payload.message;
  return "Backend returned an error.";
}

async function request(promise) {
  try {
    const { data } = await promise;
    if (data && data.ok === false) {
      throw new Error(describeBackendError(data));
    }
    return data;
  } catch (error) {
    if (error && error.response && error.response.data) {
      throw new Error(describeBackendError(error.response.data));
    }
    if (error && error.request && !error.response) {
      throw new Error(
        "AxiomTrace backend is unreachable. Start it with `npm run backend`."
      );
    }
    if (error instanceof Error) throw error;
    throw new Error(String(error));
  }
}

/**
 * GET /health - liveness probe used by the header indicator.
 *
 * @returns {Promise<Record<string, unknown>>}
 */
export function checkHealth() {
  return request(api.get("/health"));
}

/**
 * POST /projects/open - create or retrieve a workspace by name/path.
 * Payload is always normalised to the backend schema shape so missing
 * fields supply safe defaults instead of crashing the destructuring.
 *
 * @param {{ name: string, path: string }} [payload]
 * @returns {Promise<Record<string, unknown>>}
 */
export function openProject(payload = {}) {
  const name = typeof payload.name === "string" ? payload.name : "";
  const path = typeof payload.path === "string" ? payload.path : "";
  return request(api.post("/projects/open", { name, path }));
}

/**
 * GET /projects/:id/schema - load the persisted graph layout.
 *
 * @param {number|string} projectId
 * @returns {Promise<Record<string, unknown>>}
 */
export function loadGraph(projectId) {
  return request(api.get(`/projects/${projectId}/schema`));
}

/**
 * POST /projects/:id/schema - persist a graph snapshot.
 *
 * @param {number|string} projectId
 * @param {unknown[]} nodes
 * @param {unknown[]} edges
 * @returns {Promise<Record<string, unknown>>}
 */
export function saveGraph(projectId, nodes, edges) {
  return request(
    api.post(`/projects/${projectId}/schema`, {
      project_id: projectId,
      nodes,
      edges,
    })
  );
}

/**
 * POST /parse/code - compile source into a node graph.
 *
 * `file` is bound onto every returned node (`node.file` / `node.data.file`)
 * so the inspector, sandbox and transpiler always know the origin path.
 *
 * @param {string} code
 * @param {string|null} [language]
 * @param {number|string|null} [projectId]
 * @param {string|null} [file]
 * @returns {Promise<Record<string, unknown>>}
 */
export function parseCode(code, language = null, projectId = null, file = null) {
  return request(
    api.post("/parse/code", {
      code: typeof code === "string" ? code : "",
      language,
      file,
      project_id: projectId,
    })
  );
}

export function parseArchitecture(payload = {}) {
  return request(api.post("/parse/architecture", payload));
}

/**
 * POST /sandbox/execute - run a node in the isolated runtime.
 *
 * `label` / `file` / `module` identify the node so the backend can derive a
 * generated fallback snippet when `code` arrives empty - execution then
 * succeeds instead of failing with "Node source is empty".
 *
 * @param {{ nodeId?: string, node_id?: string, code: string, label?: string|null, file?: string|null, module?: string|null, fallback_code?: string|null, language?: string|null, inputPayload?: unknown, input_payload?: unknown, input_data?: unknown, project_id?: number|string|null, timeout?: number|null }} params
 * @returns {Promise<Record<string, unknown>>}
 */
export function executeNode({
  nodeId = "manual",
  node_id,
  code = "",
  label = null,
  file = null,
  module: moduleName = null,
  fallback_code = null,
  language = null,
  inputPayload,
  input_payload,
  input_data = {},
  project_id = null,
  timeout = null,
} = {}) {
  return request(
    api.post("/sandbox/execute", {
      node_id: node_id || nodeId,
      code: typeof code === "string" ? code : "",
      label,
      file,
      module: moduleName,
      fallback_code,
      language,
      input_payload: input_payload !== undefined ? input_payload : inputPayload,
      input_data,
      project_id,
      timeout,
    })
  );
}

/**
 * POST /sandbox/mutate - stress-test a node against the edge-case corpus.
 *
 * @param {{ nodeId?: string, node_id?: string, code: string, label?: string|null, file?: string|null, module?: string|null, fallback_code?: string|null, language?: string|null, iterations?: number, project_id?: number|string|null, timeout?: number|null }} params
 * @returns {Promise<Record<string, unknown>>}
 */
export function mutateNode({
  nodeId = "manual",
  node_id,
  code = "",
  label = null,
  file = null,
  module: moduleName = null,
  fallback_code = null,
  language = null,
  iterations = 3,
  project_id = null,
  timeout = null,
} = {}) {
  return request(
    api.post("/sandbox/mutate", {
      node_id: node_id || nodeId,
      code: typeof code === "string" ? code : "",
      label,
      file,
      module: moduleName,
      fallback_code,
      language,
      iterations,
      project_id,
      timeout,
    })
  );
}

/**
 * POST /ai/context - build a token-optimised Markdown dump from selection.
 *
 * @param {{ nodes?: unknown[], edges?: unknown[], projectName?: string|null, project_name?: string|null }} params
 * @returns {Promise<Record<string, unknown>>}
 */
export function generateContext({
  nodes = [],
  edges = [],
  projectName = null,
  project_name,
} = {}) {
  return request(
    api.post("/ai/context", {
      nodes,
      edges,
      project_name: project_name !== undefined ? project_name : projectName,
    })
  );
}

/**
 * POST /ai/edge-cases - detect unhandled runtime faults in a node.
 *
 * @param {{ code: string, apiKey?: string|null, api_key?: string|null }} params
 * @returns {Promise<Record<string, unknown>>}
 */
export function aiEdgeCases({ code = "", apiKey = null, api_key } = {}) {
  return request(
    api.post("/ai/edge-cases", {
      code,
      api_key: api_key !== undefined ? api_key : apiKey,
    })
  );
}

/**
 * Backwards-compatible alias kept for the desktop client's legacy "detect" call.
 *
 * @param {{ code: string, api_key?: string|null }} params
 * @returns {Promise<Record<string, unknown>>}
 */
export function aiDetect({ code, api_key = null }) {
  return aiEdgeCases({ code, api_key });
}

/**
 * POST /security/scan - static security scan for a node snippet or graph.
 *
 * @param {{ nodes?: Array<Record<string, unknown>>, code?: string, language?: string, node_id?: string }} params
 * @returns {Promise<Record<string, unknown>>} envelope with findings/summary/nodes
 */
export function securityScan({ nodes, code, language = "python", node_id = null } = {}) {
  if (code !== undefined && code !== null) {
    return request(api.post("/security/scan", { code, language, node_id }));
  }
  return request(api.post("/security/scan", { nodes: Array.isArray(nodes) ? nodes : [] }));
}

/**
 * POST /project/scan - scan a server-side project directory into a layered
 * architecture graph. Only available when the backend runs on the same
 * machine (desktop app).
 *
 * Payload always matches the backend scan schema: a directory uses
 * ``{path}``, an empty/malformed target falls back to ``{files: []}`` so
 * the backend processes the request successfully instead of rejecting it.
 *
 * @param {{ path?: string, files?: unknown[] }} [params]
 * @returns {Promise<Record<string, unknown>>}
 */
export function scanProjectPath(params = {}) {
  const path = typeof params?.path === "string" ? params.path.trim() : "";
  if (path) {
    return request(api.post("/project/scan", { path }));
  }
  return request(api.post("/project/scan", { files: [] }));
}

/**
 * POST /project/scan/upload - same graph from an uploaded file set
 * (browser folder picker with `webkitdirectory`).
 *
 * @param {File[]} files
 * @returns {Promise<Record<string, unknown>>}
 */
export function scanProjectUpload(files) {
  const form = new FormData();
  for (const file of Array.isArray(files) ? files : []) {
    // Browsers send the full relative path (webkitRelativePath) as filename.
    const relative = file.webkitRelativePath || file.name;
    form.append("files", file, relative);
  }
  return request(api.post("/project/scan/upload", form));
}

/**
 * POST /transpile - convert a node's source into a target language.
 *
 * `label` / `file` / `module` let the backend synthesise fallback source for
 * a node that arrives without text, so transpilation never hard-fails.
 *
 * @param {{ code: string, label?: string|null, file?: string|null, module?: string|null, fallback_code?: string|null, source_language?: string, target_language?: string, node_id?: string }} params
 * @returns {Promise<Record<string, unknown>>}
 */
export function transpileCode({
  code = "",
  label = null,
  file = null,
  module: moduleName = null,
  fallback_code = null,
  source_language = "python",
  target_language = "typescript",
  node_id = null,
} = {}) {
  return request(
    api.post("/transpile", {
      code: typeof code === "string" ? code : "",
      label,
      file,
      module: moduleName,
      fallback_code,
      source_language,
      target_language,
      node_id,
    })
  );
}

/**
 * POST /ai/config - validate and register the AI API key with the backend.
 *
 * Returns the backend's structured envelope without unwrapping it:
 *   { ok, status, message, verified }
 * Network-level failures raise an Error with a human-readable message.
 *
 * @param {{ api_key: string }} params
 * @returns {Promise<Record<string, unknown>>}
 */
export async function validateAiKey({ api_key } = {}) {
  try {
    const { data } = await api.post("/ai/config", { api_key });
    return data && typeof data === "object" ? data : { ok: true, message: "Key saved." };
  } catch (error) {
    if (error && error.response && error.response.data) {
      // Backend answered with a structured error (invalid key, etc.).
      return error.response.data;
    }
    if (error && error.request) {
      throw new Error(
        "AxiomTrace backend is unreachable. Start it with `npm run backend`."
      );
    }
    throw error instanceof Error ? error : new Error(String(error));
  }
}

/**
 * GET /logs - fetch recent execution records.
 *
 * @param {{ project_id?: number|string|null, node_id?: string|null, limit?: number }} [params]
 * @returns {Promise<Record<string, unknown>>}
 */
export function fetchLogs({ project_id = null, node_id = null, limit = 100 } = {}) {
  return request(
    api.get("/logs", {
      params: { project_id, node_id, limit },
    })
  );
}
