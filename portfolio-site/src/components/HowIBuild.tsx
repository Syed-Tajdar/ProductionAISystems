import { approach } from "@/data/site";
import { Section } from "./Section";

const icons = [
  "M4 19V5M4 19h16M8 15l3-4 3 2 5-6", // evals: a rising chart
  "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3Z M9 12l2 2 4-4", // guardrails: shield with check
  "M12 3v18M16 7.5c0-1.9-1.8-3-4-3s-4 1.1-4 3 1.8 2.6 4 3 4 1.1 4 3-1.8 3-4 3-4-1.1-4-3", // cost
  "M4 6h9M8.5 4v2M6 6c.5 3 2.5 5.5 6 7M11 6c-.8 3.5-3.4 6.4-7 8M13 20l4-9 4 9M14.5 17h5", // languages
];

export function HowIBuild() {
  return (
    <Section id="approach" index="07" title="How I build" intro="The same rules in every project, so the results can be trusted.">
      <ul className="grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2">
        {approach.map((a, i) => (
          <li key={a.title} className="bg-bg p-5 sm:p-6">
            <svg viewBox="0 0 24 24" className="size-5 text-accent" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d={icons[i]} />
            </svg>
            <div data-plain="approach">
              <h3 className="mt-3 font-semibold tracking-tight">{a.title}</h3>
              <p className="mt-1.5 text-[15px] leading-relaxed">{a.plain}</p>
            </div>
            <p className="mt-2 text-[13px] leading-relaxed text-muted">{a.body}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}
