/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const BACKEND = process.env.VITE_HEDWIG_API ?? "http://127.0.0.1:8730";

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
      "/v1": { target: BACKEND, changeOrigin: true },
    },
  },

  build: {
    outDir: "dist",
    sourcemap: true,
    emptyOutDir: true,
  },

  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
