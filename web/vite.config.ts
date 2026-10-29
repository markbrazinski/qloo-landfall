import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // LANDFALL_API lets several dev servers each use their own app instance (default: the app on 8000)
  server: { port: 5173, proxy: { "/api": process.env.LANDFALL_API || "http://127.0.0.1:8000" } },
});
