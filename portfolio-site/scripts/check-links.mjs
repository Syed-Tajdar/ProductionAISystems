// Checks every href/src in the static export (out/) resolves to a file or an
// element id. External links are listed, not fetched. Exits 1 on any failure.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const root = new URL("../out/", import.meta.url).pathname;
if (!existsSync(root)) {
  console.error("out/ not found. Run `npm run build` first.");
  process.exit(1);
}

const walk = (dir) =>
  readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });

const pages = walk(root).filter((f) => f.endsWith(".html"));
const ids = new Map(pages.map((p) => [p, new Set([...readFileSync(p, "utf8").matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]))]));

const resolvePage = (path) => {
  const clean = decodeURIComponent(path.split("?")[0]).replace(/^\//, "");
  for (const c of [clean, `${clean}.html`, join(clean, "index.html")]) {
    const full = join(root, c);
    if (existsSync(full) && statSync(full).isFile()) return full;
  }
  return null;
};

const errors = [];
const external = new Set();
let checked = 0;

for (const page of pages) {
  const html = readFileSync(page, "utf8");
  for (const [, attr, raw] of html.matchAll(/\s(href|src)="([^"]*)"/g)) {
    const url = raw.replaceAll("&amp;", "&");
    const where = `${relative(root, page)}: ${attr}="${url}"`;
    checked++;
    if (url === "" || url.includes("[")) errors.push(`placeholder or empty link  ${where}`);
    else if (/^(mailto:|tel:|data:)/.test(url)) continue;
    else if (/^(https?:)?\/\//.test(url)) {
      external.add(url);
      // The repository is private until PUBLIC_CODE_URL or PUBLIC_CODE=true is set; a link into it would answer 404.
      if (process.env.PUBLIC_CODE !== "true" && !process.env.PUBLIC_CODE_URL && /github\.com\/Syed-Tajdar\b/i.test(url)) errors.push(`link to the private repository  ${where}`);
    }
    else {
      const [path, hash] = url.split("#");
      const target = path ? resolvePage(path.startsWith("/") ? path : join("/", relative(root, join(page, "..")), path)) : page;
      if (!target) errors.push(`missing file  ${where}`);
      else if (hash && target.endsWith(".html") && !ids.get(target)?.has(hash)) errors.push(`missing #${hash}  ${where}`);
    }
  }
}

console.log(`Checked ${checked} links across ${pages.length} page(s).`);
if (external.size) console.log(`External (not fetched):\n  ${[...external].join("\n  ")}`);
if (errors.length) {
  console.error(`\n${errors.length} broken:\n  ${errors.join("\n  ")}`);
  process.exit(1);
}
console.log("No broken links.");
