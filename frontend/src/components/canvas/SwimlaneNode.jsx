"use client";

/**
 * Enterprise swimlane band - one horizontal tier band behind the cards.
 * Pure Revolut Light-Mono: 1px #E3E5E9 border, 8px radius, white/grey
 * surfaces, stark black typography. Non-interactive so card dragging and
 * canvas deselection pass straight through.
 */
export default function SwimlaneNode({ data }) {
  const tier = data?.tier ?? null;
  const color = data?.tierColor || "#0B0C0E";
  const count = typeof data?.count === "number" ? data.count : 0;

  return (
    <div
      className="swimlane-band pointer-events-none h-full w-full rounded-[8px] border border-[#E3E5E9] bg-[#F4F5F7]/60"
      aria-hidden="true"
      title={data?.tierDescription || undefined}
    >
      <div className="flex items-center gap-2 px-4 pt-2.5">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-[8px] border border-[#E3E5E9] bg-[#FFFFFF] font-mono text-[9px] font-bold text-[#0B0C0E]">
          T{tier}
        </span>
        <span className="truncate text-[10px] font-bold uppercase tracking-[0.18em] text-[#0B0C0E]">
          {data?.tierLabel || `Tier ${tier}`}
        </span>
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: color }}
        />
        <span className="rounded-full border border-[#E3E5E9] bg-[#FFFFFF] px-2 py-0.5 font-mono text-[9px] font-semibold text-[#5E6473]">
          {count} {count === 1 ? "node" : "nodes"}
        </span>
        {data?.tierDescription ? (
          <span className="ml-auto hidden truncate text-[9px] text-[#5E6473] lg:block">
            {data.tierDescription}
          </span>
        ) : null}
      </div>
    </div>
  );
}
