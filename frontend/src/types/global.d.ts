/** What the Electron preload script exposes. Absent when running in a browser. */
interface HedwigShell {
  readonly apiBaseUrl: string;
  readonly platform: string;
  readonly isDesktop: true;
  /** The API token (ADR-0022), via the shell. `refresh` after a 401. */
  readonly readToken?: (refresh?: boolean) => Promise<string | null>;
}

interface Window {
  readonly hedwig?: HedwigShell;
}

interface ImportMetaEnv {
  readonly VITE_HEDWIG_API?: string;
  /** Where the avatar model is, relative to the page (docs/28 §5.1). */
  readonly VITE_HEDWIG_AVATAR?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
