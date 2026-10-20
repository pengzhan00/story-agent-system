import react from "eslint-plugin-react";
import tseslint from "typescript-eslint";

// Mirror of the backend `tests/test_guards.py`: no user-visible text in source, everything goes through t(key).
export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts"] },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { react },
    settings: { react: { version: "18" } },
    rules: {
      "react/jsx-no-literals": ["error", { noStrings: false, allowedStrings: ["·", "/", "%", "-", "—", "…", "(", ")", ":", "+"] }],
    },
  },
  {
    files: ["src/**/*.test.{ts,tsx}", "tests/**"],
    rules: { "react/jsx-no-literals": "off" },
  },
);
