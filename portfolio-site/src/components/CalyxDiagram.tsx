// Simplified architecture: sources, Go services, stores, API and dashboard. Colours come from the
// site's --diagram-* variables, so the diagram follows the light and dark theme (and prints in light).
// It paints its own canvas, so its text never depends on the page background.

const box = { fill: "var(--diagram-box)", stroke: "var(--diagram-line)", strokeWidth: 1.2 };
const text = { fill: "var(--diagram-text)", fontSize: 13, fontFamily: "var(--font-sans)" };
const small = { fill: "var(--diagram-sub)", fontSize: 11, fontFamily: "var(--font-mono)" };
const label = { fill: "var(--diagram-sub)", fontSize: 10.5, fontFamily: "var(--font-mono)", letterSpacing: "0.08em" };

function Node({ x, y, w, h = 34, title, note, accent = false }: {
  x: number; y: number; w: number; h?: number; title: string; note?: string; accent?: boolean;
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={8} {...box} {...(accent ? { stroke: "var(--accent)" } : {})} />
      <text x={x + 12} y={y + (note ? 15 : h / 2 + 4.5)} {...text}>{title}</text>
      {note && <text x={x + 12} y={y + 28} {...small}>{note}</text>}
    </g>
  );
}

function Arrow({ d, dashed = false }: { d: string; dashed?: boolean }) {
  return (
    <path d={d} fill="none" stroke="var(--accent)" strokeWidth={1.5} markerEnd="url(#calyx-arrow)"
          {...(dashed ? { strokeDasharray: "5 4" } : {})} />
  );
}

export function CalyxDiagram() {
  const platforms: [string, string][] = [
    ["Ringba", "certified live"], ["Retreaver", "certified live"], ["TrackDrive", "adapter"], ["CallGrid", "adapter"],
  ];
  // Grid placed so the lines do not cross: platform sync next to the platforms, scoring and ask-ai above OpenAI.
  const services: [string, number, number][] = [
    ["Platform sync", 0, 0], ["Ingestion", 1, 0], ["Consumer", 0, 1], ["Enrichment", 1, 1],
    ["Compliance", 0, 2], ["Scoring", 1, 2], ["Ask AI", 1, 3],
  ];
  return (
    <figure tabIndex={0} aria-label="Calyx AI architecture diagram, scrolls sideways on small screens" className="overflow-x-auto rounded-xl border border-line print:overflow-visible print:border-0">
      <svg viewBox="-16 -16 992 522" role="img" aria-labelledby="calyx-diagram-title calyx-diagram-desc"
           className="block h-auto w-full min-w-[660px] sm:min-w-0">
        <title id="calyx-diagram-title">Calyx AI architecture</title>
        <desc id="calyx-diagram-desc">
          The platform-sync service pulls calls from the call-tracking platforms into Go services for ingestion,
          consumption, enrichment, compliance, scoring and Ask AI, which use Kafka and Redis and store data in
          ClickHouse and PostgreSQL. A TypeScript API serves a Next.js dashboard. Write-back requests go from the API
          to platform sync, which calls the platforms. Platform sync uses Deepgram for transcription; scoring and Ask
          AI call OpenAI. Both providers sit behind feature flags.
        </desc>
        <defs>
          <marker id="calyx-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="var(--accent)" />
          </marker>
        </defs>

        <rect x={-16} y={-16} width={992} height={522} fill="var(--diagram-canvas)" />

        {/* Docker Compose boundary */}
        <rect x={232} y={72} width={712} height={402} rx={14} fill="none" stroke="var(--diagram-line)" strokeDasharray="6 5" />
        <text x={248} y={464} {...label}>CALYX AI ON DOCKER COMPOSE, LINUX SERVER</text>

        {/* Sources */}
        <text x={16} y={58} {...label}>CALL-TRACKING PLATFORMS</text>
        <rect x={8} y={76} width={196} height={226} rx={12} fill="none" stroke="var(--diagram-line)" />
        {platforms.map(([name, note], i) => (
          <Node key={name} x={16} y={84 + i * 56} w={180} h={42} title={name} note={note} accent={i < 2} />
        ))}

        {/* Go services */}
        <text x={248} y={58} {...label}>GO SERVICES</text>
        <rect x={248} y={84} width={268} height={266} rx={12} fill="var(--accent-soft)" stroke="none" />
        {services.map(([name, col, row]) => (
          <Node key={name} x={262 + col * 124} y={98 + row * 60} w={116} title={name} accent={name === "Platform sync"} />
        ))}

        {/* Stores */}
        <text x={560} y={58} {...label}>DATA</text>
        <Node x={560} y={84} w={160} h={42} title="Kafka" note="call stream" />
        <Node x={560} y={140} w={160} h={42} title="Redis" note="dedup keys, 1 GB" />
        <Node x={560} y={196} w={160} h={42} title="ClickHouse" note="analytics" />
        <Node x={560} y={252} w={160} h={42} title="PostgreSQL" />

        {/* API and dashboard */}
        <text x={764} y={58} {...label}>PRODUCT</text>
        <Node x={764} y={140} w={164} h={42} title="TypeScript API" accent />
        <Node x={764} y={252} w={164} h={42} title="Next.js dashboard" accent />

        {/* AI providers */}
        <Node x={262} y={392} w={116} title="Deepgram" />
        <Node x={386} y={392} w={116} title="OpenAI" />
        <text x={522} y={413} {...label}>AI PROVIDERS, BEHIND FEATURE FLAGS</text>

        {/* Calls in: platforms to platform sync, then through the services to the stores and the API */}
        <Arrow d="M204 124 H258" />
        <text x={231} y={140} {...small} textAnchor="middle">sync</text>
        <Arrow d="M516 189 H556" />
        <Arrow d="M720 217 C742 217 742 166 760 163" />
        <Arrow d="M720 273 C742 273 742 176 760 170" />
        <Arrow d="M846 182 V248" />

        {/* AI: platform sync transcribes with Deepgram; scoring and ask-ai call OpenAI */}
        <Arrow d="M276 132 V142 H255 V409 H258" />
        <Arrow d="M444 312 V388" />
        <Arrow d="M502 235 H510 V409 H506" />

        {/* Write-back: API to platform sync, platform sync to the platforms */}
        <Arrow d="M846 140 V24 H340 V94" dashed />
        <Arrow d="M262 106 H208" dashed />
        <text x={593} y={16} {...small} textAnchor="middle">write-back: convert, behind flags, allowlist and amount cap</text>
      </svg>
      <figcaption className="border-t border-line px-4 py-3 text-sm text-muted print:border-0 print:px-0">
        Simplified. Calls flow left to right. Dashed: write-back from the API through platform sync to the source platform.
        <span className="sm:hidden"> Scroll sideways to see all of it.</span>
      </figcaption>
    </figure>
  );
}
