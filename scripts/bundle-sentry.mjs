import * as esbuild from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const entry = path.join(root, "prompt_matrix/static/src/sentry-init.js");
const outfile = path.join(root, "prompt_matrix/static/sentry.bundle.js");

await esbuild.build({
  entryPoints: [entry],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  outfile,
  minify: true,
  sourcemap: false,
});

console.log(`Wrote ${path.relative(root, outfile)}`);
