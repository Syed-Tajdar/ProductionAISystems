import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Footer } from "@/components/Footer";
import { JsonLd } from "@/components/JsonLd";
import { PageHeader } from "@/components/PageHeader";
import { CostCalculator } from "@/components/CostCalculator";
import { PlainWords, Questions } from "@/components/ProjectExtras";
import { ProjectVisual } from "@/components/ProjectVisual";
import { orderedProjectPages, projectPage, projectPages } from "@/data/projects";
import { site } from "@/data/site";
import { ogAlt, ogCards, ogImages } from "@/lib/og";
import { faqPage, sourceCode } from "@/lib/schema";
import { absolute } from "@/lib/site-url";

export const dynamicParams = false;

export function generateStaticParams() {
  return projectPages.map((p) => ({ slug: p.slug }));
}

type Props = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const p = projectPage((await params).slug);
  if (!p) return {};
  const url = `/projects/${p.slug}`;
  const images = ogImages(p.slug, ogAlt(ogCards.find((c) => c.id === p.slug)!));
  return {
    title: p.metaTitle,
    description: p.metaDescription,
    keywords: p.keywords,
    alternates: { canonical: url },
    openGraph: { type: "article", url, siteName: site.name, title: p.metaTitle, description: p.metaDescription, images: images.openGraph },
    twitter: { card: "summary_large_image", title: p.metaTitle, description: p.metaDescription, images: images.twitter },
  };
}

function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="mt-14 scroll-mt-20 text-xl font-semibold tracking-tight sm:text-2xl">
      {children}
    </h2>
  );
}

export default async function ProjectPage({ params }: Props) {
  const p = projectPage((await params).slug);
  if (!p) notFound();

  const breadcrumbs = {
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "Projects", item: absolute("/#projects") },
      { "@type": "ListItem", position: 2, name: p.title, item: absolute(`/projects/${p.slug}`) },
    ],
  };

  return (
    <>
      <JsonLd data={{ "@context": "https://schema.org", "@graph": [sourceCode(p), breadcrumbs, faqPage(`/projects/${p.slug}`, p.faq)] }} />
      <PageHeader />

      <main id="top" className="mx-auto max-w-3xl px-5 pb-20 pt-14 sm:px-8 sm:pt-20">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">Project</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-[-0.03em] sm:text-5xl">{p.title}</h1>
        <p className="mt-3 text-lg text-muted sm:text-xl">{p.subtitle}</p>
        <PlainWords plain={p.plain} />

        <dl className="mt-10 grid gap-4 border-y border-line py-6 text-[15px]">
          {p.facts.map(([k, v]) => (
            <div key={k} className="grid gap-1 sm:grid-cols-[6rem_1fr] sm:gap-4">
              <dt className="font-mono text-xs uppercase tracking-widest text-muted sm:pt-1">{k}</dt>
              <dd className="leading-relaxed">{v}</dd>
            </div>
          ))}
        </dl>

        <ul className={`mt-8 grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2 ${p.numbers.length === 3 ? "lg:grid-cols-3" : ""}`}>
          {p.numbers.map(([n, what]) => (
            <li key={n} className="bg-bg px-5 py-4">
              <p className="text-2xl font-semibold tracking-tight text-accent">{n}</p>
              <p className="mt-1 text-sm leading-snug text-muted">{what}</p>
            </li>
          ))}
        </ul>

        {(p.code || p.readme || p.demo) && (
          <p className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
            {p.code && (
              <a href={p.code} target="_blank" rel="noopener noreferrer" className="font-medium text-accent underline-offset-4 hover:underline">
                Code on GitHub
              </a>
            )}
            {p.readme && (
              <a href={p.readme} target="_blank" rel="noopener noreferrer" className="text-accent underline-offset-4 hover:underline">
                Full README
              </a>
            )}
            {p.demo && (
              <a href={p.demo} target="_blank" rel="noopener" className="font-medium text-accent underline-offset-4 hover:underline">
                Live demo <span aria-hidden="true">&#8599;</span>
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            )}
          </p>
        )}

        {p.slug === "call-qa-agent" && <CostCalculator />}

        <H2 id="problem">The problem</H2>
        <p className="mt-4 text-[16px] leading-relaxed text-muted">{p.problem}</p>

        <H2 id="architecture">Architecture</H2>
        <div className="mt-5 overflow-hidden rounded-xl border border-line">
          <ProjectVisual id={p.slug} />
        </div>
        <ol className="mt-6 grid gap-0">
          {p.flow.map((s, i) => (
            <li key={s.title} className="relative grid grid-cols-[2rem_1fr] gap-3 pb-5 last:pb-0">
              {i < p.flow.length - 1 && <span className="absolute bottom-0 left-[0.95rem] top-8 w-px bg-line" aria-hidden="true" />}
              <span className="grid size-8 place-items-center rounded-full border border-accent/50 bg-accent-soft font-mono text-xs text-accent">{i + 1}</span>
              <div className="pt-1">
                <h3 className="font-semibold">{s.title}</h3>
                <p className="mt-0.5 leading-relaxed text-muted">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
        <div className="mt-6 rounded-xl border border-dashed border-line p-5">
          <p className="font-mono text-xs uppercase tracking-widest text-muted">When something fails</p>
          <ul className="mt-3 grid gap-2">
            {p.guards.map((g) => (
              <li key={g} className="flex gap-3 leading-relaxed">
                <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
                <span>{g}</span>
              </li>
            ))}
          </ul>
        </div>

        <H2 id="decisions">Key decisions</H2>
        <div className="mt-5 grid gap-5">
          {p.decisions.map((d) => (
            <section key={d.title}>
              <h3 className="font-semibold">{d.title}</h3>
              <p className="mt-1.5 leading-relaxed text-muted">{d.body}</p>
            </section>
          ))}
        </div>

        <H2 id="results">Eval results</H2>
        <p className="mt-4 leading-relaxed text-muted">{p.resultsIntro}</p>
        {!Array.isArray(p.results) && (
          <dl className="mt-5 divide-y divide-line rounded-xl border border-line sm:hidden">
            {p.results.rows.map(([k, ...vs]) => (
              <div key={k} className="px-4 py-3">
                <dt className="text-[14.5px] text-muted">{k}</dt>
                <dd className="mt-1 flex flex-wrap gap-x-4 text-[14.5px] tabular-nums">
                  {vs.map((v, i) => (
                    <span key={i} className={i === vs.length - 1 ? "font-medium" : "text-muted"}>
                      <span className="font-mono text-[11px] uppercase tracking-widest text-muted">{(p.results as { head: string[] }).head[i + 1]}</span> {v}
                    </span>
                  ))}
                </dd>
              </div>
            ))}
          </dl>
        )}
        <div className={`mt-5 overflow-x-auto rounded-xl border border-line ${Array.isArray(p.results) ? "" : "hidden sm:block"}`}>
          <table className="w-full text-left text-[14.5px]">
            {Array.isArray(p.results) ? (
              <>
                <thead className="sr-only">
                  <tr>
                    <th scope="col">Metric</th>
                    <th scope="col">Result</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {p.results.map(([k, v]) => (
                    <tr key={k}>
                      <th scope="row" className="px-4 py-2.5 font-normal text-muted">{k}</th>
                      <td className="px-4 py-2.5 font-medium tabular-nums">{v}</td>
                    </tr>
                  ))}
                </tbody>
              </>
            ) : (
              <>
                <thead className="border-b border-line bg-surface">
                  <tr>
                    {p.results.head.map((h) => (
                      <th key={h} scope="col" className="px-4 py-2.5 font-mono text-[11px] font-normal uppercase tracking-widest text-muted">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {p.results.rows.map(([k, ...vs]) => (
                    <tr key={k}>
                      <th scope="row" className="px-4 py-2.5 font-normal text-muted">{k}</th>
                      {vs.map((v, i) => (
                        <td key={i} className={`whitespace-nowrap px-4 py-2.5 tabular-nums ${i === vs.length - 1 ? "font-medium" : "text-muted"}`}>
                          {v}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </>
            )}
          </table>
        </div>

        <h3 className="mt-8 font-semibold">Scope of these numbers</h3>
        <ul className="mt-3 grid gap-2">
          {p.scope.map((s) => (
            <li key={s} className="flex gap-3 leading-relaxed text-muted">
              <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-line" aria-hidden="true" />
              <span>{s}</span>
            </li>
          ))}
        </ul>

        <Questions faq={p.faq} />

        <nav aria-label="Other projects" className="mt-14 border-t border-line pt-6">
          <p className="font-mono text-xs uppercase tracking-widest text-muted">More projects</p>
          <ul className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
            {[...orderedProjectPages.filter((o) => o.slug !== p.slug).map((o) => ({ href: `/projects/${o.slug}`, label: o.title })), { href: "/calyx", label: "Calyx AI case study" }].map((l) => (
              <li key={l.href}>
                <a href={l.href} className="text-accent underline-offset-4 hover:underline">
                  {l.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </main>
      <Footer />
    </>
  );
}
