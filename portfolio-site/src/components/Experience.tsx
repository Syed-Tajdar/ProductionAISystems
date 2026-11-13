import { experience } from "@/data/site";
import { Section } from "./Section";

export function Experience() {
  return (
    <Section id="experience" index="03" title="Experience">
      <ol className="relative border-l border-line">
        {experience.map((job, i) => (
          <li key={i} className="relative pb-10 pl-7 last:pb-0">
            <span
              className="absolute -left-[5px] top-1.5 size-[9px] rounded-full border-2 border-bg bg-accent"
              aria-hidden="true"
            />
            <p className="font-mono text-xs text-muted">{job.dates}</p>
            <h3 className="mt-1.5 text-lg font-semibold tracking-tight">
              {job.role}
              {job.company && (
                <span className="font-normal text-muted">
                  {" "}
                  {job.connector ?? "at"} {job.company}
                </span>
              )}
              {job.place && <span className="font-normal text-muted">, {job.place}</span>}
            </h3>
            <p className="mt-1.5 max-w-2xl text-[15px] leading-relaxed text-muted">{job.summary}</p>
            {job.rebuilt?.map((r) => (
              <p key={r.after} className="mt-2 text-sm text-muted">
                {r.before}. Later rebuilt with AI:{" "}
                <a href={r.href} className="text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent">
                  {r.after}
                </a>
              </p>
            ))}
          </li>
        ))}
      </ol>
    </Section>
  );
}
