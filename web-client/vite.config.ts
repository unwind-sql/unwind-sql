import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

// Output goes straight into the Python package's _static directory so that
// `Project.show()` keeps working without any extra packaging step.
const STATIC_DIR = resolve(here, "../src/unwind/web/_static");

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: STATIC_DIR,
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      // FastAPI backend started by `Project.show()` (default port 8765).
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: false,
        // Server-Sent Events for /api/investigate need streaming, not buffering.
        ws: false,
      },
    },
  },
});
