// Content-Security-Policy for the static export (out/).
//
//   node scripts/csp.mjs           after `next build`: writes a policy into every page
//   node scripts/csp.mjs --check   verifies every page has a policy that covers exactly its inline scripts
//
// Next.js starts each exported page with inline scripts that differ per page (the page's data), next to the
// theme script from layout.tsx. A response header cannot list their hashes, because a static host sends the
// same header for every page and the hashes change with every build. So each page gets its own policy in a
// <meta http-equiv> tag with the SHA-256 of each inline script it contains: nothing inline runs unless it was in
// the build. What a <meta> policy cannot carry (frame-ancestors) is sent as a header from vercel.json.
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const out = new URL("../out/", import.meta.url).pathname;
const check = process.argv.includes("--check");
if (!existsSync(out)) {
  console.error("out/ not found. Run `npm run build` first.");
  process.exit(1);
}

const META = /<meta http-equiv="Content-Security-Policy" content="([^"]*)"\/>/;
const SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
// Data blocks (JSON-LD and other JSON) are never run, so they need no hash.
const isData = (attrs) => /\btype="(application\/(ld\+)?json|text\/template)"/.test(attrs);

function pages(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? pages(path) : name.endsWith(".html") ? [path] : [];
  });
}

function inlineHashes(html) {
  const hashes = [];
  for (const [, attrs, code] of html.matchAll(SCRIPT)) {
    if (/\bsrc=/.test(attrs) || isData(attrs) || !code) continue;
    hashes.push(`'sha256-${createHash("sha256").update(code, "utf8").digest("base64")}'`);
  }
  return [...new Set(hashes)];
}

function policy(hashes) {
  return [
    "default-src 'self'",
    `script-src 'self' ${hashes.join(" ")}`.trim(),
    // Style attributes in the exported markup (the hero graphic) need this; scripts stay strict.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");
}

let failed = 0;
const files = pages(out);
for (const file of files) {
  const name = file.slice(out.length);
  let html = readFileSync(file, "utf8");
  const current = html.match(META)?.[1];
  const wanted = policy(inlineHashes(html.replace(META, "")));
  if (check) {
    const firstScript = html.search(/<script\b/);
    const at = html.search(META);
    if (current === undefined) { console.error(`${name}: no Content-Security-Policy`); failed++; }
    else if (current !== wanted) { console.error(`${name}: the policy does not match the page's inline scripts`); failed++; }
    else if (at > firstScript) { console.error(`${name}: the policy comes after a script`); failed++; }
    else if (/script-src[^;]*'unsafe-inline'/.test(current)) { console.error(`${name}: scripts allow 'unsafe-inline'`); failed++; }
    continue;
  }
  html = html.replace(META, "");
  const tag = `<meta http-equiv="Content-Security-Policy" content="${wanted}"/>`;
  // Right after the charset declaration, before anything that loads or runs.
  const charset = html.match(/<meta charSet="[^"]*"\/>/i);
  if (charset) html = html.replace(charset[0], charset[0] + tag);
  else if (html.includes("<head>")) html = html.replace("<head>", "<head>" + tag);
  else { console.error(`${name}: no <head>`); failed++; continue; }
  writeFileSync(file, html);
}

if (failed) process.exit(1);
console.log(check ? `Content-Security-Policy covers the inline scripts on ${files.length} pages.` : `Content-Security-Policy written into ${files.length} pages.`);
