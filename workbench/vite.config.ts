import { defineConfig } from "vite";

export default defineConfig({
  base: "/workbench/",
  build: {
    outDir: "../src/drov/server/static",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
});
