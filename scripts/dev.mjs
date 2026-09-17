#!/usr/bin/env node
/**
 * One command to run HEDWIG in development: `npm run dev`.
 *
 * Starts the backend, the Vite dev server and the Electron shell in dependency order,
 * merges their logs into one stream, and shuts all three down together.
 *
 * Written with Node built-ins only. Orchestration is the part of a dev setup most likely
 * to misbehave at 2am, and it is worth being able to read all of it in one sitting.
 *
 * Flags:
 *   --no-electron   backend + frontend only (open http://localhost:5173 in a browser)
 *   --no-backend    attach to a backend you are already running elsewhere
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

const BACKEND_HOST = process.env.HEDWIG_API__HOST ?? "127.0.0.1";
const BACKEND_PORT = process.env.HEDWIG_API__PORT ?? "8730";
const API_BASE_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;
const RENDERER_URL = `http://localhost:${process.env.VITE_PORT ?? "5173"}`;

const READY_TIMEOUT_MS = 60_000;
const withElectron = !process.argv.includes("--no-electron");
const withBackend = !process.argv.includes("--no-backend");

const COLOURS = {
  backend: "\u001b[38;5;39m",
  frontend: "\u001b[38;5;42m",
  shell: "\u001b[38;5;213m",
  dev: "\u001b[38;5;244m",
};
const RESET = "\u001b[0m";
const supportsColour = process.stdout.isTTY;

const children = [];
let shuttingDown = false;

function paint(name, text) {
  const colour = supportsColour ? (COLOURS[name] ?? "") : "";
  const reset = supportsColour ? RESET : "";
  return `${colour}${name.padEnd(8)}${reset} ${text}`;
}

function note(message) {
  process.stdout.write(`${paint("dev", message)}\n`);
}

function run(name, command, args, { env = {}, cwd = ROOT } = {}) {
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, FORCE_COLOR: "1", ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });

  const forward = (stream) => {
    let buffer = "";
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) process.stdout.write(`${paint(name, line)}\n`);
      }
    });
  };
  forward(child.stdout);
  forward(child.stderr);

  child.on("exit", (code, signal) => {
    if (shuttingDown) return;
    note(`${name} exited (${signal ?? code}) — shutting everything down`);
    void shutdown(code ?? 1);
  });

  children.push({ name, child });
  return child;
}

async function waitForHttp(url, label) {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (shuttingDown) return false;
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
      if (response.ok) return true;
    } catch {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  note(`timed out waiting for ${label} at ${url}`);
  return false;
}

async function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;

  for (const { child } of [...children].reverse()) {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
  }

  // Give everything the chance to exit cleanly, then stop waiting.
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline && children.some(({ child }) => child.exitCode === null)) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  for (const { child } of children) {
    if (child.exitCode === null) child.kill("SIGKILL");
  }

  process.exit(code);
}

function preflight() {
  if (!existsSync(join(ROOT, ".venv"))) {
    note("no Python environment found. Run:  npm run setup");
    process.exit(1);
  }
  if (!existsSync(join(ROOT, "node_modules", "electron")) && withElectron) {
    note("no node_modules found. Run:  npm run setup");
    process.exit(1);
  }
}

async function main() {
  preflight();

  process.on("SIGINT", () => void shutdown(0));
  process.on("SIGTERM", () => void shutdown(0));

  note(`HEDWIG dev — api ${API_BASE_URL} · ui ${RENDERER_URL}`);

  if (withBackend) {
    run("backend", "uv", ["run", "hedwig", "serve", "--reload", "--log-level", "debug"]);
    if (!(await waitForHttp(`${API_BASE_URL}/v1/health`, "backend"))) return shutdown(1);
  }

  // The Vite binary directly, not through `npm run`: the npm wrapper adds a process that
  // turns every Ctrl-C into a spurious "Lifecycle script failed" error.
  run("frontend", join(ROOT, "node_modules", ".bin", "vite"), [], {
    cwd: join(ROOT, "frontend"),
  });
  if (!(await waitForHttp(RENDERER_URL, "vite"))) return shutdown(1);

  if (!withElectron) {
    note(`ready — open ${RENDERER_URL}`);
    return;
  }

  note("building the shell");
  const build = spawn("npx", ["tsc", "-p", "electron/tsconfig.json"], {
    cwd: ROOT,
    stdio: "inherit",
  });
  const buildCode = await new Promise((resolve) => build.on("exit", resolve));
  if (buildCode !== 0) return shutdown(buildCode ?? 1);

  run("shell", join(ROOT, "node_modules", ".bin", "electron"), ["."], {
    env: {
      HEDWIG_DEV: "1",
      HEDWIG_API_BASE_URL: API_BASE_URL,
      HEDWIG_RENDERER_URL: RENDERER_URL,
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1", // dev-server CSP warning; production is strict
    },
  });

  note("ready — edit src/hedwig or frontend/src and both reload");
}

await main();
