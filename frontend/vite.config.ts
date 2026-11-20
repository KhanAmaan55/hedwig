/// <reference types="vitest/config" />
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Where the proxy sends `/v1`. `HEDWIG_API_BASE_URL` (the shell's name for it) moves the proxy
// alone; `VITE_HEDWIG_API` is also exposed to the page, which then calls the backend directly
// and bypasses the proxy — and with it the token the proxy adds (docs/18 §13.2).
const BACKEND =
  process.env.HEDWIG_API_BASE_URL ?? process.env.VITE_HEDWIG_API ?? "http://127.0.0.1:8730";
const REPO_ROOT = fileURLToPath(new URL("..", import.meta.url));
const HEDWIG_CLI = process.env.HEDWIG_CLI ?? "uv run hedwig";

/**
 * The API token for browser development (ADR-0022, docs/18 §13.2).
 *
 * The proxy adds it, so the page itself never holds it. Asked of the CLI — once, lazily, on
 * the first proxied request — because only the Python config loader knows where the data
 * directory is. A 401 drops the cache, so a rotation is picked up on the next request.
 */
let proxyToken: string | null | undefined;

function apiToken(): string | null {
  if (proxyToken !== undefined) return proxyToken;
  const [command, ...args] = HEDWIG_CLI.split(/\s+/).filter(Boolean);
  try {
    proxyToken = command
      ? execFileSync(command, [...args, "token", "show"], {
          cwd: REPO_ROOT,
          encoding: "utf8",
          timeout: 20_000,
          stdio: ["ignore", "pipe", "ignore"],
        }).trim() || null
      : null;
  } catch {
    console.warn(`[hedwig] could not obtain the API token with \`${HEDWIG_CLI} token show\``);
    proxyToken = null;
  }
  return proxyToken;
}

export default defineConfig({
  plugins: [react()],

  // Relative asset paths: in the packaged desktop app the renderer is loaded from
  // file://, where absolute paths do not resolve (docs/18 §6).
  base: "./",

  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    strictPort: true, // a shifting port would silently break the Electron shell
    proxy: {
      // Browser development goes through the proxy, so the app can use relative URLs
      // and never needs CORS. The desktop shell talks to the backend directly.
      "/v1": {
        target: BACKEND,
        changeOrigin: true,
        // The live channel too (docs/16 §13): upgrades are proxied and authenticated alike.
        ws: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq, request) => {
            if (request.headers.authorization) return;
            const token = apiToken();
            if (token) proxyReq.setHeader("Authorization", `Bearer ${token}`);
          });
          proxy.on("proxyReqWs", (proxyReq, request) => {
            if (request.headers.authorization) return;
            const token = apiToken();
            if (token) proxyReq.setHeader("Authorization", `Bearer ${token}`);
          });
          proxy.on("proxyRes", (response) => {
            if (response.statusCode === 401) proxyToken = undefined;
          });
        },
      },
    },
  },

  build: {
    outDir: "dist",
    sourcemap: true,
    emptyOutDir: true,
    // The one chunk over Vite's 500 kB default is the avatar pane — three.js and three-vrm,
    // ~760 kB — which is lazy-loaded so the rest of the app never pays for it (docs/28 §6).
    // 800 covers it; growth past that, or the main chunk approaching it, still warns.
    chunkSizeWarningLimit: 800,
  },

  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
