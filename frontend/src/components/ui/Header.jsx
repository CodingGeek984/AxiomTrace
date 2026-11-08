"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  CircleDot,
  Code2,
  FileCode2,
  FolderSearch,
  LayoutGrid,
  Loader2,
  MemoryStick,
  RefreshCw,
  Settings,
  Workflow,
  Zap,
  Download,
} from "lucide-react";

import AISettingsModal from "@/components/modals/AISettingsModal";
import { cn } from "@/lib/cn";
import { fileLabelFromPath } from "@/lib/graph";
import { checkHealth, scanProjectPath, scanProjectUpload } from "@/lib/api";
import { FOCUS_RING } from "@/components/ui/styles";
import { useGraphStore } from "@/store/useGraphStore";

const HEALTH_INTERVAL_MS = 10000;

export default function Header() {
  const project = useGraphStore((state) => state.project);
  const sourceName = useGraphStore((state) => state.sourceName);
  const nodes = useGraphStore((state) =>
    Array.isArray(state.nodes) ? state.nodes : []
  );
  const logCount = useGraphStore((state) =>
    Array.isArray(state.executionLogs) ? state.executionLogs.length : 0
  );
  const clearMemoryStore = useGraphStore((state) => state.clearMemoryStore);
  const flushMemoryStore = useGraphStore((state) => state.flushMemoryStore);
  const notify = useGraphStore((state) => state.notify);
  const autoArchitect =
    useGraphStore((state) => state.autoArchitect || state.isAutoArchitectActive);
  const architectBusy = useGraphStore((state) => Boolean(state.architectBusy));
  const generateAutoArchitect = useGraphStore(
    (state) => state.generateAutoArchitect
  );
  const setAutoArchitect = useGraphStore((state) => state.setAutoArchitect);
  const autoLayout = useGraphStore((state) => state.autoLayout);
  const replaceGraph = useGraphStore((state) => state.replaceGraph);
  const setInspectorTab = useGraphStore((state) => state.setInspectorTab);
  const openExport = useGraphStore((state) => state.openExport);
  const selectedIds = useGraphStore((state) =>
    Array.isArray(state.selectedIds) ? state.selectedIds : []
  );
  const hasSelection = selectedIds.length > 0;

  const parseSource = useGraphStore((state) => state.parseSource);
  const parseStatus = useGraphStore((state) => state.parseStatus);

  const [picking, setPicking] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [flushed, setFlushed] = useState(false);
  const [scanning, setScanning] = useState(false);
  const folderInputRef = useRef(null);
  const parsing = parseStatus === "parsing" || picking;

  useEffect(() => {
    if (!flushed) return undefined;
    const timer = setTimeout(() => setFlushed(false), 1600);
    return () => clearTimeout(timer);
  }, [flushed]);

  const applyProjectScan = useCallback(
    async (response, sourceName) => {
      if (!response || !Array.isArray(response.nodes)) {
        throw new Error("Project scan returned an unexpected response.");
      }
      const nodes = response.nodes;
      const groups = Array.isArray(response.groups) ? response.groups : [];
      const tiers = Array.isArray(response.tiers) ? response.tiers : [];
      replaceGraph(
        {
          nodes,
          edges: Array.isArray(response.edges) ? response.edges : [],
          groups,
          tiers,
          language: response.language || "mixed",
          stats: response.stats || {},
          errors: Array.isArray(response.errors) ? response.errors : [],
          partial: Boolean(response.partial),
        },
        { relayout: true, sourceName }
      );
      const skipped =
        response.stats && Array.isArray(response.errors)
          ? response.errors.length
          : 0;
      notify(
        `Scanned ${sourceName}: ${nodes.length} file(s) in ${groups.length} layer(s)` +
          (skipped > 0 ? ` · ${skipped} skipped` : ""),
        "ok"
      );
    },
    [notify, replaceGraph]
  );

  const handleScanProject = useCallback(async () => {
    if (scanning) return;
    const bridge = typeof window !== "undefined" ? window.electronAPI : undefined;
    // Browser build: fall back to the directory-upload input
    if (!(bridge && typeof bridge.selectFolder === "function")) {
      folderInputRef.current?.click();
      return;
    }
    setScanning(true);
    try {
      const folder = await bridge.selectFolder();
      if (!folder) return;
      const response = await scanProjectPath({ path: folder });
      const name =
        String(folder).split(/[\\/]/).filter(Boolean).pop() || "project";
      await applyProjectScan(response, name);
    } catch (error) {
      notify(error instanceof Error ? error.message : "Project scan failed.", "error");
    } finally {
      setScanning(false);
    }
  }, [applyProjectScan, notify, scanning]);

  const handleFolderUpload = useCallback(
    async (event) => {
      const input = event?.target;
      const files = input && input.files ? Array.from(input.files) : [];
      if (input) input.value = "";
      if (files.length === 0) return;
      setScanning(true);
      try {
        const root =
          files[0]?.webkitRelativePath?.split("/")[0] || "uploaded-project";
        const response = await scanProjectUpload(files);
        await applyProjectScan(response, root);
      } catch (error) {
        notify(
          error instanceof Error ? error.message : "Project scan failed.",
          "error"
        );
      } finally {
        setScanning(false);
      }
    },
    [applyProjectScan, notify]
  );

  const handleParse = useCallback(async () => {
    if (picking || parseStatus === "parsing") return;
    setPicking(true);
    try {
      let source = null;
      let fileName = null;

      const bridge =
        typeof window !== "undefined" ? window.electronAPI : undefined;

      if (bridge && bridge.selectFile && bridge.readFile) {
        const filePath = await bridge.selectFile();
        if (!filePath) return;
        const result = await bridge.readFile(filePath);
        if (!result || result.ok === false) {
          notify(result ? result.error : "Could not read file", "error");
          return;
        }
        source = result.content;
        fileName = fileLabelFromPath(result.path || filePath);
      } else {
        const pasted = window.prompt("Paste source code to parse into a graph:");
        if (pasted === null || pasted.trim() === "") return;
        source = pasted;
        fileName = `pasted-${new Date().toISOString().slice(11, 19).replace(/:/g, "")}.txt`;
      }

      if (!source || source.trim() === "") {
        notify("Selected file is empty.", "error");
        return;
      }

      await parseSource(source, { fileName });
    } catch {
      // parseSource already notified
    } finally {
      setPicking(false);
    }
  }, [notify, parseSource, parseStatus, picking]);

  const [health, setHealth] = useState("checking");

  const probeHealth = useCallback(async () => {
    try {
      await checkHealth();
      setHealth("up");
      return true;
    } catch {
      setHealth("down");
      return false;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function probe() {
      try {
        await checkHealth();
        if (!cancelled) setHealth("up");
      } catch {
        if (!cancelled) setHealth("down");
      }
    }

    probe();
    const timer = setInterval(probe, HEALTH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const handleFlush = useCallback(() => {
    // Full workspace purge: graph, logs, outputs, caches and selection.
    if (typeof flushMemoryStore === "function") {
      flushMemoryStore();
    } else if (typeof useGraphStore.getState === "function" && typeof useGraphStore.getState().flushMemoryStore === "function") {
      useGraphStore.getState().flushMemoryStore();
    } else if (typeof clearMemoryStore === "function") {
      clearMemoryStore();
    } else if (typeof useGraphStore.getState === "function" && typeof useGraphStore.getState().flushMemory === "function") {
      useGraphStore.getState().flushMemory();
    }
    setFlushed(true);
    if (typeof notify === "function") {
      notify("Memory flushed — workspace reset", "ok");
    }
  }, [clearMemoryStore, flushMemoryStore, notify]);

  const handleToggleAutoArchitect = useCallback(async () => {
    if (architectBusy) return;
    if (autoArchitect) {
      if (typeof setAutoArchitect === "function") setAutoArchitect(false);
      return;
    }
    if (typeof setAutoArchitect === "function") setAutoArchitect(true);
    if (typeof generateAutoArchitect !== "function") return;
    try {
      await generateAutoArchitect();
    } catch {
      if (typeof setAutoArchitect === "function") setAutoArchitect(false);
    }
  }, [architectBusy, autoArchitect, generateAutoArchitect, setAutoArchitect]);

  const label = sourceName || project?.name || "Untitled";

  return (
    <>
    <input
      ref={folderInputRef}
      type="file"
      webkitdirectory=""
      directory=""
      multiple
      className="hidden"
      aria-hidden="true"
      onChange={handleFolderUpload}
    />
    <header className="flex h-11 shrink-0 items-center justify-between border-b border-revolut-border bg-revolut-bg px-4">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-revolut-accentBlack text-white">
            <Workflow size={13} strokeWidth={2.2} />
          </span>
          <span className="text-xs font-semibold tracking-tight text-revolut-textMain">AxiomTrace</span>
        </div>
        <div className="h-4 w-px bg-revolut-border" />
        <span className="max-w-[220px] truncate text-xs font-medium text-revolut-textMain">
          {label}
        </span>
        <div className="h-4 w-px bg-revolut-border" />
        <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-revolut-accentGreen">
          <CircleDot
            size={9}
            className={health === "up" ? "animate-led-pulse" : ""}
          />
          {health === "up" ? "RUNTIME LIVE" : health === "down" ? "OFFLINE" : "PROBING"}
        </span>
      </div>

      <nav aria-label="Workspace actions" className="flex min-w-0 items-center gap-1 overflow-x-auto">
        <button
          type="button"
          onClick={handleParse}
          disabled={parsing}
          className={cn(
            "flex h-6 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-md border border-transparent bg-[#0B0C0E] px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-white transition-colors hover:bg-black/85",
            FOCUS_RING,
            parsing && "opacity-60"
          )}
        >
          {parsing ? (
            <Loader2 size={10} className="animate-spin" aria-hidden="true" />
          ) : (
            <FileCode2 size={10} aria-hidden="true" />
          )}
          {parsing ? "PARSING" : "PARSE CODE"}
        </button>

        <button
          type="button"
          onClick={handleScanProject}
          disabled={scanning}
          title="Scan a project directory and generate a layered architecture graph"
          className={cn(
            "flex h-6 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-md border border-[#E3E5E9] bg-white px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-[#0B0C0E] transition-colors hover:border-[#D0D2D8] hover:bg-[#F4F5F7]",
            FOCUS_RING,
            scanning && "opacity-60"
          )}
        >
          {scanning ? (
            <Loader2 size={10} className="animate-spin" aria-hidden="true" />
          ) : (
            <FolderSearch size={10} aria-hidden="true" />
          )}
          {scanning ? "SCANNING" : "SCAN PROJECT"}
        </button>

        <button
          type="button"
          onClick={handleToggleAutoArchitect}
          disabled={architectBusy}
          title={
            autoArchitect
              ? "Disable Auto-Architect"
              : "Generate an architecture graph from the current workspace"
          }
          className={cn(
            "flex h-7 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-lg border border-transparent px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] transition-colors hover:bg-[#F4F5F7]",
            autoArchitect ? "text-revolut-accentBlue" : "text-[#5E6473] hover:text-[#0B0C0E]",
            architectBusy && "opacity-60",
            FOCUS_RING
          )}
        >
          {architectBusy ? (
            <Loader2 size={11} className="animate-spin" aria-hidden="true" />
          ) : (
            <Zap size={11} aria-hidden="true" />
          )}
          {architectBusy ? "ARCHITECTING" : "AUTO-ARCHITECT"}
        </button>

        {(Array.isArray(nodes) ? nodes.length : 0) > 0 ? (
          <button
            type="button"
            onClick={autoLayout}
            className={cn(
              "flex h-7 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-lg border border-transparent px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[#5E6473] transition-colors hover:bg-[#F4F5F7] hover:text-[#0B0C0E]",
              FOCUS_RING
            )}
          >
            <LayoutGrid size={11} aria-hidden="true" />
            LAYOUT RESET
          </button>
        ) : null}

        <button
          type="button"
          onClick={handleFlush}
          title={flushed ? "Memory flushed" : "Flush memory and reset workspace"}
          aria-live="polite"
          className={cn(
            "flex h-7 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-lg border border-transparent px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] transition-colors hover:bg-[#F4F5F7]",
            flushed
              ? "text-revolut-accentGreen"
              : "text-[#5E6473] hover:text-[#0B0C0E]",
            FOCUS_RING
          )}
        >
          {flushed ? <Check size={11} aria-hidden="true" /> : <MemoryStick size={11} aria-hidden="true" />}
          {flushed ? "FLUSHED" : "FLUSH MEMORY"}
          {(Array.isArray(nodes) ? nodes.length : 0) > 0 || logCount > 0 ? (
            <span className="tabular-nums rounded-full bg-[#F4F5F7] px-1.5 py-0.5 text-[9px] text-[#0B0C0E]">
              {(Array.isArray(nodes) ? nodes.length : 0) + logCount}
            </span>
          ) : null}
        </button>

        <button
          type="button"
          onClick={() => setAiOpen(true)}
          title="AI Settings"
          className={cn(
            "flex h-7 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-lg border border-transparent px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[#5E6473] transition-colors hover:bg-[#F4F5F7] hover:text-[#0B0C0E]",
            FOCUS_RING
          )}
        >
          <Settings size={11} aria-hidden="true" />
          AI SETTINGS
        </button>

        <button
          type="button"
          onClick={() => {
            setInspectorTab("transpile");
            if (!hasSelection) {
              notify(
                "Transpile tab opened - select a node on the canvas to convert its code",
                "ok"
              );
            }
          }}
          title="Open Transpiler"
          className={cn(
            "flex h-7 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-lg border border-transparent px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[#5E6473] transition-colors hover:bg-[#F4F5F7] hover:text-[#0B0C0E]",
            FOCUS_RING
          )}
        >
          <Code2 size={11} aria-hidden="true" />
          TRANSPILE
        </button>

        <button
          type="button"
          onClick={openExport}
          title="Export Architecture Spec"
          className={cn(
            "flex h-7 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-lg border border-transparent px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-[#5E6473] transition-colors hover:bg-[#F4F5F7] hover:text-[#0B0C0E]",
            FOCUS_RING
          )}
        >
          <Download size={11} aria-hidden="true" />
          EXPORT
        </button>

        <button
          type="button"
          onClick={() => window.location.reload()}
          title="Refresh workspace"
          aria-label="Refresh workspace"
          className={cn(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-transparent text-[#5E6473] transition-colors hover:bg-[#F4F5F7] hover:text-[#0B0C0E]",
            FOCUS_RING
          )}
        >
          <RefreshCw size={11} aria-hidden="true" />
        </button>
      </nav>
      <AISettingsModal
        key={`ai-settings-${aiOpen}`}
        open={aiOpen}
        onClose={() => setAiOpen(false)}
      />
    </header>
    {health === "down" ? (
      <div
        role="alert"
        className="flex h-9 shrink-0 items-center justify-center gap-2.5 border-b border-status-red/30 bg-status-red/5 px-4"
      >
        <AlertTriangle size={13} className="shrink-0 text-status-red" />
        <p className="min-w-0 truncate text-[11px] font-medium text-status-red">
          Backend offline - Flask API on port 5000 is unreachable.
          <span className="hidden md:inline"> Run <code className="rounded bg-status-red/10 px-1 font-mono text-[10px]">npm run backend</code> from the project root.</span>
        </p>
        <button
          type="button"
          onClick={() => {
            setHealth("checking");
            probeHealth();
          }}
          className="flex h-6 shrink-0 items-center gap-1 rounded-md border border-status-red/40 bg-white px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-status-red transition-colors hover:bg-status-red/10"
        >
          <RefreshCw size={10} />
          Retry
        </button>
      </div>
    ) : null}
    </>
  );
}
