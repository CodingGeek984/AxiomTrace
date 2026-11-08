import { registerHooks } from "node:module";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

/**
 * Minimal resolver for the `@/*` path alias used by the app.
 *
 * The Next.js bundler resolves `@/lib/...` natively; Node's test runner needs
 * a tiny hook so the store and pure tracer modules can be imported directly.
 */
const srcRoot = new URL("../src/", import.meta.url);

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (!specifier.startsWith("@/")) {
      return nextResolve(specifier, context);
    }

    const base = new URL(specifier.slice(2), srcRoot);
    const candidates = [
      base,
      new URL(`${base.href}.js`),
      new URL(`${base.href}.jsx`),
      new URL(`${base.href}/index.js`),
    ];
    const match = candidates.find((candidate) => existsSync(fileURLToPath(candidate)));

    if (!match) {
      throw new Error(`Unable to resolve alias "${specifier}" from ${context.parentURL || "unknown"}`);
    }

    return { url: match.href, shortCircuit: true };
  },
});
