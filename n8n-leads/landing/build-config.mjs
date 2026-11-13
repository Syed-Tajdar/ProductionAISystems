// Vercel build step: writes public/config.js, and the form action in public/index.html, from the
// LEAD_WEBHOOK_URL environment variable,
// https://<your-service>.onrender.com/webhook/lead-intake, with the exact host from the Render dashboard.
// Fails the build when it is missing.
import { readFileSync, writeFileSync } from "node:fs";

const url = (process.env.LEAD_WEBHOOK_URL || "").trim();
if (!/^https:\/\/[a-z0-9.-]+(:\d+)?\/webhook\/[a-z0-9-]+$/i.test(url)) {
  console.error("Set LEAD_WEBHOOK_URL to the n8n form webhook, e.g. https://<service>.onrender.com/webhook/lead-intake");
  process.exit(1);
}
writeFileSync(new URL("./public/config.js", import.meta.url), `window.LEAD_WEBHOOK_URL = ${JSON.stringify(url)};\n`);
// Without script the form posts straight to the webhook (the visitor then sees the workflow's raw answer).
const page = new URL("./public/index.html", import.meta.url);
const html = readFileSync(page, "utf8");
if (!html.includes('action="#webhook-not-set"')) {
  console.error("public/index.html has no form action placeholder");
  process.exit(1);
}
writeFileSync(page, html.replace('action="#webhook-not-set"', `action="${url}"`));
console.log(`config.js points to ${new URL(url).host}`);
