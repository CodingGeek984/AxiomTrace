"use client";

/**
 * Blob download helpers.
 *
 * Every export path funnels through {@link downloadBlob} so the temporary
 * object URL is always revoked - even when the anchor click throws. Leaked
 * object URLs pin their blob in memory for the lifetime of the document,
 * which is exactly what the desktop memory budget cannot afford.
 */

/**
 * Trigger a browser download for a blob and release the object URL.
 *
 * @param {Blob} blob
 * @param {string} filename
 * @returns {void}
 */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Download a text payload (Markdown, JSON, plain text).
 *
 * @param {string} text
 * @param {string} filename
 * @param {string} [mimeType]
 * @returns {void}
 */
export function downloadText(text, filename, mimeType = "text/plain;charset=utf-8") {
  downloadBlob(new Blob([text], { type: mimeType }), filename);
}

/**
 * Download a JSON payload.
 *
 * @param {unknown} value
 * @param {string} filename
 * @returns {void}
 */
export function downloadJson(value, filename) {
  downloadText(
    JSON.stringify(value, null, 2),
    filename,
    "application/json;charset=utf-8"
  );
}

/**
 * Build a filesystem-safe timestamp suitable for asset file names.
 *
 * @param {Date} [date]
 * @returns {string}
 */
export function fileStamp(date = new Date()) {
  return date.toISOString().replace(/[:.]/g, "-").slice(0, 19);
}
