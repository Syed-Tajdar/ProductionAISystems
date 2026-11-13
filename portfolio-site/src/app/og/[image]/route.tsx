import { ImageResponse } from "next/og";
import { visuals } from "@/components/ProjectVisual";
import { site } from "@/data/site";
import { ogCards } from "@/lib/og";

export const dynamic = "force-static";
export const dynamicParams = false;

// Exported at build time as out/og/<id>.png, one per project page and /calyx.
export function generateStaticParams() {
  return ogCards.map((c) => ({ image: `${c.id}.png` }));
}

// Same dark palette as /og.png and the site's dark theme.
const c = { bg: "#0c0e11", box: "#13161a", fg: "#e7e8eb", muted: "#9ba1ac", line: "#2c313a", accent: "#3ad1bd" };

const W = 1200;
const PAD = 72;
const DIAGRAM = W - PAD * 2;
const GAP = 44;

function Arrow() {
  return (
    <svg width={GAP} height={16} viewBox={`0 0 ${GAP} 16`} style={{ flexShrink: 0 }}>
      <path d={`M6 8H${GAP - 8}M${GAP - 14} 3l6 5-6 5`} stroke={c.muted} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export async function GET(_req: Request, { params }: { params: Promise<{ image: string }> }) {
  const id = (await params).image.replace(/\.png$/, "");
  const card = ogCards.find((o) => o.id === id);
  const v = visuals[id];
  if (!card || !v) return new Response("Not found", { status: 404 });

  const n = v.nodes.length;
  const w = (DIAGRAM - GAP * (n - 1)) / n;
  const bx = v.branch.from * (w + GAP) + w / 2;
  const left = v.branch.from >= n / 2; // the label runs left of the branch, as in the page diagram

  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between", padding: `60px ${PAD}px 56px`, background: c.bg, color: c.fg }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 26, color: c.muted }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div style={{ width: 13, height: 13, borderRadius: 7, background: c.accent }} />
            {site.name}
          </div>
          <div style={{ fontSize: 20, letterSpacing: 4, textTransform: "uppercase", color: c.accent }}>{card.kicker}</div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
          <div style={{ fontSize: 76, fontWeight: 700, letterSpacing: -2, lineHeight: 1 }}>{card.title}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
            <div style={{ fontSize: 64, fontWeight: 700, letterSpacing: -1.5, color: c.accent, lineHeight: 1 }}>{card.metric[0]}</div>
            <div style={{ display: "flex", flex: 1, fontSize: 28, color: c.muted, lineHeight: 1.3 }}>{card.metric[1]}</div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "flex-start", position: "relative", width: DIAGRAM, height: 150 }}>
          <div style={{ display: "flex", alignItems: "center", width: DIAGRAM, height: 88 }}>
            {v.nodes.map((node, i) => (
              <div key={node.label} style={{ display: "flex", alignItems: "center" }}>
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "center",
                    width: w,
                    height: 88,
                    padding: "0 20px",
                    borderRadius: 14,
                    background: c.box,
                    border: `2px solid ${i === v.branch.from ? c.accent : c.line}`,
                  }}
                >
                  <div style={{ fontSize: 27, fontWeight: 700 }}>{node.label}</div>
                  <div style={{ fontSize: 20, color: c.muted, marginTop: 2 }}>{node.sub}</div>
                </div>
                {i < n - 1 && <Arrow />}
              </div>
            ))}
          </div>
          <div style={{ position: "absolute", left: bx - 1, top: 88, height: 36, borderLeft: `2px dashed ${c.accent}` }} />
          <div
            style={{
              position: "absolute",
              top: 116,
              display: "flex",
              fontSize: 21,
              color: c.accent,
              ...(left ? { right: DIAGRAM - bx + 14 } : { left: bx + 14 }),
            }}
          >
            {v.branch.label}
          </div>
        </div>
      </div>
    ),
    { width: W, height: 630 },
  );
}
