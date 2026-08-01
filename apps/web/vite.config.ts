import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies /api to the FastAPI process so the browser sees one
// origin. Without it EventSource would be a cross-origin request and the SSE
// stream -- the thing that makes the workspace live -- would need CORS setup
// that production does not use.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.POLISCOPE_API_URL ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
