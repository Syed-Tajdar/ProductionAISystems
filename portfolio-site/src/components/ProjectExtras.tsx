import type { Faq, Plain } from "@/data/projects";

// "In plain words": three steps and why it matters, for a reader who skips the technical flow.
export function PlainWords({ plain }: { plain: Plain }) {
  return (
    <section aria-labelledby="plain-title" data-plain="plain-words" className="mt-8 rounded-xl border border-accent/30 bg-accent-soft/40 p-5 sm:p-6">
      <h2 id="plain-title" className="font-mono text-xs uppercase tracking-widest text-accent">
        In plain words
      </h2>
      <ol className="mt-3 grid gap-2">
        {plain.steps.map((step, i) => (
          <li key={step} className="grid grid-cols-[1.6rem_1fr] text-[15.5px] leading-relaxed">
            <span className="font-mono text-sm text-accent">{i + 1}.</span>
            <span>{step}</span>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-[15.5px] leading-relaxed">
        <span className="font-semibold">Why it matters: </span>
        {plain.why}
      </p>
    </section>
  );
}

// "Questions you might ask": short first-person answers; the same list is published as FAQPage JSON-LD.
export function Questions({ faq }: { faq: Faq[] }) {
  return (
    <>
      <h2 id="questions" className="mt-14 scroll-mt-20 text-xl font-semibold tracking-tight sm:text-2xl print:mt-8">
        Questions you might ask
      </h2>
      <div className="mt-5 divide-y divide-line rounded-xl border border-line">
        {faq.map((f) => (
          <details key={f.q} className="group px-5 py-4 print:break-inside-avoid" open {...(f.plain ? { "data-plain": "faq" } : {})}>
            <summary className="cursor-pointer list-none font-semibold leading-snug marker:hidden [&::-webkit-details-marker]:hidden">
              <span className="mr-2 inline-block w-3 font-mono text-accent group-open:hidden">+</span>
              <span className="mr-2 hidden w-3 font-mono text-accent group-open:inline-block">&minus;</span>
              {f.q}
            </summary>
            <p className="mt-2 pl-5 leading-relaxed text-muted">{f.a}</p>
          </details>
        ))}
      </div>
    </>
  );
}
