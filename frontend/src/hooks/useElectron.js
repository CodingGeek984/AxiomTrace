"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

/**
 * Native Electron bridge wrapper.
 *
 * Exposes the `window.electronAPI` preload surface (see `electron/preload.js`)
 * with stable fallbacks so the same components render in the browser during
 * `npm run dev`, where no bridge exists.
 *
 * @returns {{
 *   isElectron: boolean,
 *   ready: boolean,
 *   appInfo: Record<string, unknown>|null,
 *   selectFile: () => Promise<string|null>,
 *   selectFolder: () => Promise<string|null>,
 *   readFile: (path: string) => Promise<{ ok: boolean, path?: string, content?: string, error?: string }>,
 *   writeFile: (path: string, content: string) => Promise<{ ok: boolean, path?: string, bytes?: number, error?: string }>,
 * }}
 */
export function useElectron() {
  const [ready, setReady] = useState(false);
  const [appInfo, setAppInfo] = useState(null);

  const bridge = useMemo(() => {
    if (typeof window === "undefined") return null;
    return window.electronAPI || null;
  }, []);

  const isElectron = Boolean(bridge && (bridge.isElectron || bridge.selectFile));

  useEffect(() => {
    let cancelled = false;

    async function loadInfo() {
      if (!bridge || typeof bridge.getAppInfo !== "function") {
        if (!cancelled) setReady(true);
        return;
      }
      try {
        const info = await bridge.getAppInfo();
        if (!cancelled) setAppInfo(info || null);
      } catch {
        if (!cancelled) setAppInfo(null);
      } finally {
        if (!cancelled) setReady(true);
      }
    }

    loadInfo();
    return () => {
      cancelled = true;
    };
  }, [bridge]);

  const selectFile = useCallback(async () => {
    if (!bridge || typeof bridge.selectFile !== "function") return null;
    try {
      return await bridge.selectFile();
    } catch {
      return null;
    }
  }, [bridge]);

  const selectFolder = useCallback(async () => {
    if (!bridge || typeof bridge.selectFolder !== "function") return null;
    try {
      return await bridge.selectFolder();
    } catch {
      return null;
    }
  }, [bridge]);

  const readFile = useCallback(
    async (filePath) => {
      if (!filePath) return { ok: false, error: "A file path is required." };
      if (!bridge || typeof bridge.readFile !== "function") {
        return {
          ok: false,
          path: filePath,
          error: "Native file access requires the Electron desktop shell.",
        };
      }
      try {
        const result = await bridge.readFile(filePath);
        return result || { ok: false, path: filePath, error: "Empty bridge result." };
      } catch (error) {
        return {
          ok: false,
          path: filePath,
          error: error instanceof Error ? error.message : String(error),
        };
      }
    },
    [bridge]
  );

  const writeFile = useCallback(
    async (filePath, content) => {
      if (!filePath) return { ok: false, error: "A file path is required." };
      if (typeof content !== "string") {
        return { ok: false, path: filePath, error: "Content must be a string." };
      }
      if (!bridge || typeof bridge.writeFile !== "function") {
        return {
          ok: false,
          path: filePath,
          error: "Native file access requires the Electron desktop shell.",
        };
      }
      try {
        const result = await bridge.writeFile(filePath, content);
        return result || { ok: false, path: filePath, error: "Empty bridge result." };
      } catch (error) {
        return {
          ok: false,
          path: filePath,
          error: error instanceof Error ? error.message : String(error),
        };
      }
    },
    [bridge]
  );

  return { isElectron, ready, appInfo, selectFile, selectFolder, readFile, writeFile };
}

export default useElectron;
