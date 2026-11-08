"use client";

/**
 * Node source binding helpers.
 *
 * A node can reach the canvas, the Inspector and the sandbox through several
 * shapes: parsed AST nodes carry `data.code`, scanned project nodes carry
 * `data.code` *and* `data.source` plus `file` / `module`, older persisted
 * graphs may only have a top-level `code`, and Auto-Architect nodes may have
 * nothing at all. These helpers normalise every shape into executable source
 * so "Run", "Mutate" and "Transpile" never fail with
 * `ValueError: Node source is empty - nothing to execute`.
 */

const FALLBACK_NOTE = [
  "# AxiomTrace generated fallback - no source text was bound to this node.",
  "# The snippet below mirrors the node's identity so execution, mutation",
  "# stress testing and transpilation all keep working end to end.",
].join("\n");

/**
 * Canonical safe stub mandated by the repair directive. Mirrors
 * `CANONICAL_STUB` in `backend/services/node_source.py` byte for byte so the
 * client and the server recognise the same fallback header.
 */
export const CANONICAL_STUB = "# No source provided\ndef fallback_node(): return True\n";

/** Languages the Python sandbox may execute directly (mirror of backend). */
const PYTHON_LANGUAGES = new Set(["", "python", "text", "markdown", "unknown"]);

/** Common spellings -> canonical language id (mirror of backend aliases). */
const LANGUAGE_ALIASES = {
  py: "python",
  python3: "python",
  python2: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "javascript",
  node: "javascript",
  ts: "typescript",
  tsx: "typescript",
  cs: "csharp",
  "c#": "csharp",
  "c++": "cpp",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  golang: "go",
  rb: "ruby",
  kt: "kotlin",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  yml: "yaml",
  md: "markdown",
  plaintext: "text",
  "": "",
};

/** File extension -> canonical language id (mirror of backend table). */
const LANGUAGE_BY_EXTENSION = {
  ".py": "python",
  ".pyw": "python",
  ".pyi": "python",
  ".js": "javascript",
  ".jsx": "javascript",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".java": "java",
  ".go": "go",
  ".rs": "rust",
  ".cs": "csharp",
  ".c": "c",
  ".h": "c",
  ".cc": "cpp",
  ".cpp": "cpp",
  ".cxx": "cpp",
  ".hpp": "cpp",
  ".php": "php",
  ".rb": "ruby",
  ".kt": "kotlin",
  ".swift": "swift",
  ".scala": "scala",
  ".sh": "shell",
  ".bash": "shell",
  ".sql": "sql",
  ".html": "html",
  ".css": "css",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".json": "json",
  ".md": "markdown",
  ".txt": "text",
  ".vue": "html",
  ".svelte": "html",
  ".astro": "html",
};

/**
 * Canonicalise a language identifier (`"PY"` -> `"python"`), mirroring
 * `normalise_language` on the backend. Returns `""` for missing values.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function normaliseLanguage(value) {
  if (value === undefined || value === null) return "";
  let text;
  try {
    text = String(value).trim().toLowerCase();
  } catch {
    return "";
  }
  if (!text) return "";
  return LANGUAGE_ALIASES[text] ?? text;
}

/**
 * Derive a canonical language id from a file path, mirroring
 * `language_for_path` on the backend.
 *
 * @param {unknown} filePath
 * @returns {string}
 */
export function languageFromPath(filePath) {
  if (filePath === undefined || filePath === null) return "";
  let text;
  try {
    text = String(filePath).replace(/\\/g, "/").trim().toLowerCase();
  } catch {
    return "";
  }
  if (!text) return "";
  const dot = text.lastIndexOf(".");
  const slash = text.lastIndexOf("/");
  if (dot <= slash) return "";
  return LANGUAGE_BY_EXTENSION[text.slice(dot)] ?? "";
}

/**
 * True when the Python sandbox may execute this language directly. Unset or
 * unknown declarations default to Python so legacy payloads keep working.
 *
 * @param {unknown} language
 * @returns {boolean}
 */
export function isPythonLanguage(language) {
  return PYTHON_LANGUAGES.has(normaliseLanguage(language));
}

const MAX_FIELD = 160;

function clean(value, limit = MAX_FIELD) {
  if (value === undefined || value === null) return "";
  let text;
  try {
    text = String(value);
  } catch {
    return "";
  }
  text = text.replace(/\x00/g, "").replace(/[\r\n]+/g, " ").trim();
  if (text.length > limit) text = `${text.slice(0, Math.max(0, limit - 1))}...`;
  return text;
}

/** Render a value as a Python string literal (single quotes, escaped). */
function pyString(value) {
  const escaped = String(value)
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/\t/g, "\\t");
  return `'${escaped}'`;
}

/**
 * Derive a dotted module name from a file path.
 * `backend/services/user_service.py` -> `backend.services.user_service`.
 *
 * @param {unknown} filePath
 * @param {unknown} [label]
 * @returns {string}
 */
export function moduleFromPath(filePath, label) {
  const raw = clean(filePath, 400) || clean(label, 400);
  if (!raw) return "";
  const normalised = raw.replace(/\\/g, "/").replace(/^\/+/, "");
  const parts = normalised.split("/").filter((part) => part && part !== ".");
  if (parts.length === 0) return "";
  let last = parts[parts.length - 1];
  if (last.includes(".")) last = last.slice(0, last.lastIndexOf("."));
  const head = parts.slice(0, -1);
  const tail =
    last === "__init__" || last === "index" || last === "mod"
      ? []
      : [last];
  return [...head, ...tail].filter(Boolean).join(".");
}

/**
 * Generate the runnable Python stand-in used when a node has no bound text.
 *
 * Mirrors `backend/services/node_source.build_fallback_source` so the server
 * and the client synthesise identical snippets for the same node.
 *
 * @param {Record<string, unknown>} [data] node data (label / file / module)
 * @returns {string}
 */
export function fallbackSourceFor(data = {}) {
  const source = data && typeof data === "object" ? data : {};
  const label =
    clean(source.label) || clean(source.file) || clean(source.path) || "unbound-node";
  const file = clean(source.file) || clean(source.path);
  const moduleName = clean(source.module) || moduleFromPath(file, label) || label;
  const language =
    normaliseLanguage(source.language) || normaliseLanguage(languageFromPath(file));

  return [
    CANONICAL_STUB.trimEnd(),
    FALLBACK_NOTE,
    `NODE_LABEL = ${pyString(label)}`,
    `NODE_FILE = ${pyString(file)}`,
    `NODE_MODULE = ${pyString(moduleName)}`,
    `NODE_LANGUAGE = ${pyString(language)}`,
    "",
    "def main(payload=None):",
    '    """Generated entry point for a node without bound source text."""',
    "    return {",
    '        "node": NODE_LABEL,',
    '        "file": NODE_FILE,',
    '        "module": NODE_MODULE,',
    '        "language": NODE_LANGUAGE,',
    '        "payload": payload,',
    '        "fallback": True,',
    "    }",
    "",
  ].join("\n");
}

/**
 * Resolve a node's executable source plus where it came from.
 *
 * @param {Record<string, unknown>|null|undefined} node React Flow node (or raw data)
 * @returns {{ code: string, origin: "bound"|"generated", label: string, file: string, module: string, language: string }}
 */
export function resolveNodeSource(node) {
  const root = node && typeof node === "object" ? node : {};
  const data =
    root.data && typeof root.data === "object" ? root.data : root;

  const label = clean(data.label) || clean(root.label);
  const file = clean(data.file) || clean(data.path) || clean(root.file) || clean(root.path);
  const moduleName =
    clean(data.module) || clean(root.module) || moduleFromPath(file, label);
  const language =
    normaliseLanguage(data.language) ||
    normaliseLanguage(root.language) ||
    normaliseLanguage(languageFromPath(file)) ||
    "python";

  const candidates = [
    data.code,
    root.code,
    data.source,
    root.source,
    data.code_snippet,
    data.body,
    data.content,
  ];
  const bound = candidates.find(
    (value) => typeof value === "string" && value.trim().length > 0
  );

  if (bound) {
    return {
      code: bound,
      origin: "bound",
      label,
      file,
      module: moduleName,
      language,
    };
  }

  return {
    code: fallbackSourceFor({ label, file, module: moduleName, language }),
    origin: "generated",
    label,
    file,
    module: moduleName,
    language,
  };
}

/**
 * Return the same node with `data.code` and `data.source` both populated
 * whenever *either* of them already holds real text. Nodes without any source
 * are returned untouched - execution resolves their fallback on demand.
 *
 * @param {Record<string, unknown>} node
 * @returns {Record<string, unknown>} the same reference when nothing changed
 */
export function bindNodeSource(node) {
  if (!node || typeof node !== "object") return node;
  const data = node.data && typeof node.data === "object" ? node.data : null;
  if (!data) return node;

  const bound = [data.code, node.code, data.source, node.source].find(
    (value) => typeof value === "string" && value.trim().length > 0
  );
  if (!bound) return node;

  const patch = {};
  if (typeof data.code !== "string" || !data.code) patch.code = bound;
  if (typeof data.source !== "string" || !data.source) patch.source = bound;
  if (Object.keys(patch).length === 0) return node;

  return { ...node, data: { ...data, ...patch } };
}

export default resolveNodeSource;
