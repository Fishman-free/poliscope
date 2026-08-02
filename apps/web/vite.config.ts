import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The dev server proxies /api to the FastAPI process so the browser sees one
// origin. Without it EventSource would be a cross-origin request and the SSE
// stream -- the thing that makes the workspace live -- would need CORS setup
// that production does not use.
//
// loadEnv(), not the bare `process` global: this project has no other
// Node-specific code, so pulling in @types/node just to type one
// `process.env` read would be disproportionate (KISS/YAGNI). loadEnv is
// typed by vite itself and reads process.env internally on our behalf.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "POLISCOPE_");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: env.POLISCOPE_API_URL ?? "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
    build: { outDir: "dist", sourcemap: true },
  };
});
