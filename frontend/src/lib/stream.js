"use client";

/**
 * Transport-agnostic trace stream helper.
 *
 * The desktop client can optionally receive live sandbox traces over SSE
 * (`EventSource`) or a WebSocket. Not every backend deployment exposes
 * either endpoint, so the helper degrades to an inert stub and - crucially -
 * always hands back an idempotent `close()` that releases the socket, drops
 * the listeners and lets the garbage collector reclaim the buffers.
 */

/**
 * @typedef {{
 *   url: string,
 *   onMessage?: (payload: unknown) => void,
 *   onOpen?: () => void,
 *   onError?: (error: unknown) => void,
 *   onClose?: () => void,
 * }} TraceStreamOptions
 */

/**
 * @typedef {{
 *   close: () => void,
 *   mode: "sse" | "ws" | "idle",
 * }} TraceStream
 */

const NOOP = () => {};

/**
 * Parse a raw SSE payload into a JavaScript value when possible.
 *
 * @param {string} raw
 * @returns {unknown}
 */
function parsePayload(raw) {
  if (typeof raw !== "string" || raw.length === 0) return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

/**
 * Open a trace stream against the given URL.
 *
 * @param {TraceStreamOptions} options
 * @returns {TraceStream}
 */
export function createTraceStream({
  url,
  onMessage,
  onOpen,
  onError,
  onClose,
} = {}) {
  const emitMessage = typeof onMessage === "function" ? onMessage : NOOP;
  const emitOpen = typeof onOpen === "function" ? onOpen : NOOP;
  const emitError = typeof onError === "function" ? onError : NOOP;
  const emitClose = typeof onClose === "function" ? onClose : NOOP;

  if (!url || typeof window === "undefined") {
    return { close: NOOP, mode: "idle" };
  }

  let transport = null;
  let mode = "idle";
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;

    if (transport) {
      try {
        if (typeof transport.close === "function") transport.close();
        transport.onmessage = null;
        transport.onerror = null;
        transport.onopen = null;
        transport.onclose = null;
      } catch {
        // The transport may already be torn down - nothing to release.
      }
      transport = null;
    }

    emitClose();
  };

  // --- Server-Sent Events -----------------------------------------
  if (typeof window.EventSource === "function") {
    try {
      const source = new window.EventSource(url, { withCredentials: false });
      transport = source;
      mode = "sse";

      source.onopen = () => emitOpen();
      source.onmessage = (event) => emitMessage(parsePayload(event.data));
      source.onerror = (event) => {
        // The browser retries automatically; surface it without throwing.
        emitError(event);
      };

      return { close, mode };
    } catch (error) {
      emitError(error);
    }
  }

  // --- WebSocket fallback -----------------------------------------
  if (typeof window.WebSocket === "function") {
    try {
      const socket = new window.WebSocket(url);
      transport = socket;
      mode = "ws";

      socket.onopen = () => emitOpen();
      socket.onmessage = (event) => emitMessage(parsePayload(event.data));
      socket.onerror = (error) => emitError(error);
      socket.onclose = () => {
        if (!closed) emitClose();
      };

      return { close, mode };
    } catch (error) {
      emitError(error);
    }
  }

  emitError(new Error("No SSE or WebSocket transport available."));
  return { close: NOOP, mode: "idle" };
}

export default createTraceStream;
