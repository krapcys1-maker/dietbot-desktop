import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// W trybie deweloperskim zapytania /api trafiają do lokalnego serwera DietBota (dietbot serwer --port 8765).
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
});
