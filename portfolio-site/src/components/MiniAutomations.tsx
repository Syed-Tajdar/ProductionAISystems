import { isSet, miniAutomations } from "@/data/site";
import { Section } from "./Section";
import { TextLink } from "./TextLink";

export function MiniAutomations() {
  return (
    <Section id="automations" index="06" title="Mini Automations" intro="Small, focused tools that each remove one manual task.">
      <ul className="grid gap-3 sm:grid-cols-2">
        {miniAutomations.map((m, i) => (
          <li
            key={m.id}
            id={`mini-${m.id}`}
            className="flex scroll-mt-20 flex-col rounded-lg border border-line p-4 transition-colors duration-200 hover:border-accent/50 hover:bg-surface"
          >
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="text-[15px] font-medium">{m.title}</h3>
              <span className="shrink-0 font-mono text-[11px] text-muted">{String(i + 1).padStart(2, "0")}</span>
            </div>
            <p className="mt-1.5 text-sm leading-relaxed text-muted">{m.problem}</p>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <ul className="flex flex-wrap gap-1.5" aria-label="Stack">
                {m.stack.map((s, j) => (
                  <li key={j} className="font-mono text-[11px] text-muted">
                    {j > 0 && <span className="mr-1.5 text-line" aria-hidden="true">/</span>}
                    {s}
                  </li>
                ))}
              </ul>
              {isSet(m.github) && (
                <TextLink href={m.github} className="text-sm">
                  Code
                </TextLink>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}
