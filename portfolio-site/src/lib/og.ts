// Link preview images for the project pages and /calyx, rendered at build time by app/og/[image]/route.tsx.
// The home page keeps /og.png.
import { calyx } from "@/data/calyx";
import { projectPages } from "@/data/projects";
import { site } from "@/data/site";

export type OgCard = { id: string; path: string; kicker: string; title: string; metric: [string, string] };

// The headline number for each image, picked from the page's own numbers so the two never drift apart.
const headline: Record<string, string> = {
  "call-qa-agent": "67/67",
  "n8n-leads": "1.8 to 3.0 s",
  "rag-mcp": "67/67",
  calyx: "2,000+",
};

const pick = (id: string, numbers: [string, string][]) => {
  const n = numbers.find(([v]) => v === headline[id]);
  if (!n) throw new Error(`OG image for ${id}: no number "${headline[id]}" on the page`);
  return n;
};

export const ogCards: OgCard[] = [
  ...projectPages.map((p) => ({ id: p.slug, path: `/projects/${p.slug}`, kicker: "Project", title: p.title, metric: pick(p.slug, p.numbers) })),
  { id: "calyx", path: "/calyx", kicker: "Case study", title: `${calyx.title} case study`, metric: pick("calyx", calyx.numbers) },
];

export const ogImageUrl = (id: string) => `/og/${id}.png`;

// openGraph.images and twitter.images for a page.
export function ogImages(id: string, alt: string) {
  const url = ogImageUrl(id);
  return {
    openGraph: [{ url, width: 1200, height: 630, alt, type: "image/png" }],
    twitter: [{ url, alt }],
  };
}

export const ogAlt = (c: OgCard) => `${c.title} by ${site.name}: ${c.metric[0]} ${c.metric[1]}`;
