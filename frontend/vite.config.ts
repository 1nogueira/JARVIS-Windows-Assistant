import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    strictPort: true,
    host: "127.0.0.1",
    port: 1420,
    watch: { ignored: ["**/src-tauri/target/**"] },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "chrome105",
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/recharts|victory-vendor|d3-/.test(id)) return "charts";
          if (/katex|rehype-katex|remark-math/.test(id)) return "math";
          if (/highlight\.js|lowlight|rehype-highlight/.test(id)) return "syntax";
          if (/react-markdown|remark-|rehype-|unified/.test(id)) return "markdown";
          if (id.includes("@tauri-apps")) return "tauri";
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
