import js from "@eslint/js";
import tseslint from "typescript-eslint";

/**
 * Root config: the Electron main process and the development scripts.
 * The frontend is a workspace with its own config (frontend/eslint.config.js).
 */
export default tseslint.config(
  { ignores: ["dist", "frontend", "node_modules", ".venv", "docs"] },
  js.configs.recommended,
  {
    // Type-aware linting is scoped to the files the tsconfig actually covers.
    files: ["electron/**/*.ts"],
    extends: [...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
      globals: { __dirname: "readonly", process: "readonly", console: "readonly" },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-floating-promises": "error",
    },
  },
  {
    files: ["scripts/**/*.mjs", "*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: {
        process: "readonly",
        fetch: "readonly",
        AbortSignal: "readonly",
        setTimeout: "readonly",
      },
    },
  },
);
