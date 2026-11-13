import { servicesForGroup, skills } from "@/data/site";
import { Section } from "./Section";

export function Skills() {
  return (
    <Section id="skills" index="04" title="Skills">
      <dl className="divide-y divide-line">
        {skills.map((g) => (
          <div key={g.group} className="grid gap-3 py-5 first:pt-0 last:pb-0 sm:grid-cols-[10rem_1fr] sm:gap-6">
            <dt className="pt-1 text-sm font-medium">{g.group}</dt>
            <dd>
              <ul className="flex flex-wrap gap-2">
                {g.items.map((s) => (
                  <li
                    key={s}
                    className="rounded-full border border-line px-3 py-1 text-sm text-muted transition-colors hover:border-accent hover:text-fg"
                  >
                    {s}
                  </li>
                ))}
              </ul>
              {servicesForGroup(g.group).length > 0 && (
                <p className="mt-2.5 text-sm text-muted">
                  Used for:{" "}
                  {servicesForGroup(g.group).map((sv, i) => (
                    <span key={sv.id}>
                      {i > 0 && ", "}
                      <a href={`#service-${sv.id}`} className="text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent">
                        {sv.title}
                      </a>
                    </span>
                  ))}
                </p>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}
