// Checks the link preview tags of each page in the static export (out/): og:image, og:title,
// og:description, og:url and twitter:card/title/image, that each page points at its own image,
// and that the image exists as a 1200x630 PNG. Exits 1 on any failure.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("../out/", import.meta.url).pathname;
if (!existsSync(root)) {
  console.error("out/ not found. Run `npm run build` first.");
  process.exit(1);
}

const pages = [
  { file: "index.html", path: "/", image: "/og.png" },
  { file: "calyx.html", path: "/calyx", image: "/og/calyx.png" },
  ...["call-qa-agent", "n8n-leads", "rag-mcp"].map((s) => ({ file: `projects/${s}.html`, path: `/projects/${s}`, image: `/og/${s}.png` })),
];

const decode = (s) => s.replaceAll("&amp;", "&").replaceAll("&quot;", '"').replaceAll("&#x27;", "'").replaceAll("&lt;", "<").replaceAll("&gt;", ">");

function metaTags(html) {
  const tags = new Map();
  for (const [tag] of html.matchAll(/<meta\s[^>]*>/g)) {
    const key = tag.match(/\s(?:property|name)="([^"]+)"/)?.[1];
    const content = tag.match(/\scontent="([^"]*)"/)?.[1];
    if (key && content !== undefined && !tags.has(key)) tags.set(key, decode(content));
  }
  return tags;
}

function pngSize(file) {
  const b = readFileSync(file);
  if (b.length < 24 || b.toString("latin1", 1, 4) !== "PNG") return null;
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
}

const errors = [];
for (const p of pages) {
  const file = join(root, p.file);
  if (!existsSync(file)) {
    errors.push(`${p.path}: ${p.file} missing`);
    continue;
  }
  const m = metaTags(readFileSync(file, "utf8"));
  const fail = (msg) => errors.push(`${p.path}: ${msg}`);

  for (const key of ["og:title", "og:description", "og:url", "og:image", "og:image:width", "og:image:height", "og:image:alt", "twitter:card", "twitter:title", "twitter:description", "twitter:image"]) {
    if (!m.get(key)) fail(`no ${key}`);
  }
  if (m.get("twitter:card") !== "summary_large_image") fail(`twitter:card is "${m.get("twitter:card")}"`);
  if (m.get("og:image:width") !== "1200" || m.get("og:image:height") !== "630") fail("og:image size is not 1200x630");
  if (m.get("og:title") !== m.get("twitter:title")) fail("og:title and twitter:title differ");

  for (const key of ["og:image", "twitter:image", "og:url"]) {
    const v = m.get(key);
    if (!v) continue;
    let url;
    try {
      url = new URL(v);
    } catch {
      fail(`${key} is not an absolute URL: ${v}`);
      continue;
    }
    const want = key === "og:url" ? p.path : p.image;
    if (url.pathname.replace(/\/$/, "") !== want.replace(/\/$/, "")) fail(`${key} is ${url.pathname}, expected ${want}`);
  }

  const png = join(root, p.image.slice(1));
  const size = existsSync(png) ? pngSize(png) : null;
  if (!size) fail(`${p.image} missing or not a PNG`);
  else if (size[0] !== 1200 || size[1] !== 630) fail(`${p.image} is ${size.join("x")}, expected 1200x630`);

  console.log(`${p.path.padEnd(24)} ${p.image.padEnd(24)} ${m.get("og:title")}`);
}

const images = new Set(pages.map((p) => p.image));
if (images.size !== pages.length) errors.push("two pages share a preview image");

if (errors.length) {
  console.error(`\n${errors.length} problem(s):\n  ${errors.join("\n  ")}`);
  process.exit(1);
}
console.log(`\nPreview tags OK on ${pages.length} pages.`);
