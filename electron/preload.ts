/**
 * Preload — the only bridge between the shell and the renderer.
 *
 * Deliberately tiny. The renderer talks to HEDWIG over the same HTTP API any client uses
 * (docs/16 §2); the shell's job is to tell it where the backend is and hand it the API token,
 * not to proxy it. The one IPC channel carries that credential and nothing else (docs/18
 * §13.2). If this file ever grows a channel that carries application data, the desktop shell
 * has stopped being a shell.
 */

import { contextBridge, ipcRenderer } from "electron";

export interface HedwigShell {
  /** Absolute base URL of the backend. Empty string when the page is served by Vite,
   *  which proxies `/v1` itself. */
  readonly apiBaseUrl: string;
  readonly platform: NodeJS.Platform;
  readonly isDesktop: true;
  /** The API token (ADR-0022). `refresh` after a 401, so a rotated token is picked up. */
  readonly readToken: (refresh?: boolean) => Promise<string | null>;
}

const shell: HedwigShell = {
  apiBaseUrl: process.env.HEDWIG_API_BASE_URL ?? "http://127.0.0.1:8730",
  platform: process.platform,
  isDesktop: true,
  readToken: (refresh = false) =>
    ipcRenderer.invoke("hedwig:token", refresh) as Promise<string | null>,
};

contextBridge.exposeInMainWorld("hedwig", Object.freeze(shell));
