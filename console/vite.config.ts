import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxy = {
  "/v1": "http://127.0.0.1:8080",
  "/health": "http://127.0.0.1:8080",
  "/scim": "http://127.0.0.1:8080",
  "/metrics": "http://127.0.0.1:8080",
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: apiProxy,
  },
  preview: {
    port: 5173,
    proxy: apiProxy,
  },
});
