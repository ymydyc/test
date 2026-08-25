import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 前端开发时把 /api 代理到后端
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});