"use client";

export { cn } from "@/lib/cn";

/**
 * Coerce a value into a finite number, falling back when it cannot.
 *
 * @param {unknown} value
 * @param {number} fallback
 * @returns {number}
 */
export function toNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

/**
 * Format a duration expressed in milliseconds for compact UI labels.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function formatMs(value) {
  const ms = toNumber(value, 0);
  if (ms === 0) return "0 ms";
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

/**
 * Turn a crash rate (0..1) into a percentage string.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function formatPercent(value) {
  const rate = toNumber(value, 0);
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * Extract the final path segment from a POSIX or Windows path.
 *
 * @param {string|null|undefined} filePath
 * @returns {string|null}
 */
export function fileLabelFromPath(filePath) {
  if (!filePath) return null;
  const parts = String(filePath).split(/[\\/]/);
  return parts[parts.length - 1] || String(filePath);
}

/**
 * Parse a JSON textarea safely, returning a discriminated result object.
 *
 * @param {string} text
 * @returns {{ ok: true, value: unknown } | { ok: false, error: string }}
 */
export function parseJsonInput(text) {
  const raw = typeof text === "string" ? text.trim() : "";
  if (raw === "") return { ok: true, value: {} };
  try {
    return { ok: true, value: JSON.parse(raw) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

/**
 * Truncate a string to a maximum length, appending an ellipsis marker.
 *
 * @param {unknown} value
 * @param {number} [max]
 * @returns {string}
 */
export function truncate(value, max = 160) {
  const text = String(value ?? "");
  if (text.length <= max) return text;
  return `${text.slice(0, max)}…`;
}
