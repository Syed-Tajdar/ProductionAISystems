// Region profiles: the parts of the site that change with the target market. SITE_REGION picks one at
// build time (default "gulf"). To add a region, add an entry here; nothing else needs to change.

export type RegionProfile = {
  // Optional second line under the English tagline, in another language.
  secondLine?: { text: string; lang: string; dir: "ltr" | "rtl" };
  // Shown after the location in Contact and the recruiter block.
  relocation: string;
  relocationChip?: string; // shorter wording for the hero chip; the full sentence is used when absent
  seo: {
    description: string; // home page meta description, about 155 characters
    keywords: string[]; // added to the shared keywords
  };
  // Order of the project cards, by project id. Must list every project once.
  projectOrder: string[];
};

export const regions = {
  gulf: {
    secondLine: {
      text: "أبني وكلاء ذكاء اصطناعي وحلول أتمتة مرتبطة بأنظمة الأعمال الفعلية، بالعربية والإنجليزية.",
      lang: "ar",
      dir: "rtl",
    },
    relocation: "Open to relocate to UAE, KSA or Qatar, or remote",
    relocationChip: "Open to UAE, KSA, Qatar or remote",
    seo: {
      description:
        "AI Automation Engineer: I turn manual business processes into production AI systems, agents, workflows and integrations, for teams in the UAE, KSA and Qatar.",
      keywords: ["Arabic", "Arabic RAG", "bilingual AI", "UAE", "Dubai", "Saudi Arabia", "Qatar", "AI engineer UAE", "n8n developer Dubai"],
    },
    projectOrder: ["calyx", "rag-mcp", "call-qa-agent", "n8n-leads"],
  },
  global: {
    relocation: "Open to remote work and relocation",
    seo: {
      description:
        "AI Automation Engineer: I turn manual business processes into production AI systems, agents, workflows and integrations. Open to remote work.",
      keywords: ["remote AI engineer", "LLM agents", "workflow automation"],
    },
    projectOrder: ["calyx", "call-qa-agent", "n8n-leads", "rag-mcp"],
  },
} satisfies Record<string, RegionProfile>;

export type RegionName = keyof typeof regions;

const requested = (process.env.SITE_REGION ?? "").trim() || "gulf";
if (!(requested in regions)) {
  throw new Error(`Unknown SITE_REGION "${requested}". Use one of: ${Object.keys(regions).join(", ")}.`);
}

export const regionName = requested as RegionName;
export const region: RegionProfile = regions[regionName];

// Sorts items by the region's project order; ids not in the list keep their place at the end.
export function inRegionOrder<T>(items: T[], id: (item: T) => string): T[] {
  const rank = (item: T) => {
    const i = region.projectOrder.indexOf(id(item));
    return i === -1 ? Infinity : i;
  };
  return [...items].sort((a, b) => rank(a) - rank(b));
}
