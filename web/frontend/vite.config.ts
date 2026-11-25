import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// DEV: Vite serves the UI and proxies /api to the backend, so the browser sees ONE origin
// and no CORS configuration is needed anywhere.
// PROD: the backend serves BOTH from 127.0.0.1 — app.py mounts the built assets AFTER its
// API routes so nothing shadows /api. This file is not involved in production at all.
const API_PORT = process.env.FORGE_WEB_PORT ?? "8765";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5190,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.tsx", "src/**/*.test.ts"],
    // e2e/ is Playwright's; Vitest must never try to run it.
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    css: false,
  },
});