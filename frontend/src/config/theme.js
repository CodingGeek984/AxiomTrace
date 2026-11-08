/**
 * AxiomTrace design tokens.
 *
 * Single source of truth for the Revolut-inspired palette. Tailwind picks
 * the same values up through `tailwind.config.js` (loaded by the `@config`
 * directive in `globals.css`); components that render inline SVG (edges,
 * handles, badges, meters) import them from here so JS and CSS cannot drift.
 */

/** Canonical Revolut monochrome contract, mirrored in tailwind.config.js. */
export const REVOLUT = {
  bgDark: "#0B0C0E",
  surfaceDark: "#14161B",
  borderDark: "#262933",
  bgLight: "#FFFFFF",
  cardLight: "#F4F5F7",
  textPrimary: "#0B0C0E",
  textMuted: "#5E6473",
  accentGreen: "#30D158",
  accentBlue: "#007AFF",
};

export const COLORS = {
  app: REVOLUT.bgLight, // Crisp white canvas
  card: REVOLUT.bgLight, // White node / card surface
  cardHover: REVOLUT.cardLight, // Soft neutral raised surface
  panel: REVOLUT.cardLight, // Sidebar & inspector drawer
  input: REVOLUT.bgLight, // Input / editor field
  border: "#E3E5E9", // Subtle 1px light border
  borderActive: "#D0D2D8", // Focused element border
  accent: "#007AFF", // Minimal blue active indicator
  accentGlow: "rgba(0, 122, 255, 0.15)",
  purple: "#5856D6",
  success: "#30D158", // Status LED
  successGlow: "rgba(48, 209, 88, 0.25)",
  error: "#FF3B30", // Alert red
  errorGlow: "rgba(255, 59, 48, 0.25)",
  warning: "#FF9500", // Warning orange
  warningGlow: "rgba(255, 149, 0, 0.25)",
  processing: "#007AFF", // Executing
  textPrimary: REVOLUT.textPrimary,
  textSecondary: REVOLUT.textMuted,
  textDimmed: "#8B909C",
};

/** Light landing / marketing palette. */
export const LIGHT_COLORS = {
  app: "#FFFFFF",
  card: "#F4F5F7",
  cardHover: "#EDEFF3",
  border: "#E6E8EC",
  borderActive: "#D5D8DF",
  textPrimary: "#0B0C0E",
  textSecondary: "#5E6473",
  textDimmed: "#8B909C",
  accent: "#007AFF",
  success: "#30D158",
  error: "#FF3B30",
  warning: "#FF9500",
};

/** Shared gradient recipes for wordmarks, rails and hero surfaces. */
export const GRADIENTS = {
  brand: "linear-gradient(140deg, #30D158 0%, #007AFF 58%, #5856D6 100%)",
  nodeRail: (color) => `linear-gradient(90deg, ${color}, transparent 88%)`,
  ambient:
    "radial-gradient(60rem 32rem at 10% -8%, rgba(0, 122, 255, 0.09), transparent 62%), radial-gradient(48rem 30rem at 92% 104%, rgba(48, 209, 88, 0.06), transparent 60%)",
};

/**
 * Canvas metrics shared by the dagre layout and the node renderer.
 *
 * Enterprise spacing contract (UI/UX MASTER DIRECTIVE): rankSep 160 between
 * ranks/tiers, nodeSep 100 between sibling nodes, strict top-to-bottom flow
 * so bands never overlap and fitView never has to crush the graph to fit.
 */
export const LAYOUT = {
  nodeWidth: 300,
  nodeHeight: 150,
  rankDirection: "TB",
  nodeSeparation: 100,
  rankSeparation: 160,
  /**
   * Hard cap on cards per row inside a tier band. Mirrors
   * `MAX_TIER_COLUMNS` in backend/services/project_scanner.py and
   * services/ast_service.py so SCAN PROJECT and PARSE CODE emit the same
   * geometry: dense tiers wrap into additional rows instead of collapsing
   * into one flat horizontal row of cards.
   */
  maxColumns: 6,
  marginX: 48,
  marginY: 48,
};

/**
 * Canonical auto-fit viewport. ``reactFlowInstance.fitView({ duration: 800,
 * padding: 0.25 })`` centers the swimlane blueprint immediately after nodes
 * and edges load; ``maxZoom: 0.85`` keeps the graph at a readable ~85%
 * instead of a 15% crush while preserving the requested 0.25 padding.
 */
export const FIT_VIEW = { duration: 800, padding: 0.25, maxZoom: 0.85 };

/**
 * Semantic node families produced by the AST parser.
 *
 * The first five are architectural roles; the remaining four are the legacy
 * control-flow types used for branches, loops and simple statements. Keeping
 * them together means a parsed graph never renders an unknown badge.
 */
export const SEMANTIC_TYPES = [
  "entrypoint",
  "service",
  "database",
  "utility",
  "error_handler",
  "function",
  "condition",
  "loop",
  "action",
  "presentation",
  "api",
  "integration",
];

export const DEFAULT_TYPE = "action";

/** Per-type presentation metadata (label, accent colour, short badge). */
export const TYPE_META = {
  entrypoint: {
    label: "Entry Point",
    color: "#0B0C0E",
    badge: "entry",
    group: "entrypoint",
    description: "Public entry point or request handler.",
  },
  service: {
    label: "Service",
    color: "#007AFF",
    badge: "svc",
    group: "service",
    description: "Business logic and orchestration.",
  },
  database: {
    label: "Data Layer",
    color: "#5856D6",
    badge: "db",
    group: "data",
    description: "Models, schemas and persistence.",
  },
  utility: {
    label: "Utility",
    color: "#5E6473",
    badge: "util",
    group: "utility",
    description: "Supporting helper or value preparation.",
  },
  error_handler: {
    label: "Error Handling",
    color: "#FF3B30",
    badge: "err",
    group: "error",
    description: "Guard, raised exception or recovery path.",
  },
  function: {
    label: "Function",
    color: "#0B0C0E",
    badge: "fn",
    group: "service",
    description: "Callable code block.",
  },
  condition: {
    label: "Condition",
    color: "#FF9500",
    badge: "if",
    group: "control",
    description: "Branching control-flow decision.",
  },
  loop: {
    label: "Loop",
    color: "#30D158",
    badge: "loop",
    group: "control",
    description: "Repeated control-flow block.",
  },
  action: {
    label: "Action",
    color: "#5E6473",
    badge: "do",
    group: "utility",
    description: "Simple statement or side effect.",
  },
  presentation: {
    label: "UI Layer",
    color: "#0B0C0E",
    badge: "ui",
    group: "presentation",
    description: "Views, components and frontend pages.",
  },
  api: {
    label: "API & Routing",
    color: "#30D158",
    badge: "api",
    group: "api",
    description: "Controllers, routers, endpoints and middleware.",
  },
  integration: {
    label: "External I/O",
    color: "#FF9500",
    badge: "i/o",
    group: "integration",
    description: "Third-party SDKs and outbound connectors.",
  },
};

/**
 * Ordered architectural domains returned by the parser (`groups`).
 *
 * Every domain belongs to exactly one of the four enterprise tiers rendered
 * as swimlane bands on the canvas. The granular domain is kept for badges
 * and legend detail; the tier drives layout order and band grouping.
 */
export const GROUP_META = {
  presentation: {
    label: "Presentation / UI",
    short: "UI",
    color: "#0B0C0E",
    order: 0,
    tier: 1,
  },
  api: {
    label: "API & Routing",
    short: "API",
    color: "#30D158",
    order: 1,
    tier: 2,
  },
  entrypoint: {
    label: "Entry Point / Controllers",
    short: "Entry",
    color: "#0B0C0E",
    order: 2,
    tier: 1,
  },
  service: {
    label: "Business Logic / Services",
    short: "Service",
    color: "#007AFF",
    order: 3,
    tier: 3,
  },
  data: {
    label: "Data & Storage",
    short: "Data",
    color: "#5856D6",
    order: 4,
    tier: 4,
  },
  integration: {
    label: "External Integrations",
    short: "I/O",
    color: "#FF9500",
    order: 5,
    tier: 4,
  },
  utility: {
    label: "Utilities / Support",
    short: "Utility",
    color: "#5E6473",
    order: 6,
    tier: 3,
  },
  error: {
    label: "Error Handling",
    short: "Errors",
    color: "#FF3B30",
    order: 7,
    tier: 3,
  },
  control: {
    label: "Control Flow",
    short: "Flow",
    color: "#FF9500",
    order: 8,
    tier: 3,
  },
};

/**
 * Canonical four-tier enterprise architecture.
 *
 * Tier 1: client/presentation (UI, views, entry points)
 * Tier 2: API & gateway (controllers, routes, endpoints, middleware)
 * Tier 3: business logic (domain services, handlers, utilities)
 * Tier 4: data & storage (models, schemas, databases, connectors)
 */
export const TIERS = [
  {
    id: 1,
    short: "Client",
    label: "Client / Presentation Layer",
    description: "UI components, views, pages and entry points.",
    color: "#0B0C0E",
  },
  {
    id: 2,
    short: "API",
    label: "API & Gateway Layer",
    description: "Controllers, routes, endpoints and middleware.",
    color: "#30D158",
  },
  {
    id: 3,
    short: "Logic",
    label: "Business Logic / Service Layer",
    description: "Core domain services, handlers, utilities and control flow.",
    color: "#007AFF",
  },
  {
    id: 4,
    short: "Data",
    label: "Data & Storage Layer",
    description: "Models, schemas, databases and external connectors.",
    color: "#5856D6",
  },
];

export const TIER_BY_ID = Object.fromEntries(TIERS.map((tier) => [tier.id, tier]));

/** Resolve tier presentation metadata with a business-logic fallback. */
export function tierMeta(tier) {
  return TIER_BY_ID[tier] || TIER_BY_ID[3];
}

/** Derive the enterprise tier for a node group/domain. */
export function tierForGroup(group) {
  return GROUP_META[group]?.tier || 3;
}

/** Runtime status vocabulary used by node LEDs and the inspector. */
export const STATUS_META = {
  idle: { label: "idle", color: COLORS.textDimmed, tone: "text-text-secondary" },
  running: { label: "running", color: COLORS.processing, tone: "text-status-blue" },
  success: { label: "ok", color: COLORS.success, tone: "text-status-green" },
  error: { label: "error", color: COLORS.error, tone: "text-status-red" },
  timeout: { label: "timeout", color: COLORS.warning, tone: "text-status-orange" },
};

/** Radial glow painted behind a status LED. */
export const STATUS_GLOW = {
  idle: "transparent",
  running: COLORS.accentGlow,
  success: COLORS.successGlow,
  error: COLORS.errorGlow,
  timeout: COLORS.warningGlow,
};

/**
 * In-flight operation vocabulary.
 *
 * Mirrors the `busy` map in the store: one key per outstanding request so a
 * node under mutation shows an amber "mutating" LED while a plain execution
 * keeps the blue spinner.
 */
export const BUSY_META = {
  exec: { label: "executing", color: COLORS.processing, tone: "text-status-blue" },
  mutate: { label: "mutating", color: COLORS.warning, tone: "text-status-orange" },
  detect: { label: "scanning", color: COLORS.accent, tone: "text-status-blue" },
};

/** Radial glow painted behind an in-flight operation LED. */
export const BUSY_GLOW = {
  exec: COLORS.accentGlow,
  mutate: COLORS.warningGlow,
  detect: COLORS.accentGlow,
};

/** Severity ordering + colour used by AI edge-case findings. */
export const SEVERITY_META = {
  high: { order: 0, tone: "text-status-red", color: COLORS.error },
  medium: { order: 1, tone: "text-status-orange", color: COLORS.warning },
  low: { order: 2, tone: "text-text-secondary", color: COLORS.textSecondary },
};

/** Resolve node presentation metadata with an action fallback. */
export function typeMeta(type) {
  return TYPE_META[type] || TYPE_META[DEFAULT_TYPE];
}

/** Resolve architectural domain metadata with a utility fallback. */
export function groupMeta(group) {
  return GROUP_META[group] || GROUP_META.utility;
}

/** Resolve status presentation metadata with an idle fallback. */
export function statusMeta(status) {
  return STATUS_META[status] || STATUS_META.idle;
}

/** Resolve in-flight operation metadata with an execution fallback. */
export function busyMeta(kind) {
  return BUSY_META[kind] || BUSY_META.exec;
}

/** Resolve severity presentation metadata with a low fallback. */
export function severityMeta(severity) {
  return SEVERITY_META[severity] || SEVERITY_META.low;
}

/** React Flow edge styling derived from the palette. */
export const EDGE_STYLE = {
  stroke: COLORS.border,
  strokeWidth: 1.6,
};

export const EDGE_LABEL_STYLE = {
  fill: COLORS.textSecondary,
  fontSize: 10,
  fontWeight: 600,
};

export const EDGE_LABEL_BG_STYLE = {
  fill: COLORS.app,
};
