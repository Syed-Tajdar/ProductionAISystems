import { calyx } from "@/data/calyx";
import type { Badge, Project } from "@/data/site";
import { isSet } from "@/data/site";
import { ProjectVisual } from "./ProjectVisual";
import { TextLink } from "./TextLink";

function Links({ project }: { project: Project }) {
  const pageLabel = project.page.startsWith("/projects/") ? "Project page" : "Case study";
  return (
    <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-3 border-t border-line pt-4 text-sm">
      {isSet(project.demo) && (
        <a
          href={project.demo}
          target="_blank"
          rel="noopener"
          className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-accent px-4 font-medium text-accent-fg transition-[transform,filter] duration-150 hover:-translate-y-px hover:brightness-110"
        >
          Try it live <span aria-hidden="true">&#8599;</span>
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      )}
      <TextLink href={project.page} className="font-medium">
        {pageLabel}
      </TextLink>
      {project.code && <TextLink href={project.code}>Code</TextLink>}
      {project.extra?.map((l) => (
        <TextLink key={l.label} href={l.href}>
          {l.label}
        </TextLink>
      ))}
    </div>
  );
}

function Plain({ project }: { project: Project }) {
  return (
    <div data-plain="card">
      <p className="mt-3 text-[15.5px] leading-relaxed">
        <span className="font-semibold">In plain words: </span>
        {project.plain}
      </p>
      <ul className="mt-3.5 flex flex-wrap gap-1.5" aria-label="Results">
        {project.metrics.map((m: Badge) => (
          <li key={m.plain} title={m.technical} className="rounded-md bg-accent-soft px-2 py-1 text-[12.5px] font-medium text-accent">
            {m.plain}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Stack({ items }: { items: string[] }) {
  return (
    <ul className="mt-4 flex flex-wrap gap-1" aria-label="Stack">
      {items.map((s) => (
        <li key={s} className="rounded border border-line px-1.5 py-px font-mono text-[10.5px] text-muted">
          {s}
        </li>
      ))}
    </ul>
  );
}

const card =
  "group flex flex-col rounded-xl border border-line bg-surface p-6 transition-[border-color,transform,box-shadow] duration-200 hover:border-accent/50 hover:shadow-[0_10px_30px_-14px_rgb(0_0_0/0.18)] motion-safe:hover:-translate-y-1 sm:p-7";

function CalyxScreen() {
  const s = calyx.screens[0];
  return (
    <picture>
      <source type="image/webp" srcSet={`${s.small} 720w, ${s.large} 1440w`} sizes="(min-width: 1024px) 34rem, 100vw" />
      { }
      <img src={s.small} width={s.width} height={s.height} alt={s.alt} loading="lazy" className="block h-auto w-full" />
    </picture>
  );
}

export function ProjectCard({ project, index }: { project: Project; index: number }) {
  const number = String(index + 1).padStart(2, "0");
  const title = (
    <a href={project.page} className="transition-colors hover:text-accent">
      {project.title}
    </a>
  );

  if (project.featured) {
    return (
      <article className={`${card} lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] lg:gap-10`}>
        <div className="flex flex-col">
          <p className="flex items-baseline gap-3 font-mono text-xs text-muted">
            <span>{number}</span>
            <span className="uppercase tracking-widest text-accent">Featured, in production</span>
          </p>
          <h3 className="mt-4 text-2xl font-semibold tracking-tight sm:text-[1.7rem]">{title}</h3>
          <Plain project={project} />
          <p className="mt-4 text-sm leading-relaxed text-muted">{project.problem}</p>
          <Links project={project} />
          <Stack items={project.stack} />
        </div>
        <figure className="order-first mb-6 overflow-hidden rounded-lg border border-line lg:order-none lg:mb-0 lg:self-center">
          {project.id === "calyx" ? <CalyxScreen /> : <ProjectVisual id={project.id} />}
          {project.id === "calyx" && (
            <figcaption className="border-t border-line px-3 py-2 font-mono text-[11px] text-muted">Running system, financial figures blurred</figcaption>
          )}
        </figure>
      </article>
    );
  }

  return (
    <article className={card}>
      <div className="mb-5 overflow-hidden rounded-lg border border-line">
        <ProjectVisual id={project.id} />
      </div>
      <span className="font-mono text-xs text-muted">{number}</span>
      <h3 className="mt-2 text-xl font-semibold tracking-tight">{title}</h3>
      <Plain project={project} />
      <p className="mt-4 text-sm leading-relaxed text-muted">{project.problem}</p>
      <div className="mt-auto">
        <Links project={project} />
        <Stack items={project.stack} />
      </div>
    </article>
  );
}
