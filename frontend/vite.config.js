/** Vite 构建配置，产物由 Nginx 作为单页应用提供。 */
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    proxy: { "/api": "http://127.0.0.1:8787" },
  },
});
