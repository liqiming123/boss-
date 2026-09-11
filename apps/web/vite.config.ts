import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import ElementPlus from "unplugin-element-plus/vite";
import Components from "unplugin-vue-components/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  if (mode === "production" && /localhost|127\.0\.0\.1/.test(env.VITE_API_BASE_URL ?? "")) {
    throw new Error("生产构建禁止使用 localhost API 地址，请设置 VITE_API_BASE_URL 或使用同源 /api/v1");
  }
  return {
  plugins: [
    vue(),
    Components({ resolvers: [ElementPlusResolver()] }),
    ElementPlus(),
  ],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    server: { deps: { inline: ["element-plus"] } },
  },
  };
});
