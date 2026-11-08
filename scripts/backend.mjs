#!/usr/bin/env node
/**
 * AxiomTrace backend launcher.
 *
 * 1. If a healthy AxiomTrace backend already answers on :5000 (spawned by
 *    Electron or a previous run), reuse it and exit 0.
 * 2. Otherwise spawn the Flask app from backend/.venv (or the system
 *    python as a fallback) and forward stdio/signals.
 *
 * Port can be overridden with AXIOM_PORT; host with AXIOM_HOST.
 */
import http from "node:http";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BACKEND_DIR = path.join(ROOT, "backend");
const ENTRY = path.join(BACKEND_DIR, "app.py");

const PORT = Number(process.env.AXIOM_PORT || 5000);
const HOST = process.env.AXIOM_HOST || "127.0.0.1";

function probeHealth(timeoutMs = 2000) {
  return new Promise((resolve) => {
    const request = http.request(
      { host: HOST, port: PORT, path: "/api/v1/health", method: "GET", timeout: timeoutMs },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => (body += chunk));
        response.on("end", () => {
          try {
            const parsed = JSON.parse(body);
            resolve(response.statusCode === 200 && parsed && parsed.ok === true);
          } catch {
            resolve(false);
          }
        });
      }
    );
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
    request.end();
  });
}

function resolveInterpreter() {
  const candidates = [
    path.join(BACKEND_DIR, ".venv", "bin", "python"),
    path.join(BACKEND_DIR, ".venv", "bin", "python3"),
    path.join(BACKEND_DIR, "venv", "bin", "python"),
    "python3",
    "python",
  ];
  for (const candidate of candidates) {
    if (!candidate.includes(path.sep)) return candidate; // PATH lookup
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      /* try next */
    }
  }
  return "python3";
}

const healthy = await probeHealth();
if (healthy) {
  console.log(
    `[axiomtrace] Backend already running on http://${HOST}:${PORT} - reusing it (nothing to start).`
  );
  process.exit(0);
}

if (!fs.existsSync(ENTRY)) {
  console.error(`[axiomtrace] backend/app.py not found at ${ENTRY}`);
  process.exit(1);
}

const interpreter = resolveInterpreter();
console.log(`[axiomtrace] Starting backend: ${interpreter} ${ENTRY}`);

const child = spawn(interpreter, [ENTRY], {
  cwd: BACKEND_DIR,
  stdio: "inherit",
  env: {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    AXIOM_HOST: HOST,
    AXIOM_PORT: String(PORT),
  },
});

const forward = (signal) => {
  if (!child.killed) child.kill(signal);
};
process.on("SIGINT", () => forward("SIGINT"));
process.on("SIGTERM", () => forward("SIGTERM"));
process.on("exit", () => forward("SIGTERM"));

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(typeof code === "number" ? code : 0);
});
