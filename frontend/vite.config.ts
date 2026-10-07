import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api and /files to the backend on 8000; in the container nginx
// does this. Paths match production exactly, so dev and Docker behave alike.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/files": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
