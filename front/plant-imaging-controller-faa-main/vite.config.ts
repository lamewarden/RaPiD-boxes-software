import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";

// The backend is the Python FastAPI app (see ../back). In development we proxy
// /api (REST + WebSocket + MJPEG) to it so the dev server mirrors production,
// where FastAPI serves this built SPA from dist/spa directly.
const API_TARGET = process.env.RAPIDBOXES_API ?? "http://localhost:8000";

// https://vitejs.dev/config/
export default defineConfig(() => ({
  server: {
    host: true,
    port: 8080,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist/spa",
  },
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./client"),
      "@shared": path.resolve(__dirname, "./shared"),
    },
  },
}));
