// Compares out/profile.json (built from src/data/site.ts) with the copy the demos use, demo-ui/profile.json.
// `--write` updates the copy; then run `python demo-ui/sync.py` to pass it on to the demos. Exits 1 on a mismatch.
import { existsSync, readFileSync, writeFileSync } from "node:fs";

const built = new URL("../out/profile.json", import.meta.url);
const copy = new URL("../../demo-ui/profile.json", import.meta.url);
if (!existsSync(built)) {
  console.error("out/profile.json not found. Run `npm run build` first.");
  process.exit(1);
}
const want = JSON.stringify(JSON.parse(readFileSync(built, "utf8")), null, 2) + "\n";
if (process.argv.includes("--write")) {
  writeFileSync(copy, want);
  console.log("demo-ui/profile.json updated.");
} else if (!existsSync(copy) || readFileSync(copy, "utf8") !== want) {
  console.error("demo-ui/profile.json differs from the site data. Run `npm run check-profile -- --write`, then `python demo-ui/sync.py`.");
  process.exit(1);
} else {
  console.log("demo-ui/profile.json matches the site data.");
}
