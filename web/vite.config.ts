import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api":
          process.env.VITE_API_PROXY_TARGET ||
          env.VITE_API_PROXY_TARGET ||
          "http://127.0.0.1:8000",
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test-setup.ts",
      include: ["src/**/*.test.{ts,tsx}"],
    },
  };
});
