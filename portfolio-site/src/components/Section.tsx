type Props = {
  id: string;
  index: string;
  title: string;
  intro?: string;
  wide?: boolean; // heading above the content instead of beside it
  children: React.ReactNode;
};

export function Section({ id, index, title, intro, wide = false, children }: Props) {
  return (
    <section id={id} aria-labelledby={`${id}-title`}>
      <div className="mx-auto max-w-6xl px-5 sm:px-8">
        <div className={`grid gap-8 border-t border-line py-16 sm:py-24 ${wide ? "lg:gap-10" : "lg:grid-cols-[13rem_1fr] lg:gap-12"}`}>
          <header className={wide ? "lg:flex lg:items-end lg:justify-between lg:gap-12" : "lg:sticky lg:top-24 lg:self-start"}>
            <div>
              <p className="font-mono text-xs tracking-widest text-accent">{index}</p>
              <h2 id={`${id}-title`} className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
                {title}
              </h2>
            </div>
            {intro && <p className={`mt-3 text-[15px] leading-relaxed text-muted ${wide ? "max-w-md" : "max-w-sm"}`}>{intro}</p>}
          </header>
          <div>{children}</div>
        </div>
      </div>
    </section>
  );
}
