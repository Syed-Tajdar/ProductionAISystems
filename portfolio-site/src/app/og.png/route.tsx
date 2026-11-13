import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ImageResponse } from "next/og";
import { site } from "@/data/site";

export const dynamic = "force-static";

// Exported at build time as out/og.png for link previews.
// The portrait, embedded at build time (the image renderer reads it as a data URL).
const photo = `data:image/jpeg;base64,${readFileSync(join(process.cwd(), "public", site.photo.jpg["640"])).toString("base64")}`;

export function GET() {
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", gap: 56, padding: "64px 72px", background: "#0c0e11", color: "#e7e8eb" }}>
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", height: "100%", flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 28, color: "#9ba1ac" }}>
            <div style={{ width: 14, height: 14, borderRadius: 7, background: "#3ad1bd" }} />
            {site.name}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ fontSize: 76, fontWeight: 700, letterSpacing: -2, lineHeight: 1 }}>{site.title}</div>
            <div style={{ fontSize: 29, color: "#e7e8eb", lineHeight: 1.3 }}>{site.pitch}</div>
            <div style={{ fontSize: 22, color: "#9ba1ac", lineHeight: 1.35 }}>
              {site.proofLine.before + site.proofLine.link.label + site.proofLine.after}
            </div>
          </div>
          <div style={{ display: "flex", height: 6, width: 160, background: "#3ad1bd", borderRadius: 3 }} />
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element -- rendered to PNG at build time */}
        <img src={photo} width={336} height={420} alt="" style={{ borderRadius: 24, border: "2px solid #252930", objectFit: "cover" }} />
      </div>
    ),
    { width: 1200, height: 630 },
  );
}
