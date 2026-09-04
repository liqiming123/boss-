import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import ElementPlus from "unplugin-element-plus/vite";
import Components from "unplugin-vue-components/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";
export default defineConfig({
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
});
