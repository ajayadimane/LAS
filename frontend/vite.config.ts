import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

/** "las/" or "/las/" -> "/las"; "" or "/" -> "". */
const trimPath = (p = "") => {
  const v = p.trim().replace(/^\/+|\/+$/g, "");
  return v ? `/${v}` : "";
};

// In dev, /api/* and /health are proxied to the FastAPI middleware so the
// browser sees one origin and no CORS setup is needed.
//
// VITE_BASE_PATH is the UI's context path (where index.html and the assets
// are served); VITE_API_BASE_PATH is the middleware's (its CONTEXT_PATH).
// Both are baked in at build time, so a change needs a rebuild.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_PROXY_TARGET || "http://localhost:8000";
  const apiBase = trimPath(env.VITE_API_BASE_PATH);

  return {
    base: `${trimPath(env.VITE_BASE_PATH)}/`,
    plugins: [react()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: {
      port: 5173,
      proxy: {
        [`${apiBase}/api`]: { target, changeOrigin: true },
        [`${apiBase}/health`]: { target, changeOrigin: true },
      },
    },
  };
});
