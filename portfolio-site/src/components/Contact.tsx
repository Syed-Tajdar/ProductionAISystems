import { region } from "@/data/regions";
import { emailHref, githubHref, linkedinHref, site } from "@/data/site";
import { Section } from "./Section";
import { TextLink } from "./TextLink";

export function Contact() {
  const rows = [
    { label: "LinkedIn", value: site.linkedin.replace(/^(\[?)https?:\/\/(www\.)?/, "$1"), href: linkedinHref },
    // GitHub only once the code is public (PUBLIC_CODE=true), so nothing points at a private repository.
    ...(githubHref ? [{ label: "GitHub", value: site.github.replace(/^(\[?)https?:\/\//, "$1"), href: githubHref }] : []),
  ];
  return (
    <Section id="contact" index="08" title="Contact" intro="The fastest way to reach me is email.">
      <TextLink
        href={emailHref}
        className="inline-block break-all text-2xl font-semibold tracking-tight underline decoration-accent/40 decoration-2 underline-offset-[6px] hover:decoration-accent sm:text-4xl"
      >
        {site.email}
      </TextLink>
      <dl className="mt-10 grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2">
        {rows.map((r) => (
          <div key={r.label} className="bg-bg px-5 py-4">
            <dt className="font-mono text-[11px] uppercase tracking-widest text-muted">{r.label}</dt>
            <dd className="mt-1.5 break-all text-[15px]">
              <TextLink href={r.href}>{r.value}</TextLink>
            </dd>
          </div>
        ))}
        <div className={`bg-bg px-5 py-4 ${rows.length % 2 ? "" : "sm:col-span-2"}`}>
          <dt className="font-mono text-[11px] uppercase tracking-widest text-muted">Location</dt>
          <dd className="mt-1.5 text-[15px]">
            {site.location}. {region.relocation}
          </dd>
        </div>
      </dl>
    </Section>
  );
}
