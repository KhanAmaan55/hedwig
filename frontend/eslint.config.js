import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage"] },
  js.configs.recommended,
  // `configs.flat.*` is the flat-config form; `configs.*` is still eslintrc-shaped.
  reactHooks.configs.flat["recommended-latest"],
  {
    // Type-aware linting is scoped to the files the tsconfig actually covers, so
    // config files written in plain JS do not trip rules that need a program.
    files: ["**/*.{ts,tsx}"],
    extends: [...tseslint.configs.strictTypeChecked],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { "react-refresh": reactRefresh },
    rules: {
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/consistent-type-imports": "error",
      // Numbers in template literals are unambiguous and readable; `String(n)` is noise.
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      // Time shown in the UI comes from the backend's clock, not the browser's
      // (docs/03 §5.6). The same seam, one layer up.
      "no-restricted-globals": [
        "error",
        { name: "Date", message: "Use time from the API, not the browser clock." },
      ],
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "src/test/**"],
    rules: {
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-return": "off",
    },
  },
);
