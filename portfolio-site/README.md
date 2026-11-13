# Portfolio site

[![portfolio-site](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/portfolio-site.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/portfolio-site.yml)

Portfolio built with Next.js 16 (static export) and Tailwind CSS 4: the main page, the Calyx AI case study at `/calyx` and a page per project under `/projects/`. No web fonts, no animation or chart libraries, one small client component (theme toggle), Vercel Analytics as the only script.

Spec: [00-portfolio-site.md](00-portfolio-site.md)

## Run locally

Requires Node 20.9 or newer.

```bash
npm install
npm run dev          # http://localhost:3000
npm run build        # static export to out/, then a Content-Security-Policy written into every page
npm run export-calyx # prints /calyx to public/calyx-ai-case-study.pdf (needs Playwright with Chromium)
npm run check-links  # verifies every href/src in out/ resolves
npm run check-og     # verifies the link preview tags and images of each page in out/
npm run check-csp    # every page in out/ has a policy that covers exactly its inline scripts
npm run check-profile # out/profile.json (name and links) matches ../demo-ui/profile.json, the demos' footer data
npm run typecheck
```

To preview the production build, serve `out/` with any static server, for example `npx serve out`.

## Edit content

Everything on the main page comes from [`src/data/site.ts`](src/data/site.ts): name, tagline, the proof bar, project cards, the "How I build" points, the 8 mini automation cards, skills, experience and contact details. The parts that depend on the target region come from a region profile (next section).

- **Real numbers only.** Every figure in the proof bar, the card badges and the project pages comes from a recorded eval run or the running system, and the project pages keep each README's scope notes next to them. Update them together when an eval is rerun.
- **Demos:** the live demo URLs are in `demos` in `site.ts`. With a URL, the card shows "Try it live" and the project page a "Live demo" link, both opening in a new tab, and the JSON-LD names it as a `WebApplication`. Without one, the card links only to the project page.
- **Project pages** (`/projects/call-qa-agent`, `/projects/n8n-leads`, `/projects/rag-mcp`) are built from [`src/data/projects.ts`](src/data/projects.ts): problem, architecture steps, failure handling, key decisions, eval results with scope notes, and links to the code. Add an entry there and it gets a page, a sitemap entry and its JSON-LD.

- Values in `[brackets]` are placeholders. They render as plain text, never as links, so there are no broken links while you fill them in.
- Replace a placeholder with the real value (no brackets) and it becomes a link. For example set `github` in a mini automation to its repo URL.
- Replace `public/cv.pdf` with your CV (keep the file name).
- The Calyx AI case study text is in `src/data/calyx.ts`. After editing it, run `npm run build && npm run export-calyx` so the PDF matches the page.

## Region profiles

`SITE_REGION` (a build-time environment variable, default `gulf`) selects one profile in [`src/data/regions.ts`](src/data/regions.ts). There is no switch on the site itself: each build is for one region.

| Setting | `gulf` | `global` |
| --- | --- | --- |
| Second-language line under the tagline | Arabic, `lang="ar"`, right to left | none |
| Relocation line in Contact | Open to relocate to UAE, KSA or Qatar, or remote | Open to remote work and relocation |
| Home page meta description and extra keywords | Arabic and English, UAE, KSA, Qatar | remote work, LLM agents, workflow automation |
| Project card order (also the sitemap and JSON-LD) | Calyx AI, Bilingual RAG, Call QA, n8n leads | Calyx AI, Call QA, n8n leads, Bilingual RAG |

- **Add a region:** add an entry to `regions` in `regions.ts` with the same fields. Nothing else changes.
- **Checks at build time:** an unknown `SITE_REGION` stops the build with the list of valid names, and every profile's `projectOrder` must list each project exactly once, so a typo in any profile fails the build, not only in the selected one.
- **Locally:** `SITE_REGION=global npm run build`.
- **On Vercel:** Project Settings > Environment Variables, add `SITE_REGION` with the profile name for Production (and Preview if wanted), then redeploy: the value is read when the site is built, so it only applies to new deployments. Leaving it unset gives `gulf`. To serve two regions at once, create a second Vercel project from the same repository with a different `SITE_REGION` and domain.

## Structure

```
src/
  app/
    layout.tsx        default metadata and title template, theme script, analytics
    page.tsx          section order, JSON-LD for the person and the projects
    calyx/page.tsx    Calyx AI case study (content in data/calyx.ts)
    projects/[slug]/  one page per entry in data/projects.ts
    sitemap.ts, robots.ts   sitemap.xml and robots.txt, built at export time
    globals.css       colour tokens (light and dark), base styles
    og.png/route.tsx  1200x630 link preview image of the main page, rendered at build time
    og/[image]/       one preview image per project page and /calyx (out/og/<id>.png)
    profile.json/     name and links for the demos' footer, see ../demo-ui/README.md
    icon.svg          favicon
  components/         Nav, Hero, PipelineGraphic (CSS-animated), Projects, ProjectCard,
                      ProjectVisual (small architecture SVGs), HowIBuild, MiniAutomations,
                      Skills, Experience, Contact, Footer, Section, PageHeader, JsonLd,
                      ThemeToggle, CalyxDiagram (inline SVG in the site's colours)
  data/site.ts        all content of the main page
  data/regions.ts     region profiles, selected by SITE_REGION
  data/projects.ts    the project pages
  data/calyx.ts       the case study text
  lib/site-url.ts     base URL for metadata, canonical links, sitemap and JSON-LD
  lib/og.ts           which pages get their own preview image, and its headline number
  lib/schema.ts       JSON-LD: Person, SoftwareSourceCode per project, CreativeWork for Calyx AI
public/calyx-ai-case-study.pdf   printed from /calyx by scripts/export-calyx.mjs
scripts/check-links.mjs
scripts/check-og.mjs
```

## Theme

Colours are CSS variables in `globals.css`; the accent is `--accent`. The page follows the system colour scheme until the visitor uses the toggle, which is then stored in `localStorage`. A small inline script sets the theme before first paint to avoid a flash.

**Browser dark modes:** the page declares `<meta name="color-scheme" content="light dark">`, and the root sets `color-scheme: only light` or `only dark` to match the active theme, so phone browsers with their own dark mode (Chrome's auto dark for web contents, Samsung Internet) use the site's themes instead of force-darkening it. The diagrams (project card visuals, the hero pipeline, the Calyx AI diagram) paint their own canvas, boxes and text from the `--diagram-*` colours, so if a browser darkens the page anyway, each diagram stays readable as one unit. Checked with Chromium's forced dark mode (`--blink-settings=forceDarkModeEnabled=true`) on every page in both themes.

Motion is CSS only: the hero pipeline pulse (opacity and transform, so it stays on the compositor) and a small hover lift on cards. With `prefers-reduced-motion: reduce` every animation is off and the graphic is a static diagram.

## SEO

- Each page has its own title (template `%s | Syed Tajdar`), description, canonical URL and Open Graph and Twitter tags, and exactly one `h1`.
- Link previews: the main page uses `/og.png`. Each project page and `/calyx` has its own 1200x630 image with the project name, one headline number and its architecture diagram, rendered at build time from the same data as the page (`lib/og.ts`; the build fails if the chosen number is no longer on the page). Image URLs are absolute, so set `NEXT_PUBLIC_SITE_URL` or build on Vercel. LinkedIn caches a preview for about a week: after a change, refresh it with LinkedIn's Post Inspector.
- `sitemap.xml` and `robots.txt` are generated at build time from the same page list.
- JSON-LD: on the main page a `Person` (job title, `knowsAbout`, `sameAs` LinkedIn and GitHub), a `SoftwareSourceCode` per project and a `CreativeWork` for the Calyx AI case study; each project page has its `SoftwareSourceCode` and a breadcrumb.
- Every image has alt text; the inline SVG diagrams have a text description (`aria-label` or `title` and `desc`), and decorative icons are hidden from screen readers.

## Deploy (Vercel)

Import the repository in Vercel, set **Root Directory** to `portfolio-site`, keep the Next.js preset. Turn on Web Analytics in the project settings.

The domain is not final, so every absolute URL (`metadataBase`, canonical links, the sitemap, robots.txt and JSON-LD) comes from `NEXT_PUBLIC_SITE_URL`, for example `https://example.com`. Without it Vercel's production URL is used, and a local build falls back to `http://localhost:3000`. Set it in the Vercel project's environment variables once the domain is chosen, and redeploy.

Code links (the GitHub button, "View code" and README links) stay off while the repository is private. Set `PUBLIC_CODE_URL` to the public repository's address, for example `https://github.com/<owner>/<name>`, and redeploy to turn them on and point them there. `PUBLIC_CODE=true` alone still works and uses this repository's own address.

## Security headers

`vercel.json` sends these with every response: `Content-Security-Policy: frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin` and a `Permissions-Policy` that turns off camera, microphone, location and payment.

The script policy cannot be one header. Next.js starts every exported page with inline scripts that hold that page's data, next to the theme script, so their hashes differ per page and per build, and a static host sends the same header for every page. `scripts/csp.mjs` runs after `next build` and writes a `<meta http-equiv="Content-Security-Policy">` into each page with the SHA-256 of each inline script on it: `script-src 'self'` plus those hashes, no `'unsafe-inline'` for scripts. Styles allow inline, because the exported markup has style attributes. `frame-ancestors` only works as a header, which is why it is in `vercel.json`. `npm run check-csp` fails the build in CI if a page's policy and its scripts drift apart.

## Checks

Measured on the static export served with brotli and cache headers (Lighthouse 13.5, mobile preset):

| Page | Performance | Accessibility | Best Practices | SEO |
| --- | --- | --- | --- | --- |
| `/` (three runs) | 99, 99, 100 | 100 | 96 | 100 |
| `/projects/call-qa-agent` | 100 | 100 | 96 | 100 |
| `/projects/rag-mcp` | 100 | 100 | 96 | 100 |
| `/calyx` | 100 | 100 | 96 | 100 |

Main page: LCP 1.5 to 1.6 s, TBT 70 to 90 ms, CLS 0. One earlier run of the main page scored 83 (TBT 440 ms); three reruns did not repeat it. The Best Practices miss is the analytics script returning 404 outside Vercel.
