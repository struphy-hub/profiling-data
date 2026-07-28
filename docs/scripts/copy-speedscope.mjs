// Copy the speedscope web app into public/ so run pages can embed it.
//
// speedscope ships a self-contained static build (its own HTML entry point plus
// hashed JS/CSS/font assets, all referenced relatively), so serving the release
// directory as-is is all that is needed — there is no server component and no
// bundling to do on our side.
//
// Run from package.json's `predev`/`prebuild` hooks, i.e. after `npm ci` has
// populated node_modules. The Python figure generation runs earlier in CI and
// cannot do this itself.
import { cp, rm, access } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(here, "../node_modules/speedscope/dist/release");
const destination = resolve(here, "../public/speedscope");

try {
  await access(source);
} catch {
  console.error(
    `speedscope not found at ${source}. Run \`npm install\` in docs/ first.`,
  );
  process.exit(1);
}

await rm(destination, { recursive: true, force: true });
await cp(source, destination, { recursive: true });
console.log(`Copied speedscope to ${destination}`);
