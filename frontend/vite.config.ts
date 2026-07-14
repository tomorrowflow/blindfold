import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// ADR-0026: the built bundle is vendored into src/blindfold/ui_dist/ and served by
// FastAPI at /ui/ — `base` must match that mount point so asset URLs resolve.
// `server.proxy` gives the `vite dev` loop a live API without a second CORS story:
// run `blindfold serve` on 127.0.0.1:25463, then `npm run dev` here.
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  build: {
    outDir: "../src/blindfold/ui_dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:25463",
    },
  },
});
