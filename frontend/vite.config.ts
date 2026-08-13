import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the FastAPI backend on :8765 so we can iterate
// without CORS during development. In production we build to dist/ and the
// backend serves these assets under the same origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "../backend/app/static",
    emptyOutDir: true,
  },
});
