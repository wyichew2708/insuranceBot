import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    // §10.4: no source maps in prod builds
    sourcemap: false,
  },
  server: {
    proxy: {
      "/v1": "http://localhost:8000",
    },
  },
});
