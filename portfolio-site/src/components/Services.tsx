import { glossary, services } from "@/data/site";
import { PipelineGraphic } from "./PipelineGraphic";
import { Section } from "./Section";

// A skill tag in a plain-language block: technical names carry their short explanation next to them.
export function SkillTag({ name }: { name: string }) {
  const gloss = glossary[name];
  return (
    <li className="rounded-md border border-line px-1.5 py-px font-mono text-[11px] text-muted">
      {gloss ? (
        <span data-explained>
          {name} <span className="font-sans">({gloss})</span>
        </span>
      ) : (
        name
      )}
    </li>
  );
}

export function Services() {
  return (
    <Section id="services" index="01" title="What I can automate for your team" intro="Each one is backed by a working build on this page." wide>
      <ul className="grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-3" data-plain="services">
        {services.map((sv) => (
          <li key={sv.id} id={`service-${sv.id}`} className="flex scroll-mt-20 flex-col bg-bg px-4 py-3.5 sm:p-6">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <h3 className="font-semibold tracking-tight">{sv.title}</h3>
              <ul className="flex flex-wrap gap-1" aria-label={`Industries for ${sv.title}`}>
                {sv.industries.map((ind) => (
                  <li key={ind} className="rounded-full bg-accent-soft px-2 py-px text-[11.5px] font-medium text-accent">
                    {ind}
                  </li>
                ))}
              </ul>
            </div>
            <p className="mt-1 text-[14.5px] leading-snug text-muted">{sv.body}</p>
            <p className="mt-1.5 text-[13.5px] leading-snug">
              <span className="text-muted">See it working: </span>
              {sv.proof.map((l, i) => (
                <span key={l.label}>
                  {i > 0 && <span className="text-muted">, </span>}
                  <a href={l.href} className="text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent">
                    {l.label}
                  </a>
                </span>
              ))}
            </p>
            <details className="group mt-1.5 sm:mt-auto sm:pt-3">
              <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-[12.5px] text-muted hover:text-fg [&::-webkit-details-marker]:hidden">
                <span className="inline-block w-2.5 font-mono text-accent group-open:hidden">+</span>
                <span className="hidden w-2.5 font-mono text-accent group-open:inline-block">&minus;</span>
                Skills used
              </summary>
              <ul className="mt-1.5 flex flex-wrap gap-1" aria-label={`Skills for ${sv.title}`}>
                {sv.skills.map((k) => (
                  <SkillTag key={k} name={k} />
                ))}
              </ul>
            </details>
          </li>
        ))}
      </ul>
      <div className="mt-6 hidden max-w-xl lg:block">
        <PipelineGraphic />
      </div>
    </Section>
  );
}
