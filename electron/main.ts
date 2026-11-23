/**
 * Electron main process — the desktop shell.
 *
 * The shell owns the window and the backend's lifetime. It contains no application
 * logic: everything it knows about HEDWIG is a URL and a health endpoint (ADR-0015).
 *
 * Two modes:
 *   development — the Vite dev server and the backend are started by scripts/dev.mjs;
 *                 this process attaches to them and gets hot reload for free.
 *   production  — this process spawns `hedwig serve` and loads the built frontend from
 *                 disk. Nothing is served over HTTP except the loopback API.
 */

import { execFile, spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { join } from "node:path";
import { promisify } from "node:util";
import { BrowserWindow, app, ipcMain, shell } from "electron";

const isDevelopment = process.env.HEDWIG_DEV === "1" || !app.isPackaged;

const API_BASE_URL = process.env.HEDWIG_API_BASE_URL ?? "http://127.0.0.1:8730";
const RENDERER_URL = process.env.HEDWIG_RENDERER_URL ?? "http://localhost:5173";
const BACKEND_READY_TIMEOUT_MS = 30_000;
/** How to run the CLI. `uv run hedwig` in development; the installed `hedwig` when packaged. */
const HEDWIG_CLI = process.env.HEDWIG_CLI ?? (isDevelopment ? "uv run hedwig" : "hedwig");
const TOKEN_TIMEOUT_MS = 20_000;

const execFileAsync = promisify(execFile);

let mainWindow: BrowserWindow | null = null;
let backend: ChildProcess | null = null;
let isQuitting = false;

/** One instance per machine: two backends against one database is a documented failure
 *  mode (docs/17 §12), and the shell should not be the thing that causes it. */
if (!app.requestSingleInstanceLock()) {
  app.quit();
}

app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

function log(message: string, fields: Record<string, unknown> = {}): void {
  const suffix = Object.entries(fields)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" ");
  process.stdout.write(`[shell] ${message}${suffix ? ` ${suffix}` : ""}\n`);
}

/**
 * In a packaged app the backend is ours to start and to stop.
 * In development scripts/dev.mjs already runs it with --reload.
 */
function startBackend(): void {
  if (isDevelopment) {
    log("attaching to the development backend", { url: API_BASE_URL });
    return;
  }

  log("starting backend");
  backend = spawn("hedwig", ["serve"], {
    stdio: ["ignore", "inherit", "inherit"],
    env: { ...process.env, HEDWIG_LOGGING__FORMAT: "json" },
  });

  backend.on("exit", (code) => {
    log("backend exited", { code: code ?? "signal" });
    backend = null;
    // The shell is a window onto the backend. Without one there is nothing to show.
    if (!isQuitting) app.quit();
  });
}

async function waitForBackend(): Promise<boolean> {
  const deadline = Date.now() + BACKEND_READY_TIMEOUT_MS;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${API_BASE_URL}/v1/health`, {
        signal: AbortSignal.timeout(2000),
      });
      if (response.ok) {
        log("backend ready", { url: API_BASE_URL });
        return true;
      }
    } catch {
      // Not up yet. The loop is the retry.
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }

  log("backend did not become ready", { timeout_ms: BACKEND_READY_TIMEOUT_MS });
  return false;
}

/**
 * The API token (ADR-0022, docs/18 §13.2).
 *
 * Asked of the CLI rather than read from `~/.hedwig/token`, because only the Python config
 * loader knows every place the data directory can be moved to. Cached; a renderer that gets a
 * 401 asks again with `refresh`, which is how a rotation reaches an open window. Never logged.
 */
let cachedToken: string | null = null;
let pendingToken: Promise<string | null> | null = null;

async function resolveToken(refresh: boolean): Promise<string | null> {
  if (cachedToken && !refresh) return cachedToken;
  pendingToken ??= (async () => {
    const [command, ...args] = HEDWIG_CLI.split(/\s+/).filter(Boolean);
    if (!command) return null;
    try {
      const { stdout } = await execFileAsync(command, [...args, "token", "show"], {
        timeout: TOKEN_TIMEOUT_MS,
        env: process.env,
      });
      return stdout.trim() || null;
    } catch (error) {
      log("could not obtain the API token", {
        command: HEDWIG_CLI,
        error: error instanceof Error ? error.message.split("\n")[0] : "unknown",
      });
      return null;
    }
  })().finally(() => {
    pendingToken = null;
  });
  cachedToken = await pendingToken;
  return cachedToken;
}

/** Only the app's own page may ask for the token — not a frame it happened to load. */
function isOwnFrame(url: string | undefined): boolean {
  if (!url) return false;
  return isDevelopment ? url.startsWith(RENDERER_URL) : url.startsWith("file://");
}

ipcMain.handle("hedwig:token", async (event, refresh: unknown) => {
  if (!isOwnFrame(event.senderFrame?.url)) return null;
  return resolveToken(refresh === true);
});

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 720,
    minHeight: 520,
    show: false,
    backgroundColor: "#0e1116",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      // The renderer is untrusted by construction: it displays model output.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  window.once("ready-to-show", () => window.show());

  // Electron grants every permission by default. HEDWIG's page may use the microphone (speech
  // input, VOI-02) and nothing else: no camera, location, notifications or screen capture, and
  // never for any other origin.
  window.webContents.session.setPermissionRequestHandler((contents, permission, grant, details) => {
    const audioOnly =
      permission === "media" &&
      "mediaTypes" in details &&
      (details.mediaTypes ?? []).every((type) => type === "audio");
    grant(audioOnly && isOwnFrame(contents.getURL()));
  });
  window.webContents.session.setPermissionCheckHandler(
    (_contents, permission, origin) => permission === "media" && isOwnFrame(origin),
  );

  // External links open in the user's browser; nothing navigates away from the app.
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  window.webContents.on("will-navigate", (event, url) => {
    const allowed = isDevelopment ? [RENDERER_URL] : [];
    if (!allowed.some((prefix) => url.startsWith(prefix))) {
      event.preventDefault();
      void shell.openExternal(url);
    }
  });

  return window;
}

async function loadRenderer(window: BrowserWindow): Promise<void> {
  if (isDevelopment) {
    await window.loadURL(RENDERER_URL);
    window.webContents.openDevTools({ mode: "detach" });
    return;
  }
  await window.loadFile(join(__dirname, "..", "frontend", "index.html"));
}

async function main(): Promise<void> {
  startBackend();
  await app.whenReady();

  mainWindow = createWindow();
  const ready = await waitForBackend();
  if (!ready) {
    log("continuing without a backend; the UI will show a disconnected state");
  }
  await loadRenderer(mainWindow);

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow();
      void loadRenderer(mainWindow);
    }
  });
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

async function stopBackend(): Promise<void> {
  const child = backend;
  if (!child) return;
  backend = null;

  log("stopping backend");
  child.kill("SIGTERM");
  // Give the backend its documented graceful shutdown (docs/17 §4) before giving up.
  await Promise.race([once(child, "exit"), new Promise((resolve) => setTimeout(resolve, 5000))]);
  if (child.exitCode === null) child.kill("SIGKILL");
}

app.on("before-quit", (event) => {
  if (isQuitting) return;
  isQuitting = true;
  if (!backend) return;

  // Electron does not await an async listener, so the quit is deferred explicitly and
  // resumed once the backend has actually stopped. Otherwise the process would exit
  // while the backend is mid-shutdown.
  event.preventDefault();
  void stopBackend().finally(() => app.quit());
});

void main();
