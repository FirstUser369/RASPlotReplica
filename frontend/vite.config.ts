import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND_ORIGIN = "http://127.0.0.1:8765";
const TRUSTED_DEVELOPMENT_ORIGINS = new Set([
  "http://127.0.0.1:5173",
  "http://localhost:5173",
]);

type ProxyRequest = { setHeader(name: string, value: string): void };
type DevelopmentRequest = { headers: Record<string, string | string[] | undefined> };

export function rewriteTrustedDevelopmentOrigin(
  proxyRequest: ProxyRequest,
  request: DevelopmentRequest,
) {
  const origin = request.headers.origin;
  if (typeof origin === "string" && TRUSTED_DEVELOPMENT_ORIGINS.has(origin)) {
    proxyRequest.setHeader("origin", BACKEND_ORIGIN);
  }
}

// Dev server proxies /api to the FastAPI backend on :8765 so we can iterate
// without CORS during development. In production we build to dist/ and the
// backend serves these assets under the same origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (proxyRequest, request) => {
            rewriteTrustedDevelopmentOrigin(proxyRequest, request);
          });
        },
      },
    },
  },
  build: {
    outDir: "../backend/app/static",
    emptyOutDir: true,
  },
});
