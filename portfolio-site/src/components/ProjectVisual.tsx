// Small simplified architecture per project card: a row of nodes with arrows and one dashed branch for
// the failure path. Inline SVG in the site's colour variables, so it follows the theme. It paints its own
// canvas, boxes and text from the --diagram-* colours, so nothing in it depends on the page background.

type Node = { label: string; sub: string };
export type Visual = { nodes: Node[]; branch: { from: number; label: string }; alt: string };

export const visuals: Record<string, Visual> = {
  "call-qa-agent": {
    nodes: [
      { label: "Audio", sub: "file or URL" },
      { label: "Deepgram", sub: "diarized" },
      { label: "Claude", sub: "rubric" },
      { label: "Dashboard", sub: "evidence" },
    ],
    branch: { from: 2, label: "no matching quote: review" },
    alt: "Audio goes to Deepgram for transcription, Claude scores it against a rubric and the dashboard shows the evidence; a verdict without a matching quote goes to review.",
  },
  "n8n-leads": {
    nodes: [
      { label: "Form", sub: "webhook" },
      { label: "Claude", sub: "classify" },
      { label: "Supabase", sub: "dedupe" },
      { label: "Telegram", sub: "hot leads" },
    ],
    branch: { from: 1, label: "LLM fails: unclassified" },
    alt: "A form webhook sends the lead to Claude for classification, Supabase deduplicates and stores it, and Telegram alerts sales on hot leads; if the LLM fails the lead is saved as unclassified and still alerted.",
  },
  "rag-mcp": {
    nodes: [
      { label: "Docs", sub: "EN + AR" },
      { label: "pgvector", sub: "+ full-text" },
      { label: "Claude", sub: "cited" },
      { label: "Web, MCP", sub: "read-only" },
    ],
    branch: { from: 2, label: "quote not in source: dropped" },
    alt: "English and Arabic documents are searched with pgvector and full-text search, Claude writes a cited answer, and it is served on the web and over MCP; statements whose quote is not in the source are dropped.",
  },
  calyx: {
    nodes: [
      { label: "Platforms", sub: "4 adapters" },
      { label: "Services", sub: "Go, dedup" },
      { label: "ClickHouse", sub: "analytics" },
      { label: "Dashboard", sub: "Next.js" },
    ],
    branch: { from: 3, label: "write-back, behind flags" },
    alt: "Call-tracking platforms sync into Go services that deduplicate the calls into ClickHouse for the Next.js dashboard, with write-back to the platforms behind flags.",
  },
  "calyx-wide": {
    nodes: [
      { label: "Platforms", sub: "Ringba, Retreaver" },
      { label: "Platform sync", sub: "Go, rate limited" },
      { label: "Kafka, Redis", sub: "stream, dedup" },
      { label: "ClickHouse", sub: "analytics" },
      { label: "API, dashboard", sub: "Next.js" },
    ],
    branch: { from: 1, label: "Deepgram and OpenAI, behind feature flags" },
    alt: "Ringba, Retreaver and two more platforms sync through a Go platform-sync service, Kafka and Redis deduplicate the call stream into ClickHouse, and a TypeScript API serves the Next.js dashboard; Deepgram and OpenAI sit behind feature flags.",
  },
};

export function ProjectVisual({ id, className = "" }: { id: string; className?: string }) {
  const v = visuals[id];
  if (!v) return null;
  const wide = v.nodes.length > 4;
  const width = wide ? 640 : 320;
  const gap = wide ? 22 : 16;
  const w = (width - 2 - gap * (v.nodes.length - 1)) / v.nodes.length;
  const x = (i: number) => 1 + i * (w + gap);
  const top = 6;
  const h = wide ? 46 : 42;
  const bx = x(v.branch.from) + w / 2;
  const left = v.branch.from >= v.nodes.length / 2; // label runs left of the branch
  const pad = 12;
  return (
    <svg viewBox={`${-pad} ${-pad + 4} ${width + pad * 2} ${92 + pad}`} role="img" aria-label={v.alt} className={`block h-auto w-full ${className}`}>
      <rect x={-pad} y={-pad + 4} width={width + pad * 2} height={92 + pad} fill="var(--diagram-canvas)" />
      {v.nodes.map((n, i) => (
        <g key={n.label}>
          <rect x={x(i)} y={top} width={w} height={h} rx={7} fill="var(--diagram-box)" stroke={i === v.branch.from ? "var(--accent)" : "var(--diagram-line)"} strokeWidth={1.2} />
          <text x={x(i) + w / 2} y={top + 18} textAnchor="middle" fill="var(--diagram-text)" fontSize={wide ? 12.5 : 11} fontWeight={600} fontFamily="var(--font-sans)">
            {n.label}
          </text>
          <text x={x(i) + w / 2} y={top + 33} textAnchor="middle" fill="var(--diagram-sub)" fontSize={wide ? 10 : 9} fontFamily="var(--font-mono)">
            {n.sub}
          </text>
          {i < v.nodes.length - 1 && (
            <path
              d={`M${x(i) + w + 3} ${top + h / 2} h${gap - 8} m-4 -3.5 l4 3.5 l-4 3.5`}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={1.4}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
        </g>
      ))}
      <path d={`M${bx} ${top + h} V${top + h + 22}`} stroke="var(--accent)" strokeWidth={1.2} strokeDasharray="3 3" />
      <circle cx={bx} cy={top + h + 24} r={2.5} fill="var(--accent)" />
      <text
        x={left ? bx - 8 : bx + 8}
        y={top + h + 27.5}
        textAnchor={left ? "end" : "start"}
        fill="var(--diagram-sub)"
        fontSize={wide ? 11 : 10}
        fontFamily="var(--font-mono)"
      >
        {v.branch.label}
      </text>
    </svg>
  );
}
