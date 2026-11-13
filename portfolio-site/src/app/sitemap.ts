import type { MetadataRoute } from "next";
import { orderedProjectPages } from "@/data/projects";
import { absolute } from "@/lib/site-url";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  return [
    { url: absolute("/"), lastModified, changeFrequency: "monthly", priority: 1 },
    { url: absolute("/calyx"), lastModified, changeFrequency: "monthly", priority: 0.8 },
    ...orderedProjectPages.map((p) => ({ url: absolute(`/projects/${p.slug}`), lastModified, changeFrequency: "monthly" as const, priority: 0.8 })),
  ];
}
