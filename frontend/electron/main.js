"use strict";

const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const path = require("path");
const fs = require("fs");
const http = require("http");
const { spawn } = require("child_process");

// Chromium's GPU compositor can render a fully black window on some Linux
// drivers (Mesa/NVIDIA/Wayland). Software rendering is slower but reliable;
// set AXIOM_ENABLE_GPU=1 to opt back in.
if (process.env.AXIOM_ENABLE_GPU !== "1") {
  app.disableHardwareAcceleration();
}

const DEV_URL = process.env.ELECTRON_START_URL || "http://localhost:3000";
const DEV_SERVER_PORT = Number(new URL(DEV_URL).port) || 3000;
const FRONTEND_DIR = path.resolve(__dirname, "..");
const BACKEND_DIR = path.resolve(__dirname, "..", "..", "backend");
const BACKEND_ENTRY = path.join(BACKEND_DIR, "app.py");
const IS_WINDOWS = process.platform === "win32";

// `npm run electron:dev` starts Next.js itself via concurrently, so it passes
// --no-renderer-spawn to avoid two dev servers racing for the same port.
const RENDERER_SPAWN_DISABLED =
  process.argv.includes("--no-renderer-spawn") ||
  process.env.AXIOM_DISABLE_RENDERER_SPAWN === "1";

const AUTO_START_RENDERER_AFTER_ATTEMPT = 1;
const RENDERER_RETRY_LIMIT = 60;
const BACKEND_HEALTH_URL =
  process.env.AXIOM_BACKEND_URL || "http://127.0.0.1:5000/api/v1/health";

let mainWindow = null;
let backendProcess = null;
let rendererProcess = null;
let backendSpawnedAt = 0;

function resolvePythonInterpreter() {
  const candidates = IS_WINDOWS
    ? [
        path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe"),
        path.join(BACKEND_DIR, ".venv", "bin", "python"),
        "python",
        "py",
      ]
    : [
        path.join(BACKEND_DIR, ".venv", "bin", "python"),
        path.join(BACKEND_DIR, ".venv", "bin", "python3"),
        "python3",
        "python",
      ];

  for (const candidate of candidates) {
    if (candidate === "python" || candidate === "python3" || candidate === "py") {
      return candidate;
    }
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch (error) {
      continue;
    }
  }
  return IS_WINDOWS ? "python" : "python3";
}

/**
 * Ask the already-running backend whether it is an AxiomTrace server.
 * Returns true only when /api/v1/health answers 200 with { ok: true }.
 */
function probeBackendHealth(timeoutMs = 2500) {
  return new Promise((resolve) => {
    const request = http.get(BACKEND_HEALTH_URL, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => {
        try {
          const parsed = JSON.parse(body);
          resolve(response.statusCode === 200 && parsed && parsed.ok === true);
        } catch {
          resolve(false);
        }
      });
    });
    request.setTimeout(timeoutMs, () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
  });
}

function startBackendProcess() {
  if (process.env.AXIOM_DISABLE_BACKEND === "1") {
    console.log("[AxiomTrace] Backend spawn disabled via AXIOM_DISABLE_BACKEND=1");
    return;
  }
  if (!fs.existsSync(BACKEND_ENTRY)) {
    console.warn("[AxiomTrace] backend/app.py not found at", BACKEND_ENTRY);
    return;
  }
  if (backendProcess) {
    return;
  }

  const interpreter = resolvePythonInterpreter();
  try {
    backendProcess = spawn(interpreter, [BACKEND_ENTRY], {
      cwd: BACKEND_DIR,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        AXIOM_HOST: "127.0.0.1",
        AXIOM_PORT: "5000",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    console.error("[AxiomTrace] Failed to spawn Python backend:", error.message);
    backendProcess = null;
    return;
  }

  backendProcess.stdout.on("data", (chunk) => {
    console.log(`[axiom-backend] ${chunk.toString().trim()}`);
  });
  backendProcess.stderr.on("data", (chunk) => {
    console.error(`[axiom-backend] ${chunk.toString().trim()}`);
  });
  backendProcess.on("error", (error) => {
    console.error("[AxiomTrace] Backend process error:", error.message);
    backendProcess = null;
  });
  backendProcess.on("exit", (code, signal) => {
    const uptimeMs = backendSpawnedAt ? Date.now() - backendSpawnedAt : null;
    console.log(
      `[AxiomTrace] Backend exited (code=${code}, signal=${signal || "none"})`
    );
    if (code !== 0 && uptimeMs !== null && uptimeMs < 5000) {
      console.error(
        "[AxiomTrace] Backend died immediately - is another service already bound to port 5000? " +
          "Stop it (e.g. `fuser -k 5000/tcp`) or start AxiomTrace with AXIOM_PORT set."
      );
    }
    backendProcess = null;
    backendSpawnedAt = 0;
  });

  backendSpawnedAt = Date.now();
  console.log(`[AxiomTrace] Spawned backend: ${interpreter} ${BACKEND_ENTRY}`);
}

function stopBackendProcess() {
  if (!backendProcess) {
    return;
  }
  try {
    if (IS_WINDOWS) {
      spawn("taskkill", ["/pid", String(backendProcess.pid), "/f", "/t"], {
        stdio: "ignore",
      });
    } else {
      backendProcess.kill("SIGTERM");
      const forceTimer = setTimeout(() => {
        try {
          backendProcess.kill("SIGKILL");
        } catch (error) {
          console.warn("[AxiomTrace] SIGKILL failed:", error.message);
        }
      }, 2500);
      forceTimer.unref();
    }
  } catch (error) {
    console.warn("[AxiomTrace] Failed to terminate backend:", error.message);
  } finally {
    backendProcess = null;
  }
}

function resolveNextBinary() {
  const binary = path.join(
    FRONTEND_DIR,
    "node_modules",
    ".bin",
    IS_WINDOWS ? "next.cmd" : "next"
  );
  return fs.existsSync(binary) ? binary : null;
}

function startRendererProcess() {
  if (RENDERER_SPAWN_DISABLED) {
    console.log("[AxiomTrace] Renderer spawn is disabled");
    return;
  }
  if (rendererProcess) {
    return;
  }

  const nextBinary = resolveNextBinary();
  if (!nextBinary) {
    console.warn(
      "[AxiomTrace] Next.js binary not found. Run \"npm install\" in",
      FRONTEND_DIR
    );
    return;
  }

  rendererProcess = spawn(
    nextBinary,
    ["dev", "-p", String(DEV_SERVER_PORT)],
    {
      cwd: FRONTEND_DIR,
      env: { ...process.env, PORT: String(DEV_SERVER_PORT) },
      stdio: ["ignore", "pipe", "pipe"],
      shell: IS_WINDOWS,
    }
  );

  rendererProcess.stdout.on("data", (chunk) => {
    console.log(`[axiom-next] ${chunk.toString().trim()}`);
  });
  rendererProcess.stderr.on("data", (chunk) => {
    console.error(`[axiom-next] ${chunk.toString().trim()}`);
  });
  rendererProcess.on("error", (error) => {
    console.error("[AxiomTrace] Next.js process error:", error.message);
    rendererProcess = null;
  });
  rendererProcess.on("exit", (code, signal) => {
    console.log(
      `[AxiomTrace] Next.js exited (code=${code}, signal=${signal || "none"})`
    );
    rendererProcess = null;
  });

  console.log(
    `[AxiomTrace] Started Next.js dev server on port ${DEV_SERVER_PORT}`
  );
}

function stopRendererProcess() {
  if (!rendererProcess) {
    return;
  }
  try {
    if (IS_WINDOWS) {
      spawn("taskkill", ["/pid", String(rendererProcess.pid), "/f", "/t"], {
        stdio: "ignore",
      });
    } else {
      rendererProcess.kill("SIGTERM");
      const forceTimer = setTimeout(() => {
        try {
          rendererProcess.kill("SIGKILL");
        } catch (error) {
          console.warn("[AxiomTrace] Next.js SIGKILL failed:", error.message);
        }
      }, 2500);
      forceTimer.unref();
    }
  } catch (error) {
    console.warn("[AxiomTrace] Failed to terminate Next.js:", error.message);
  } finally {
    rendererProcess = null;
  }
}

function probeUrl(url, timeoutMs = 4000) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    const request = http.get(url, (response) => {
      response.resume();
      finish({ reachable: true, refused: false });
    });
    request.setTimeout(timeoutMs, () => {
      request.destroy();
      finish({ reachable: false, refused: false });
    });
    request.on("error", (error) => {
      finish({
        reachable: false,
        refused: error.code === "ECONNREFUSED" || error.code === "ENOTFOUND",
      });
    });
  });
}

function showRendererError(detail) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  const html = `<!doctype html>
<html>
  <head><meta charset="utf-8" /><title>AxiomTrace</title></head>
  <body style="margin:0;background:#0B0C0E;color:#F2F4F8;font-family:system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center">
    <div style="max-width:560px;padding:32px;border:1px solid #262933;border-radius:16px;background:#14161B">
      <h1 style="margin:0 0 12px;font-size:18px">Не удалось загрузить интерфейс</h1>
      <p style="margin:0 0 16px;color:#A7ADBB;font-size:14px;line-height:1.6">
        Next.js dev-сервер не отвечает на ${DEV_URL}. Запустите приложение командой
        <code style="color:#007AFF">npm run electron:dev</code> из папки <code style="color:#007AFF">frontend</code>.
      </p>
      <pre style="margin:0;padding:12px;background:#0F1014;border:1px solid #262933;border-radius:10px;color:#FF453A;white-space:pre-wrap;font-size:12px">${detail}</pre>
    </div>
  </body>
</html>`;
  mainWindow
    .loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
    .then(() => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.show();
      }
    })
    .catch((error) => {
      console.error("[AxiomTrace] Failed to show error page:", error.message);
    });
}

async function waitForRenderer(attempt = 0) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }

  const probe = await probeUrl(DEV_URL);
  if (probe.reachable) {
    try {
      await mainWindow.loadURL(DEV_URL);
      return;
    } catch (error) {
      console.warn(
        `[AxiomTrace] Renderer probe succeeded but load failed:`,
        error.message
      );
    }
  }

  console.warn(
    `[AxiomTrace] Renderer not ready (attempt ${attempt + 1}/${RENDERER_RETRY_LIMIT}): ${DEV_URL}`
  );

  if (
    probe.refused &&
    attempt >= AUTO_START_RENDERER_AFTER_ATTEMPT &&
    !rendererProcess &&
    !RENDERER_SPAWN_DISABLED
  ) {
    startRendererProcess();
  }

  if (attempt < RENDERER_RETRY_LIMIT) {
    setTimeout(() => waitForRenderer(attempt + 1), 1000);
  } else {
    showRendererError(
      `Не удалось подключиться к ${DEV_URL} после ${RENDERER_RETRY_LIMIT} попыток.`
    );
  }
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1080,
    minHeight: 640,
    show: false,
    backgroundColor: "#0B0C0E",
    title: "AxiomTrace",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (process.env.AXIOM_FOCUS === "1") {
      mainWindow.focus();
    }
  });

  mainWindow.webContents.on(
    "did-fail-load",
    (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
      if (!isMainFrame || errorCode === -3) {
        return;
      }
      console.error(
        `[AxiomTrace] Failed to load ${validatedURL}: ${errorDescription} (${errorCode})`
      );
    }
  );

  if (!app.isPackaged && process.env.AXIOM_DEVTOOLS !== "0") {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  waitForRenderer();
}

ipcMain.handle("axiom:select-folder", async () => {
  if (!mainWindow) {
    return { canceled: true, filePaths: [] };
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Open project folder",
    properties: ["openDirectory", "createDirectory"],
    buttonLabel: "Open Project",
  });
  return { canceled: result.canceled, filePaths: result.filePaths };
});

ipcMain.handle("axiom:select-file", async () => {
  if (!mainWindow) {
    return { canceled: true, filePaths: [] };
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Open source file",
    properties: ["openFile"],
    filters: [
      { name: "Source files", extensions: ["py", "js", "jsx", "ts", "tsx", "mjs", "cjs"] },
      { name: "All files", extensions: ["*"] },
    ],
    buttonLabel: "Parse File",
  });
  return { canceled: result.canceled, filePaths: result.filePaths };
});

ipcMain.handle("axiom:read-file", async (_event, filePath) => {
  if (typeof filePath !== "string" || filePath.trim().length === 0) {
    return { ok: false, error: "A file path is required." };
  }
  try {
    const content = fs.readFileSync(filePath, "utf8");
    return { ok: true, path: filePath, content };
  } catch (error) {
    return { ok: false, path: filePath, error: error.message };
  }
});

ipcMain.handle("axiom:write-file", async (_event, filePath, content) => {
  if (typeof filePath !== "string" || filePath.trim().length === 0) {
    return { ok: false, error: "A file path is required." };
  }
  if (typeof content !== "string") {
    return { ok: false, error: "Content must be a string." };
  }
  try {
    const directory = path.dirname(filePath);
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(filePath, content, "utf8");
    return { ok: true, path: filePath, bytes: Buffer.byteLength(content, "utf8") };
  } catch (error) {
    return { ok: false, path: filePath, error: error.message };
  }
});

ipcMain.handle("axiom:app-info", async () => ({
  versions: {
    electron: process.versions.electron,
    node: process.versions.node,
    chrome: process.versions.chrome,
  },
  platform: process.platform,
  backendEntry: BACKEND_ENTRY,
}));

app.whenReady().then(async () => {
  // Reuse a healthy backend if one is already listening on :5000 instead
  // of spawning a duplicate that dies with EADDRINUSE.
  const alreadyRunning = await probeBackendHealth();
  if (alreadyRunning) {
    console.log(
      "[AxiomTrace] Backend already healthy on :5000 - reusing it (spawn skipped)"
    );
  } else {
    startBackendProcess();
  }
  createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  stopRendererProcess();
  stopBackendProcess();
});

app.on("will-quit", () => {
  stopRendererProcess();
  stopBackendProcess();
});

process.on("SIGTERM", () => {
  stopRendererProcess();
  stopBackendProcess();
  app.quit();
});

process.on("SIGINT", () => {
  stopRendererProcess();
  stopBackendProcess();
  app.quit();
});
