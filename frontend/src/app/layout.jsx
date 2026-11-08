import {
  Inter,
  JetBrains_Mono,
  Plus_Jakarta_Sans,
} from "next/font/google";
import "@xyflow/react/dist/style.css";
import "./globals.css";

import ErrorBoundary from "@/components/ui/ErrorBoundary";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const plusJakarta = Plus_Jakarta_Sans({
  variable: "--font-display",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata = {
  title: "AxiomTrace - Visual Logic Inspector",
  description:
    "Local-first desktop studio that compiles source code into an interactive AST node graph with sandbox execution, mutation stress testing and LLM context export.",
};

export const viewport = {
  colorScheme: "light",
  themeColor: "#FFFFFF",
};

export default function RootLayout({ children }) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${plusJakarta.variable} ${jetbrainsMono.variable} h-full antialiased`}
      style={{ colorScheme: "light" }}
    >
      <body className="h-full overflow-hidden bg-revolut-bg text-revolut-textMain">
        <ErrorBoundary>{children}</ErrorBoundary>
      </body>
    </html>
  );
}
