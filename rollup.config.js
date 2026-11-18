import deckyPlugin from "@decky/rollup";

const config = deckyPlugin({});
// Decky cache-busts index.js on reload. Keep all plugin code in that entry so
// partial updates and cached child modules cannot mix different releases.
config.output.inlineDynamicImports = true;
config.plugins.push({
  name: "check-decky-entry",
  generateBundle(_options, bundle) {
    const chunks = Object.values(bundle).filter(item => item.type === "chunk");
    if (chunks.length !== 1 || chunks[0].fileName !== "index.js" ||
        chunks[0].imports.length || chunks[0].dynamicImports.length) {
      this.error("Decky frontend must be a self-contained dist/index.js");
    }
  },
});
export default config;
