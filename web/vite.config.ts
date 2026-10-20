/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API address comes from the environment, never from source: `STORY_AGENT_API=http://host:port pnpm dev`.
const api = process.env.STORY_AGENT_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: api, changeOrigin: true } } },
  build: { outDir: "dist", sourcemap: true },
  test: { environment: "jsdom", setupFiles: ["./tests/setup.ts"], globals: true, css: false },
});
