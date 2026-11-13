import { calyx } from "@/data/calyx";
import { orderedProjectPages, type ProjectPage } from "@/data/projects";
import { githubHref, site } from "@/data/site";
import { ogImageUrl } from "./og";
import { absolute } from "./site-url";

export const personId = absolute("/#person");

export const person = {
  "@type": "Person",
  "@id": personId,
  name: site.name,
  alternateName: site.shortName,
  jobTitle: site.title,
  description: site.pitch,
  url: absolute("/"),
  email: `mailto:${site.email}`,
  address: { "@type": "PostalAddress", addressLocality: "Islamabad", addressCountry: "PK" },
  knowsLanguage: ["en", "ur"], // English and Urdu; the systems are built for Arabic and English
  knowsAbout: [
    "AI automation",
    "LLM agents",
    "Claude API",
    "n8n workflow automation",
    "Retrieval-augmented generation (RAG)",
    "Arabic and English NLP",
    "Model Context Protocol (MCP)",
    "LLM evaluation",
    "Call QA automation",
    "Speech-to-text",
    "Python",
    "Go",
    "PostgreSQL",
    "pgvector",
    "ClickHouse",
  ],
  sameAs: [site.linkedin, ...(githubHref ? [githubHref] : [])],
};

const author = { "@type": "Person", "@id": personId, name: site.name, url: absolute("/") };

export function sourceCode(p: ProjectPage) {
  return {
    "@type": "SoftwareSourceCode",
    "@id": absolute(`/projects/${p.slug}#project`),
    name: p.title,
    description: p.metaDescription,
    url: absolute(`/projects/${p.slug}`),
    image: absolute(ogImageUrl(p.slug)),
    ...(p.code ? { codeRepository: p.code } : {}),
    programmingLanguage: p.languages,
    keywords: p.keywords.join(", "),
    author,
    // The running demo of this code, when there is one.
    ...(p.demo ? { targetProduct: { "@type": "WebApplication", name: `${p.title} live demo`, url: p.demo,
                                    applicationCategory: "BusinessApplication", operatingSystem: "Web" } } : {}),
  };
}

export const calyxWork = {
  "@type": "CreativeWork",
  "@id": absolute("/calyx#case-study"),
  name: `${calyx.title} case study`,
  headline: `${calyx.title}: ${calyx.subtitle}`,
  description: `${calyx.subtitle}. Built end to end as the sole engineer: Go services, ClickHouse, Kafka, Redis and an AI layer.`,
  url: absolute("/calyx"),
  image: absolute(ogImageUrl("calyx")),
  keywords: "call analytics, pay-per-call, Go, ClickHouse, Kafka, multi-tenant, AI",
  author,
};

export const homeGraph = {
  "@context": "https://schema.org",
  "@graph": [
    { "@type": "WebSite", "@id": absolute("/#website"), url: absolute("/"), name: site.name, publisher: { "@id": personId }, inLanguage: "en" },
    { ...person, mainEntityOfPage: absolute("/") },
    calyxWork,
    ...orderedProjectPages.map(sourceCode),
  ],
};

// FAQPage for a page's "Questions you might ask".
export function faqPage(url: string, faq: { q: string; a: string }[]) {
  return {
    "@type": "FAQPage",
    "@id": `${absolute(url)}#questions`,
    mainEntity: faq.map((f) => ({ "@type": "Question", name: f.q, acceptedAnswer: { "@type": "Answer", text: f.a } })),
  };
}
