"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Eye, EyeOff, ExternalLink, KeyRound, Loader2, X } from "lucide-react";

import {
  getDeepSeekKey,
  maskDeepSeekKey,
  removeDeepSeekKey,
  setDeepSeekKey,
  validateApiKey,
} from "@/lib/aiKey";
import { validateAiKey } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useGraphStore } from "@/store/useGraphStore";

export default function AISettingsModal({ open, onClose }) {
  const notify = useGraphStore((state) => state.notify);
  const [key, setKey] = useState(() => getDeepSeekKey() || "");
  const [show, setShow] = useState(false);
  const [saved, setSaved] = useState(() => Boolean(getDeepSeekKey()));
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const closeTimer = useRef(null);

  useEffect(() => {
    return () => {
      if (closeTimer.current) window.clearTimeout(closeTimer.current);
    };
  }, []);

  if (!open) return null;

  const resetFeedback = () => {
    setError(null);
    setJustSaved(false);
  };

  const handleSave = async (event) => {
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    if (busy || justSaved) return;

    const trimmed = key.trim();
    const verdict = validateApiKey(trimmed);
    if (!verdict.ok) {
      setError(verdict.error);
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const response = await validateAiKey({ api_key: trimmed });
      const accepted = response && response.ok !== false;

      if (!accepted) {
        setError(
          (response && response.message) ||
            "The backend rejected this key. Check it and try again."
        );
        return;
      }

      setDeepSeekKey(trimmed);
      setSaved(true);
      setJustSaved(true);
      notify(
        (response && response.message) || "API key saved",
        response && response.verified === false ? "warn" : "ok"
      );
      closeTimer.current = window.setTimeout(() => {
        onClose();
      }, 900);
    } catch (saveError) {
      // Backend unreachable: keep the key locally so it works as soon as
      // the backend is reachable again, but tell the user it was not
      // verified server-side.
      setDeepSeekKey(trimmed);
      setSaved(true);
      setJustSaved(true);
      notify(
        saveError?.message ||
          "Key saved locally - backend unreachable, not verified",
        "warn"
      );
      closeTimer.current = window.setTimeout(() => {
        onClose();
      }, 1200);
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = () => {
    removeDeepSeekKey();
    setKey("");
    setSaved(false);
    resetFeedback();
    notify("API key removed", "warn");
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <form
        onSubmit={handleSave}
        className="relative w-full max-w-[420px] overflow-hidden rounded-xl border border-revolut-border bg-revolut-bg shadow-lg"
      >
        <div className="flex items-center justify-between border-b border-revolut-border px-4 py-3">
          <div className="flex items-center gap-2">
            <KeyRound size={14} className="text-revolut-textMain" />
            <span className="text-sm font-medium text-revolut-textMain">DeepSeek API Key</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-revolut-textMuted transition-colors hover:bg-revolut-surface hover:text-revolut-textMain"
            title="Close"
          >
            <X size={14} />
          </button>
        </div>
        <div className="flex flex-col gap-3 px-4 py-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="ai-api-key" className="text-xs text-revolut-textMain">
              API Key
            </label>
            <div
              className={cn(
                "flex items-center gap-1 rounded-md border bg-white",
                error ? "border-status-red/50" : "border-revolut-border"
              )}
            >
              <input
                id="ai-api-key"
                type={show ? "text" : "password"}
                value={key}
                onChange={(event) => {
                  setKey(event.target.value);
                  setError(null);
                  setJustSaved(false);
                }}
                placeholder="sk-..."
                className="flex-1 bg-transparent px-2.5 py-1.5 text-xs text-revolut-textMain outline-none"
                autoComplete="off"
                spellCheck={false}
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShow((prev) => !prev)}
                className="mr-1 rounded-md p-1 text-revolut-textMuted transition-colors hover:bg-revolut-surface hover:text-revolut-textMain"
                title={show ? "Hide key" : "Show key"}
              >
                {show ? <EyeOff size={12} /> : <Eye size={12} />}
              </button>
            </div>
            {error ? (
              <p className="text-[10px] font-medium text-status-red">{error}</p>
            ) : (
              <span className="text-[10px] text-revolut-textMuted">
                {saved
                  ? `Key saved locally: ${maskDeepSeekKey(key || getDeepSeekKey())}`
                  : "Key not set"}
              </span>
            )}
          </div>
          <p className="text-[11px] leading-relaxed text-revolut-textMuted">
            The key is stored only on this device and is sent with AI requests (Auto-Architect and
            Edge-cases) directly from the backend to the configured model endpoint. It is never
            persisted server-side. All other features work without a key.
          </p>
          <a
            href="https://platform.deepseek.com/api_keys"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[11px] text-revolut-accentBlue hover:underline"
          >
            Get API key
            <ExternalLink size={11} />
          </a>
          <div className="flex items-center justify-between gap-2 pt-1">
            <button
              type="button"
              onClick={handleRemove}
              disabled={!saved && !key.trim()}
              className={cn(
                "rounded-md px-2.5 py-1.5 text-xs text-revolut-textMuted transition-colors hover:bg-revolut-surface hover:text-revolut-textMain",
                (!saved && !key.trim()) && "opacity-50"
              )}
            >
              Remove key
            </button>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md px-2.5 py-1.5 text-xs text-revolut-textMuted transition-colors hover:bg-revolut-surface hover:text-revolut-textMain"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={busy || justSaved}
                className={cn(
                  "flex min-w-[72px] items-center justify-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-medium text-white transition-colors",
                  justSaved ? "bg-status-green" : "bg-revolut-accentBlack hover:bg-black/90",
                  (busy || justSaved) && "cursor-default"
                )}
              >
                {busy ? (
                  <>
                    <Loader2 size={12} className="animate-spin" />
                    Saving
                  </>
                ) : justSaved ? (
                  <>
                    <Check size={12} strokeWidth={3} />
                    Saved
                  </>
                ) : (
                  "Save"
                )}
              </button>
            </div>
          </div>
        </div>
      </form>
    </div>
  );
}
