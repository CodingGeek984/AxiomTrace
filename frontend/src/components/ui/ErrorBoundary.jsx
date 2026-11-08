"use client";

import { Component } from "react";
import { AlertOctagon, RefreshCw } from "lucide-react";

import { cn } from "@/lib/cn";
import { BUTTON_PRIMARY, BUTTON_GHOST } from "@/components/ui/styles";

/**
 * Last line of defence for the canvas.
 *
 * React only unmounts a tree when a render throws during commit; without a
 * boundary a single malformed node payload would take the whole studio down.
 * The boundary keeps the shell alive and offers a reload that preserves the
 * persisted workspace on the backend.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
    this.handleRetry = this.handleRetry.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Keep the detail in the console for local debugging; the UI only
    // surfaces a short, actionable message.
    console.error("[AxiomTrace] render error:", error, info);
  }

  handleRetry() {
    this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const message =
      (error && error.message) || "An unexpected render error occurred.";

    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-bg-app p-6">
        <div className="w-full max-w-md rounded-[2rem] border border-border-subtle bg-bg-card p-7 shadow-panel">
          <div className="flex items-center gap-3.5">
            <span className="flex h-12 w-12 items-center justify-center rounded-3xl border border-status-red/35 bg-status-red/10 text-status-red">
              <AlertOctagon size={18} />
            </span>
            <div className="min-w-0">
              <p className="font-display text-lg font-bold tracking-tight text-revolut-textMain">
                The workspace hit an error
              </p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-text-dimmed">
                AxiomTrace kept running - your project is still on disk.
              </p>
            </div>
          </div>

          <pre className="mt-4 max-h-40 overflow-auto rounded-2xl border border-border-subtle bg-bg-input p-4 font-mono text-[11px] leading-relaxed text-status-red">
            {String(message).slice(0, 1200)}
          </pre>

          <div className="mt-5 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className={cn(BUTTON_GHOST, "px-4 py-2")}
            >
              Reload
            </button>
            <button
              type="button"
              onClick={this.handleRetry}
              className={cn(BUTTON_PRIMARY, "px-4 py-2")}
            >
              <RefreshCw size={13} />
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }
}
