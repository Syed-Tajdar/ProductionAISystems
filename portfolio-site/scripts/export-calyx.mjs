// Prints the built /calyx page to public/calyx-ai-case-study.pdf (and out/) and writes a light-theme copy
// of its architecture diagram to ../calyx-case-study/architecture.svg.
// Needs `npm run build` first and Playwright with Chromium (npx playwright install chromium).
import { createReadStream, copyFileSync, existsSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { extname, join } from "node:path";

const { chromium } = createRequire(import.meta.url)("playwright");
const out = new URL("../out/", import.meta.url).pathname;
const pdfPath = new URL("../public/calyx-ai-case-study.pdf", import.meta.url).pathname;
const svgPath = new URL("../../calyx-case-study/architecture.svg", import.meta.url).pathname;
if (!existsSync(join(out, "calyx.html"))) {
  console.error("out/calyx.html not found. Run `npm run build` first.");
  process.exit(1);
}

const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".pdf": "application/pdf" };
const server = createServer((req, res) => {
  let path = decodeURIComponent(req.url.split("?")[0]);
  if (!extname(path)) path = path === "/" ? "/index.html" : `${path}.html`;
  const file = join(out, path);
  if (!file.startsWith(out) || !existsSync(file) || !statSync(file).isFile()) return res.writeHead(404).end();
  res.writeHead(200, { "content-type": types[extname(file)] ?? "application/octet-stream" });
  createReadStream(file).pipe(res);
}).listen(0);
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch();
const page = await browser.newPage({ colorScheme: "light" });
await page.addInitScript(() => localStorage.setItem("theme", "light"));
await page.goto(`${base}/calyx`, { waitUntil: "networkidle" });
await page.emulateMedia({ media: "print" });
await page.pdf({
  path: pdfPath, format: "A4", printBackground: true, preferCSSPageSize: true, displayHeaderFooter: true,
  headerTemplate: "<span></span>",
  footerTemplate: `<div style="width:100%;font:8px ui-sans-serif,system-ui,sans-serif;color:#6b7280;padding:0 16mm;display:flex;justify-content:space-between">
    <span>Calyx AI case study, Syed Tajdar</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`,
});
copyFileSync(pdfPath, join(out, "calyx-ai-case-study.pdf"));

// The diagram uses CSS variables; resolve them to the light theme for a standalone file.
const svg = await page.$eval("figure svg", (el) => {
  const vars = getComputedStyle(document.documentElement);
  const clone = el.cloneNode(true);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.removeAttribute("class");
  let markup = clone.outerHTML;
  for (const name of ["--bg", "--surface", "--fg", "--muted", "--line", "--accent", "--accent-soft", "--diagram-canvas", "--diagram-box", "--diagram-line", "--diagram-text", "--diagram-sub", "--font-sans", "--font-mono"]) {
    markup = markup.replaceAll(`var(${name})`, vars.getPropertyValue(name).trim().replaceAll('"', "'"));
  }
  return markup;
});
writeFileSync(svgPath, `<?xml version="1.0" encoding="UTF-8"?>\n${svg.replace("<svg ", '<svg style="background:#ffffff" ')}\n`);

await browser.close();
server.close();
console.log(`wrote ${pdfPath}, out/calyx-ai-case-study.pdf and ${svgPath}`);
