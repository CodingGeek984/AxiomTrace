"use client";

import { statusMeta } from "@/config/theme";
import { cn } from "@/lib/utils";

const SIZES = {
  sm: "gap-1 px-2 py-1 text-[9px]",
  md: "gap-1.5 px-2.5 py-1 text-[10px]",
  lg: "gap-2 px-3 py-1.5 text-[11px]",
};

const LED_SIZES = {
  sm: "h-1.5 w-1.5",
  md: "h-2 w-2",
  lg: "h-2.5 w-2.5",
};

/**
 * Compact status pill with a glowing LED indicator.
 *
 * Renders the runtime vocabulary shared with the backend sandbox
 * (`idle | running | success | error | timeout`), defaulting to `idle`.
 *
 * @param {{
 *   status?: string,
 *   label?: string,
 *   size?: "sm"|"md"|"lg",
 *   pulse?: boolean,
 *   className?: string,
 * }} props
 */
export default function StatusBadge({
  status = "idle",
  label,
  size = "md",
  pulse = false,
  className,
}) {
  const meta = statusMeta(status);
  const text = label || meta.label;
  const animated = pulse || status === "running";

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border font-medium uppercase tracking-wider",
        SIZES[size] || SIZES.md,
        meta.tone,
        className
      )}
      style={{
        borderColor: `${meta.color}40`,
        backgroundColor: `${meta.color}12`,
      }}
      title={`status: ${status}`}
    >
      <span
        className={cn(
          "shrink-0 rounded-full",
          LED_SIZES[size] || LED_SIZES.md,
          animated && "animate-led-pulse"
        )}
        style={{
          backgroundColor: meta.color,
          boxShadow: `0 0 8px ${meta.color}80`,
        }}
      />
      {text}
    </span>
  );
}
