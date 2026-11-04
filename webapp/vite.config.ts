import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  // Electron 生产环境用 file:// 加载，必须用相对路径，否则 /assets/* 会指向磁盘根目录
  base: "./",
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    outDir: "dist/renderer",
    emptyOutDir: true,
  },
});
