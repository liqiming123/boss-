import { defineConfig } from "vite";
import { resolve } from "node:path";

// Content scripts are classic scripts in Chrome MV3. Build this entry as one
// self-contained IIFE so it does not contain top-level import statements.
export default defineConfig({
  build: {
    emptyOutDir: false,
    lib: {
      entry: resolve("src/content/content-entry.ts"),
      formats: ["iife"],
      name: "RecruitmentContent",
      fileName: () => "content.js",
    },
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
