import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies to a running engine: start it with `python -m orion --headless --port 8765`.
const target = `http://127.0.0.1:${process.env.ORION_PORT ?? 8765}`;

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../orion/web", emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: { proxy: { "/api": target, "/ws": { target, ws: true } } },
});
