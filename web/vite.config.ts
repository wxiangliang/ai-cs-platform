import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 开发经 Vite 代理 /api → 后端（零 CORS 依赖）；
// 目标可用环境变量覆盖：VITE_API_TARGET=http://host:port npm run dev
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
        ws: true, // WebSocket 升级请求一并代理（用户端实时通道）
      },
    },
  },
});
