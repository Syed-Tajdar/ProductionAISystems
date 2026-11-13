// Base URL for metadata, canonical links, the sitemap and JSON-LD. The domain is not final, so it comes
// from NEXT_PUBLIC_SITE_URL, then Vercel's production URL, then localhost.
export const siteUrl = (
  process.env.NEXT_PUBLIC_SITE_URL ??
  (process.env.VERCEL_PROJECT_PRODUCTION_URL
    ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
    : "http://localhost:3000")
).replace(/\/$/, "");

export const absolute = (path: string) => `${siteUrl}${path === "/" ? "/" : path}`;
