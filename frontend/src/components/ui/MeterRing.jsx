"use client";

import { COLORS } from "@/config/theme";
import { cn } from "@/lib/cn";

/**
 * Fintech-style circular meter used for compact health / stability metrics.
 *
 * @param {{
 *   value?: number,
 *   size?: number,
 *   stroke?: number,
 *   color?: string,
 *   label?: string,
 *   sublabel?: string,
 *   className?: string,
 * }} props
 */
export default function MeterRing({
  value = 0,
  size = 96,
  stroke = 8,
  color = COLORS.success,
  label,
  sublabel,
  className,
}) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;
  const center = size / 2;

  return (
    <div
      role="img"
      aria-label={`${label || `${Math.round(pct)}%`}${sublabel ? ` ${sublabel}` : ""}`}
      className={cn("relative inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="-rotate-90"
        aria-hidden="true"
      >
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={COLORS.border}
          strokeWidth={stroke}
        />
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{
            transition: "stroke-dashoffset 500ms ease",
            filter: `drop-shadow(0 0 6px ${color}66)`,
          }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-1">
        <span className="axiom-numeric font-display text-lg font-extrabold leading-none tracking-tight text-revolut-textMain">
          {label || `${Math.round(pct)}%`}
        </span>
        {sublabel ? (
          <span className="text-[8px] font-semibold uppercase tracking-[0.18em] text-text-dimmed">
            {sublabel}
          </span>
        ) : null}
      </div>
    </div>
  );
}
