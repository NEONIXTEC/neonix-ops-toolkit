import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// dev: the Vite server proxies /api to the FastAPI server (uvicorn server.app:app --port 8000),
// so the browser sees one origin and the API needs no CORS settings.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
  },
});
