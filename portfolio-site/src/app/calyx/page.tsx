import type { Metadata } from "next";
import { CalyxDiagram } from "@/components/CalyxDiagram";
import { Footer } from "@/components/Footer";
import { JsonLd } from "@/components/JsonLd";
import { PageHeader } from "@/components/PageHeader";
import { calyx } from "@/data/calyx";
import { site } from "@/data/site";
import { ogAlt, ogCards, ogImages } from "@/lib/og";
import { calyxWork, faqPage } from "@/lib/schema";
import { PlainWords, Questions } from "@/components/ProjectExtras";

const title = "Calyx AI case study: call analytics in Go";
const images = ogImages("calyx", ogAlt(ogCards.find((c) => c.id === "calyx")!));
const description = "Calyx AI: multi-tenant call analytics for pay-per-call networks, built end to end in Go with ClickHouse, Kafka and an AI layer. 2,000+ calls a day.";

export const metadata: Metadata = {
  title,
  description,
  keywords: ["call analytics", "pay-per-call", "call tracking integration", "Go", "ClickHouse", "AI automation engineer"],
  openGraph: { type: "article", url: "/calyx", siteName: site.name, title, description, images: images.openGraph },
  twitter: { card: "summary_large_image", title, description, images: images.twitter },
  alternates: { canonical: "/calyx" },
};

function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="mt-14 scroll-mt-20 text-xl font-semibold tracking-tight sm:text-2xl print:mt-8 print:break-after-avoid">
      {children}
    </h2>
  );
}

export default function CalyxPage() {
  return (
    <>
      <JsonLd data={{ "@context": "https://schema.org", "@graph": [calyxWork, faqPage("/calyx", calyx.faq)] }} />
      <PageHeader />

      <main id="top" className="mx-auto max-w-3xl px-5 pb-20 pt-14 sm:px-8 sm:pt-20 print:max-w-none print:px-0 print:pt-0">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">Case study</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-[-0.03em] sm:text-5xl">{calyx.title}</h1>
        <p className="mt-3 text-lg text-muted sm:text-xl">{calyx.subtitle}</p>
        <div className="print:hidden">
          <PlainWords plain={calyx.plain} />
        </div>
        <p className="mt-2 hidden text-sm text-muted print:block">
          {site.name}, {site.title} | {site.email}
        </p>

        <dl className="mt-10 grid gap-4 border-y border-line py-6 text-[15px] print:mt-6 print:py-4">
          {calyx.facts.map(([k, v]) => (
            <div key={k} className="grid gap-1 sm:grid-cols-[6rem_1fr] sm:gap-4">
              <dt className="font-mono text-xs uppercase tracking-widest text-muted sm:pt-1">{k}</dt>
              <dd className="leading-relaxed">{v}</dd>
            </div>
          ))}
        </dl>

        <ul className="mt-8 grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2 print:break-inside-avoid">
          {calyx.numbers.map(([n, what]) => (
            <li key={n} className="bg-bg px-5 py-4">
              <p className="text-2xl font-semibold tracking-tight text-accent">{n}</p>
              <p className="mt-1 text-sm leading-snug text-muted">{what}</p>
            </li>
          ))}
        </ul>

        <p className="mt-8 print:hidden">
          <a href={calyx.pdf} download className="text-sm text-accent underline-offset-4 hover:underline">
            Download as PDF
          </a>
        </p>

        <H2 id="problem">The problem</H2>
        <p className="mt-4 text-[16px] leading-relaxed text-muted">{calyx.problem}</p>

        <H2 id="architecture">Architecture</H2>
        <div className="mt-5 print:break-inside-avoid">
          <CalyxDiagram />
        </div>

        {/* The PDF leaves the screens out: the heading and images are for the web page only. */}
        <div className="print:hidden">
        <H2 id="screens">Screens from the running system (financial figures blurred)</H2>
        <ul className="mt-5 grid gap-5">
          {calyx.screens.map((s) => (
            <li key={s.large}>
              <figure>
                <a href={s.large} className="block overflow-hidden rounded-lg border border-line transition-colors hover:border-accent/60">
                  <picture>
                    <source type="image/webp" srcSet={`${s.small} 720w, ${s.large} ${s.width}w`} sizes="(min-width: 768px) 46rem, 100vw" />
                    { }
                    <img src={s.small} width={s.width} height={s.height} alt={s.alt} loading="lazy" className="block h-auto w-full" />
                  </picture>
                </a>
                <figcaption className="mt-2 font-mono text-xs text-muted">{s.caption}</figcaption>
              </figure>
            </li>
          ))}
        </ul>
        </div>

        <H2 id="built">What I built</H2>
        <div className="mt-5 grid gap-5">
          {calyx.built.map((b) => (
            <section key={b.title} className="print:break-inside-avoid">
              <h3 className="font-semibold">{b.title}</h3>
              <p className="mt-1.5 leading-relaxed text-muted">{b.body}</p>
            </section>
          ))}
        </div>

        <H2 id="how">How I worked</H2>
        <p className="mt-4 leading-relaxed text-muted">{calyx.howIWorked}</p>

        <H2 id="hard-problems">Hard problems solved</H2>
        <ol className="mt-5 grid gap-4">
          {calyx.hardProblems.map((p, i) => (
            <li key={p.title} className="grid grid-cols-[2rem_1fr] print:break-inside-avoid">
              <span className="font-mono text-sm text-accent">{String(i + 1).padStart(2, "0")}</span>
              <div>
                <h3 className="font-semibold">{p.title}</h3>
                <p className="mt-1 leading-relaxed text-muted">{p.body}</p>
              </div>
            </li>
          ))}
        </ol>

        <H2 id="results">Results</H2>
        <ul className="mt-4 grid gap-2">
          {calyx.results.map((r) => (
            <li key={r} className="flex gap-3 leading-relaxed">
              <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
              <span>{r}</span>
            </li>
          ))}
        </ul>

        <div className="print:hidden">
          <Questions faq={calyx.faq} />
        </div>

        <p className="mt-12 border-t border-line pt-5 text-sm text-muted print:hidden">{calyx.note}</p>
        <p className="mt-12 hidden border-t border-line pt-5 text-sm text-muted print:block">{calyx.printNote}</p>
      </main>
      <div className="print:hidden">
        <Footer />
      </div>
    </>
  );
}
