import { orderedProjects } from "@/data/site";
import { ProjectCard } from "./ProjectCard";
import { Section } from "./Section";

export function Projects() {
  return (
    <Section id="projects" index="02" title="Projects" intro="Production work and production-style builds you can try, each with measured results and deploy steps." wide>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {orderedProjects.map((p, i) => (
          <div key={p.id} className={p.featured ? "md:col-span-2 lg:col-span-3" : "flex md:last:col-span-2 lg:last:col-span-1 [&>article]:w-full"}>
            <ProjectCard project={p} index={i} />
          </div>
        ))}
      </div>
    </Section>
  );
}
