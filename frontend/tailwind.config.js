/**
 * AxiomTrace design tokens - Revolut-inspired light-mono system.
 *
 * Clean, crisp light-mode primary canvas with stark black typography,
 * soft neutral card containers, and zero visual clutter.
 *
 * @type {import('tailwindcss').Config}
 */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /* Canonical Revolut monochrome contract. */
        revolut: {
          bg: "#FFFFFF",         // Pure crisp white background
          surface: "#F4F5F7",    // Soft neutral card/panel fill
          border: "#E3E5E9",     // Subtle light border
          textMain: "#0B0C0E",   // Stark black primary text
          textMuted: "#5E6473",  // Medium neutral secondary text
          accentBlack: "#000000",// Solid black buttons & pills
          accentBlue: "#007AFF", // Minimal active indicator
          accentGreen: "#30D158" // Status LED
        },
        bg: {
          app: "#FFFFFF", // Pure crisp white canvas
          card: "#F4F5F7", // Soft neutral card surface
          cardHover: "#EDEFF3", // Raised / hover surface
          panel: "#F4F5F7", // Sidebar & inspector drawer
          input: "#FFFFFF", // Input / editor field
          elevated: "#FFFFFF", // Floating pill / popover surface
          scrim: "rgba(11, 12, 14, 0.4)", // Modal backdrop
        },
        border: {
          subtle: "#E3E5E9", // Subtle 1px border
          active: "#D0D2D8", // Focused element border
          accent: "#007AFF", // Accent active border
        },
        accent: {
          blue: "#007AFF", // Minimal active indicator
          blueGlow: "rgba(0, 122, 255, 0.15)",
          black: "#000000",
        },
        status: {
          green: "#30D158", // Status LED
          greenGlow: "rgba(48, 209, 88, 0.25)",
          red: "#FF3B30", // Alert / crash
          redGlow: "rgba(255, 59, 48, 0.25)",
          yellow: "#FF9500", // Warning orange
          orange: "#FF9500", // Warning orange
          blue: "#007AFF", // Processing / executing
        },
        light: {
          app: "#FFFFFF",
          card: "#F4F5F7",
          cardHover: "#EDEFF3",
          border: "#E3E5E9",
          borderActive: "#D0D2D8",
          text: "#0B0C0E",
          secondary: "#5E6473",
          dimmed: "#8B909C",
          accentSoft: "#F0F7FF",
        },
        text: {
          primary: "#0B0C0E", // Stark black primary text
          secondary: "#5E6473", // Medium neutral secondary text
          dimmed: "#8B909C", // Inactive / disabled text
        },
      },
      fontFamily: {
        sans: ["Inter", "SF Pro Display", "-apple-system", "sans-serif"],
        display: [
          "var(--font-display)",
          "Plus Jakarta Sans",
          "Inter",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      borderRadius: {
        "4xl": "2rem",
        "5xl": "2.5rem",
      },
      boxShadow: {
        "glow-blue": "0 0 0 1px rgba(0, 122, 255, 0.2)",
        "glow-blue-strong": "0 0 0 1px rgba(0, 122, 255, 0.4)",
        "glow-green": "0 0 0 1px rgba(48, 209, 88, 0.25)",
        "glow-red": "0 0 0 1px rgba(255, 59, 48, 0.25)",
        "glow-orange": "0 0 0 1px rgba(255, 149, 0, 0.25)",
        "card-subtle": "0 1px 1px rgba(11, 12, 14, 0.02)",
        "card-hover": "0 2px 4px rgba(11, 12, 14, 0.03)",
        panel: "0 10px 40px rgba(11, 12, 14, 0.04)",
        dock: "0 4px 24px rgba(11, 12, 14, 0.06), inset 0 1px 0 rgba(255, 255, 255, 1)",
        "pill-light": "0 1px 2px rgba(11, 12, 14, 0.02)",
        "light-card": "0 1px 1px rgba(11, 12, 14, 0.02)",
        "inner-hairline": "inset 0 1px 0 rgba(255, 255, 255, 1)",
      },
      backgroundImage: {
        "canvas-radial": "none",
        "brand-mark":
          "linear-gradient(140deg, #0B0C0E 0%, #000000 100%)",
        "card-sheen":
          "linear-gradient(180deg, rgba(255, 255, 255, 1) 0%, rgba(255, 255, 255, 0) 100%)",
        "light-sheen":
          "linear-gradient(180deg, rgba(255, 255, 255, 1) 0%, rgba(244, 245, 247, 0.8) 100%)",
      },
      keyframes: {
        "arch-in": {
          "0%": { opacity: 0, transform: "scale(0.98)" },
          "100%": { opacity: 1, transform: "scale(1)" },
        },
        "led-pulse": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.45", transform: "scale(0.82)" },
        },
        "fade-in-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "scan-line": {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(220%)" },
        },
        "float-soft": {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-4px)" },
        },
        "ring-pulse": {
          "0%": { opacity: "0.65", transform: "scale(0.9)" },
          "70%, 100%": { opacity: "0", transform: "scale(1.7)" },
        },
        "trace-pulse": {
          "0%, 100%": {
            opacity: "1",
            boxShadow: "0 0 0 0 rgba(0, 122, 255, 0.28)",
          },
          "50%": {
            opacity: "0.65",
            boxShadow: "0 0 0 7px rgba(0, 122, 255, 0)",
          },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "led-pulse": "led-pulse 1.05s ease-in-out infinite",
        "fade-in-up": "fade-in-up 220ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "scan-line": "scan-line 1.6s linear infinite",
        "float-soft": "float-soft 4.5s ease-in-out infinite",
        "ring-pulse": "ring-pulse 2.2s ease-out infinite",
        "trace-pulse": "trace-pulse 1.6s ease-in-out infinite",
        shimmer: "shimmer 2.4s linear infinite",
      },
    },
  },
  plugins: [],
};
