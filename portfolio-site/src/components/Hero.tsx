import { Fragment } from "react";
import { region } from "@/data/regions";
import { coreStack, emailHref, isSet, linkedinHref, proof, site } from "@/data/site";
import { WatchDemo } from "./WatchDemo";

const base =
  "inline-flex h-11 items-center justify-center rounded-lg px-4 text-sm font-medium transition-[background-color,border-color,color,transform] duration-150 sm:px-5";

const secondary = `${base} border border-line bg-surface hover:-translate-y-px hover:border-accent`;

function Action({ href, label, primary = false, download, className = "" }: {
  href?: string; label: string; primary?: boolean; download?: string; className?: string;
}) {
  if (!isSet(href)) {
    return (
      <span className={`${base} cursor-default border border-dashed border-line text-muted ${className}`} title="Link not added yet">
        {label}
      </span>
    );
  }
  const external = /^https?:/.test(href);
  return (
    <a
      href={href}
      {...(download ? { download } : {})}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      className={`${primary ? `${base} bg-accent text-accent-fg hover:-translate-y-px hover:brightness-110` : secondary} ${className}`}
    >
      {label}
    </a>
  );
}

function Photo({ className = "", wrapper = "", sizes }: { className?: string; wrapper?: string; sizes: string }) {
  return (
    <picture className={wrapper}>
      <source type="image/webp" srcSet={`${site.photo.webp["320"]} 320w, ${site.photo.webp["640"]} 640w`} sizes={sizes} />
      { }
      <img
        src={site.photo.jpg["640"]}
        srcSet={`${site.photo.jpg["320"]} 320w, ${site.photo.jpg["640"]} 640w`}
        sizes={sizes}
        width={640}
        height={800}
        alt={site.photo.alt}
        className={className}
        fetchPriority="high"
      />
    </picture>
  );
}

const chip = "rounded-full border border-line bg-surface px-3 py-1 text-[13px] leading-snug text-muted";

export function Hero() {
  return (
    <section id="top" aria-labelledby="hero-title">
      <div className="mx-auto max-w-6xl px-5 pb-14 pt-4 sm:px-8 sm:pb-24 sm:pt-20">
        <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-center lg:gap-14" data-plain="hero">
          <div>
            <div className="flex items-center gap-3.5">
              <Photo sizes="64px" wrapper="shrink-0 lg:hidden" className="size-16 rounded-full border border-line object-cover object-top" />
              <p className="flex flex-wrap items-center gap-x-3 gap-y-1.5 font-mono text-xs uppercase tracking-widest text-muted">
                <span>{site.name}</span>
                <span className="rounded-full border border-accent/40 px-2.5 py-0.5 normal-case tracking-normal text-accent">{site.availability}</span>
              </p>
            </div>
            <h1 id="hero-title" className="mt-3 text-[2.4rem] font-semibold leading-[1.04] tracking-[-0.035em] sm:mt-6 sm:text-7xl">
              {site.title}
            </h1>
            <p className="mt-3 max-w-2xl text-[17px] leading-snug sm:mt-6 sm:text-xl sm:leading-relaxed">{site.pitch}</p>
            <p className="mt-2 max-w-2xl text-[14.5px] leading-snug text-muted sm:mt-3 sm:text-base sm:leading-relaxed">
              {site.proofLine.before}
              <a href={site.proofLine.link.href} className="font-medium text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent">
                {site.proofLine.link.label}
              </a>
              {site.proofLine.after}
            </p>

            <ul aria-label="About me" className="mt-3.5 flex flex-wrap gap-1.5 sm:mt-7 sm:gap-2">
              <li className={chip}>{region.relocationChip ?? region.relocation}</li>
              <li className={chip}>{site.chips.languages}</li>
              <li className={chip}>{site.chips.experience}</li>
            </ul>

            <p className="mt-3 font-mono text-[12px] leading-relaxed text-muted sm:mt-5 sm:text-[13px]">
              <span className="text-fg">Core stack:</span>{" "}
              {coreStack.map((s, i) => (
                <Fragment key={s}>
                  <span className="whitespace-nowrap">{i < coreStack.length - 1 ? `${s} ·` : s}</span>{" "}
                </Fragment>
              ))}
              <a href="#skills" className="whitespace-nowrap text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent">
                All skills
              </a>
            </p>

            {/* Phones: two rows, as before the video button. CV and the video share the first, the other three the
                second, each as wide as its label. */}
            <div className="mt-4 grid grid-cols-2 gap-2.5 sm:mt-8 sm:flex sm:flex-wrap sm:gap-3">
              <Action href={site.cv} label="Download CV" primary download={site.cvFileName} />
              <WatchDemo className={`${secondary} gap-2 max-sm:gap-1.5 max-sm:px-3`}>
                {/* The icon gives way on phones narrower than 390px, so the label stays on one line. */}
                <svg viewBox="0 0 24 24" className="size-3.5 shrink-0 text-accent max-[389px]:hidden" fill="currentColor" aria-hidden="true">
                  <path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5Z" />
                </svg>
                <span className="whitespace-nowrap">Watch 1-min demo</span>
              </WatchDemo>
              <div className="col-span-2 flex flex-wrap gap-2.5 sm:contents">
                <Action href="#projects" label="Try a live demo" className="max-sm:flex-auto max-sm:px-3" />
                <Action href={linkedinHref} label="LinkedIn" className="max-sm:flex-auto max-sm:px-3" />
                <Action href={emailHref} label="Email" className="max-sm:flex-auto max-sm:px-3" />
              </div>
            </div>

            {region.secondLine && (
              <p lang={region.secondLine.lang} dir={region.secondLine.dir} className="mt-6 max-w-2xl border-s-2 border-accent ps-3 text-[16px] leading-relaxed text-muted sm:mt-8 sm:text-lg">
                {region.secondLine.text}
              </p>
            )}
          </div>
          <div className="hidden lg:block">
            <Photo sizes="320px" wrapper="block" className="aspect-[4/5] w-full rounded-2xl border border-line object-cover shadow-[0_24px_60px_-30px_rgb(0_0_0/0.45)]" />
          </div>
        </div>

        <ul aria-label="Results" data-plain="results" className="mt-12 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:mt-20 lg:grid-cols-4">
          {proof.map((p) => (
            <li key={p.label} className="flex flex-col bg-bg px-4 py-4 sm:px-5 sm:py-5" title={p.technical}>
              <p className="text-2xl font-semibold tracking-tight text-accent sm:text-3xl">{p.value}</p>
              <p className="mt-1.5 text-[13px] leading-snug text-muted sm:text-sm">{p.label}</p>
              <a href={p.href} className="mt-auto pt-3 font-mono text-[11px] text-muted underline-offset-4 transition-colors hover:text-accent hover:underline">
                {p.source}: how it was measured
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
