/**
 * Preload — the only bridge between the shell and the renderer.
 *
 * Deliberately tiny. The renderer talks to HEDWIG over the same HTTP API any client uses
 * (docs/16 §2); the shell's job is to tell it where the backend is, not to proxy it. If
 * this file ever grows an IPC channel that carries application data, the desktop shell has
 * stopped being a shell.
 */

import { contextBridge } from "electron";

export interface HedwigShell {
  /** Absolute base URL of the backend. Empty string when the page is served by Vite,
   *  which proxies `/v1` itself. */
  readonly apiBaseUrl: string;
  readonly platform: NodeJS.Platform;
  readonly isDesktop: true;
}

const shell: HedwigShell = {
  apiBaseUrl: process.env.HEDWIG_API_BASE_URL ?? "http://127.0.0.1:8730",
  platform: process.platform,
  isDesktop: true,
};

contextBridge.exposeInMainWorld("hedwig", Object.freeze(shell));
