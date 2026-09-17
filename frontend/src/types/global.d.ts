/** What the Electron preload script exposes. Absent when running in a browser. */
interface HedwigShell {
  readonly apiBaseUrl: string;
  readonly platform: string;
  readonly isDesktop: true;
}

interface Window {
  readonly hedwig?: HedwigShell;
}

interface ImportMetaEnv {
  readonly VITE_HEDWIG_API?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
