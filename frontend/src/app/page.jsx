"use client";

import { ReactFlowProvider } from "@xyflow/react";
import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";

import ExportModal from "@/components/modals/ExportModal";
import GraphCanvas from "@/components/canvas/GraphCanvas";
import Inspector from "@/components/canvas/Inspector";
import Header from "@/components/ui/Header";
import StatusBar from "@/components/ui/StatusBar";
import Toolbar from "@/components/ui/Toolbar";
import { cn } from "@/lib/cn";
import { useGraphStore } from "@/store/useGraphStore";

function ToastHost() {
  const toast = useGraphStore((state) => state.toast);
  if (!toast) return null;

  const Icon =
    toast.tone === "error"
      ? XCircle
      : toast.tone === "warn"
        ? AlertTriangle
        : CheckCircle2;
  const toneClass =
    toast.tone === "error"
      ? "border-[#FF3B30]/40 text-[#FF3B30]"
      : toast.tone === "warn"
        ? "border-[#FF9500]/40 text-[#FF9500]"
        : "border-[#30D158]/40 text-[#30D158]";
  const iconClass =
    toast.tone === "error"
      ? "bg-[#FF3B30]/12 text-[#FF3B30]"
      : toast.tone === "warn"
        ? "bg-[#FF9500]/12 text-[#FF9500]"
        : "bg-[#30D158]/12 text-[#30D158]";

  return (
    <div className="pointer-events-none fixed bottom-24 left-1/2 z-[70] -translate-x-1/2">
      <div
        key={toast.id}
        className={cn(
          "flex max-w-[92vw] items-center gap-1.5 rounded-md border bg-revolut-surface py-1 pl-1 pr-2.5 text-[11px]",
          toneClass
        )}
      >
        <span
          className={cn(
            "flex h-5 w-5 shrink-0 items-center justify-center rounded-sm",
            iconClass
          )}
        >
          <Icon size={12} />
        </span>
        <span className="truncate">{toast.message}</span>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <ReactFlowProvider>
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-revolut-bg text-revolut-textMain">
        <Header />
        <main className="relative flex min-h-0 flex-1">
          <div className="relative flex min-w-0 flex-1">
            <GraphCanvas />
            <Toolbar />
          </div>
          <Inspector />
        </main>
        <StatusBar />
        <ExportModal />
        <ToastHost />
      </div>
    </ReactFlowProvider>
  );
}
