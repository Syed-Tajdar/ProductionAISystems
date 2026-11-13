import { site } from "@/data/site";

export const dynamic = "force-static";

// Exported at build time as out/profile.json. The demos' footer reads a copy of it (demo-ui/profile.json),
// so the name and links there come from this site's data; scripts/check-profile.mjs keeps the copy in step.
export function GET() {
  return Response.json({ name: site.name, shortName: site.shortName, linkedin: site.linkedin, github: site.github, contactPath: "/#contact" });
}
