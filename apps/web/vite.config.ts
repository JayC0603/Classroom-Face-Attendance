import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = loadEnv("development", import.meta.dirname, "FR_DEV_").FR_DEV_API_URL
  || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        ws: true
      },
      "/health": apiTarget
    }
  }
});
