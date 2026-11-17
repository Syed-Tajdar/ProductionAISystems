import { region } from "@/data/regions";
import { demos, emailHref, linkedinHref, site } from "@/data/site";
import { Section } from "./Section";
import { TextLink } from "./TextLink";
import { demoVideo } from "@/data/demo-video";

export function Recruiters() {
  const rows: [string, React.ReactNode][] = [
    ["Roles", site.roles.join(", ")],
    ["Location", `${site.location}. ${region.relocation}.`],
    ["Time zone", site.timeZone],
    ["Languages", site.languages],
    ["Experience", site.experience],
    ["Availability", site.availability],
  ];
  return (
    <Section id="recruiters" index="05" title="For recruiters" intro="The short version, with everything you need to pass this on.">
      <div data-plain="recruiters">
        <dl className="grid gap-px overflow-hidden rounded-xl border border-line bg-line">
          {rows.map(([k, v]) => (
            <div key={k} className="grid gap-1 bg-bg px-5 py-3.5 sm:grid-cols-[8rem_1fr] sm:gap-4">
              <dt className="font-mono text-[11px] uppercase tracking-widest text-muted sm:pt-0.5">{k}</dt>
              <dd className="text-[15px] leading-relaxed">{v}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-[15px]">
          <a href={site.cv} download={site.cvFileName} className="font-medium text-accent underline-offset-4 hover:underline">
            Download CV (PDF)
          </a>
          <TextLink href={emailHref}>{site.email}</TextLink>
          <TextLink href={linkedinHref}>LinkedIn</TextLink>
          <TextLink href={demoVideo.mp4}>1-minute demo video</TextLink>
          <TextLink href={demos["call-qa-agent"]} rel="noopener">Call review demo</TextLink>
          <TextLink href={demos["rag-mcp"]} rel="noopener">Document answers demo</TextLink>
          {demos["n8n-leads"] && (
            <TextLink href={demos["n8n-leads"]} rel="noopener">
              Lead sorting demo
            </TextLink>
          )}
        </p>
      </div>
    </Section>
  );
}
