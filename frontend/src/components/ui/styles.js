/**
 * Shared Revolut-inspired component recipes.
 *
 * Every interactive surface in the studio pulls its classes from here so the
 * pill geometry, elevation and typographic rhythm stay identical across the
 * header, inspector, modals and canvas toolbars.
 */

export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/70 focus-visible:ring-offset-2 focus-visible:ring-offset-bg-app";

export const BUTTON_PRIMARY =
  `inline-flex items-center justify-center gap-1.5 rounded-full bg-revolut-accentBlack px-5 py-2.5 text-xs font-semibold tracking-tight text-white shadow-pill-light transition-all duration-150 ease-in-out hover:bg-black/90 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 ${FOCUS_RING}`;

export const BUTTON_PRIMARY_LIGHT =
  `inline-flex items-center justify-center gap-1.5 rounded-full bg-revolut-textPrimary px-6 py-3 text-sm font-semibold tracking-tight text-white transition-all duration-150 ease-in-out hover:bg-black active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 ${FOCUS_RING}`;

export const BUTTON_SECONDARY_LIGHT =
  `inline-flex items-center justify-center gap-1.5 rounded-full border border-[#E6E8EC] bg-revolut-cardLight px-6 py-3 text-sm font-semibold tracking-tight text-revolut-textPrimary transition-all duration-150 ease-in-out hover:border-[#D5D8DF] hover:bg-white active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 ${FOCUS_RING}`;

export const BUTTON_GHOST_LIGHT =
  `inline-flex items-center justify-center gap-1.5 rounded-full border border-transparent px-4 py-2.5 text-xs font-medium tracking-tight text-revolut-textMuted transition-all duration-150 ease-in-out hover:bg-revolut-cardLight hover:text-revolut-textPrimary active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 ${FOCUS_RING}`;

export const ICON_BADGE_LIGHT =
  "flex shrink-0 items-center justify-center rounded-2xl border border-[#E6E8EC] bg-white text-revolut-textPrimary";

export const BUTTON_ACCENT =
  `inline-flex items-center justify-center gap-1.5 rounded-full bg-revolut-accentBlack px-5 py-2.5 text-xs font-medium tracking-tight text-white transition-all duration-150 ease-in-out hover:bg-black/90 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 ${FOCUS_RING}`;

export const BUTTON_SECONDARY =
  `inline-flex items-center justify-center gap-1.5 rounded-full border border-border-subtle bg-bg-card px-4 py-2.5 text-xs font-medium tracking-tight text-text-primary transition-all duration-150 ease-in-out hover:border-border-active hover:bg-bg-cardHover active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 ${FOCUS_RING}`;

export const BUTTON_GHOST =
  `inline-flex items-center justify-center gap-1.5 rounded-full border border-transparent px-4 py-2.5 text-xs font-medium tracking-tight text-text-secondary transition-all duration-150 ease-in-out hover:bg-bg-cardHover hover:text-text-primary active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 ${FOCUS_RING}`;

export const BUTTON_ICON =
  `inline-flex h-9 w-9 items-center justify-center rounded-full border border-border-subtle bg-bg-card/60 text-text-secondary transition-all duration-150 ease-in-out hover:border-border-active hover:bg-bg-cardHover hover:text-text-primary active:scale-95 ${FOCUS_RING}`;

export const CARD =
  "rounded-3xl border border-border-subtle bg-bg-card shadow-card-subtle";

export const CARD_LIGHT =
  "rounded-3xl border border-transparent bg-light-card text-light-text";

export const PILL =
  "inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-bg-card/70 px-3 py-1 font-medium";

export const SECTION_LABEL =
  "text-[10px] font-semibold uppercase tracking-[0.2em] text-text-dimmed";

export const ICON_BADGE =
  "flex shrink-0 items-center justify-center rounded-2xl border border-border-subtle bg-bg-input";

export const METRIC =
  "font-display font-bold tracking-tight text-text-primary axiom-numeric";
