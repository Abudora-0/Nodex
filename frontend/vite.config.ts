import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  worker: { format: "es" },
  clearScreen: false,
  plugins: [react()],
  server: {
    port: 5173,
    // The backend owns /api; everything else is served by Vite in dev.
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
  },
});
