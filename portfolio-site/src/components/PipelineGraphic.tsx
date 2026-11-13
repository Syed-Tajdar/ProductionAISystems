// Hero graphic: the Call QA pipeline. A pulse travels along the connectors and each step lights up when
// it arrives. CSS only (classes in globals.css); with reduced motion it is a static diagram. Boxes and
// their text use the --diagram-* colours, so the labels keep their contrast as one unit.

const steps = [
  { label: "Call", sub: "audio or URL", icon: "M5 4h3l2 5-2.5 1.5a11 11 0 0 0 6 6L15 14l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z" },
  { label: "Transcribe", sub: "Deepgram", icon: "M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4" },
  { label: "Score", sub: "Claude", icon: "M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" },
  { label: "Evidence", sub: "quote checked", icon: "M7 7h4v4c0 3-1.5 5-4 6M15 7h4v4c0 3-1.5 5-4 6" },
];

export function PipelineGraphic() {
  return (
    <figure className="rounded-xl border border-line bg-surface p-4 sm:p-5">
      <figcaption className="flex items-center justify-between gap-3 font-mono text-[11px] uppercase tracking-widest text-muted">
        <span>Call QA pipeline</span>
        <span className="flex items-center gap-1.5 normal-case tracking-normal">
          <span className="pipe-live size-1.5 rounded-full bg-accent" aria-hidden="true" />
          per call, end to end
        </span>
      </figcaption>
      <ol className="mt-4 flex items-stretch" aria-label="Call, then transcribe, then score, then evidence">
        {steps.map((s, i) => (
          <li key={s.label} className="contents">
            {i > 0 && (
              <span className="relative mx-0.5 h-[2px] w-1.5 shrink-0 self-center overflow-hidden rounded-full bg-line sm:mx-1 sm:w-4" aria-hidden="true">
                <span className="pipe-pulse" style={{ animationDelay: `${(i - 1) * 1.2}s` }} />
              </span>
            )}
            <span className="relative flex min-w-0 flex-1 flex-col items-center rounded-lg border border-diagram-line bg-diagram-box px-0.5 py-2.5 text-center text-diagram-text">
              <span className="pipe-glow" style={{ animationDelay: `${i * 1.2}s` }} aria-hidden="true" />
              <svg viewBox="0 0 24 24" className="size-5 text-accent" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d={s.icon} />
              </svg>
              <span className="mt-1.5 max-w-full truncate text-[11.5px] font-semibold tracking-tight sm:text-[13px]">{s.label}</span>
              <span className="mt-0.5 hidden font-mono text-[10px] leading-tight text-diagram-sub sm:block">{s.sub}</span>
            </span>
          </li>
        ))}
      </ol>
      <div className="mt-4 rounded-lg border border-diagram-line bg-diagram-box px-3 py-2.5 font-mono text-[11.5px] leading-relaxed">
        <p className="flex flex-wrap items-center gap-x-2 text-diagram-sub">
          <span>recording_disclosure</span>
          <span className="rounded bg-accent-soft px-1.5 font-semibold text-accent">PASS</span>
        </p>
        <p className="mt-1 text-diagram-text">&ldquo;this call is recorded for quality and compliance purposes&rdquo;</p>
        <p className="mt-1 text-[10.5px] text-diagram-sub">From sample call 01 in the eval, where every verdict matched.</p>
      </div>
    </figure>
  );
}
