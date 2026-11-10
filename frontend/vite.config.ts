import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import cesium from "vite-plugin-cesium";

export default defineConfig({
  // vite-plugin-cesium 负责把 Cesium 的 Workers/Assets/Widgets 注入构建产物，
  // 否则部署后会出现"白屏但控制台 404 worker"的典型问题
  plugins: [vue(), cesium()],
  server: {
    port: 5173,
    proxy: {
      // 开发期直接转发到 FastAPI，避免前端手改 baseURL；CORS 仍由后端显式放行 5173
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      // Potree PoC 是独立入口，不进入 SPA 主包；主入口保持 index.html 不变
      input: {
        main: "index.html",
        potree: "potree.html",
      },
    },
  },
});
