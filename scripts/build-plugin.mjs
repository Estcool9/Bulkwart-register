import { build } from "esbuild";
import JSZip from "jszip";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pluginDir = path.join(root, "plugin");
const manifest = await readFile(path.join(pluginDir, "manifest.json"), "utf8");

const result = await build({
  entryPoints: [path.join(pluginDir, "src", "index.js")],
  bundle: true,
  format: "cjs",
  platform: "browser",
  target: "es2020",
  charset: "utf8",
  external: ["react", "react-dom", "react-dom/client", "react/jsx-runtime", "@plugin-host", "@plugin-host/ui"],
  write: false,
  logLevel: "warning",
});
const bundle = result.outputFiles[0].text;

const zip = new JSZip();
const fixedDate = new Date("2026-01-01T00:00:00Z");
zip.file("manifest.json", manifest, { date: fixedDate });
zip.file("index.js", bundle, { date: fixedDate });
const archive = await zip.generateAsync({
  type: "nodebuffer",
  compression: "DEFLATE",
  compressionOptions: { level: 9 },
});
const output = path.join(pluginDir, "stalwart-signup-manager.zip");
await writeFile(output, archive);
console.log("Built " + path.relative(root, output) + " (" + archive.length + " bytes)");
