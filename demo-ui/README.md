# Shared demo UI

Footer, "What this demo shows" card, and empty, error and rate-limit states for the Call QA, RAG and lead automation demos. Each demo deploys from its own folder on Vercel, so it keeps a copy of these files; `sync.py` writes the copies and CI fails when one is out of date. The lead demo is a static page: `n8n-leads/landing/build.py` renders it with the macros, and its CI checks the built page is current.

| File | What it is |
| --- | --- |
| `demo_ui.html` | Jinja macros: `theme_toggle()`, `footer(profile, portfolio_url)`, `demo_card(id, title, points, script_url)`, `state(kind, title, body, action_href, action_label)` with kind `empty`, `error` or `limit`, and `wait(seconds)` for a Retry-After value |
| `demo-base.css` | The base every demo shares, loaded before the demo's own stylesheet: colour tokens for light and dark, type, links, focus ring, skip link, page width and the header with the logo mark |
| `demo-ui.css` | Styles of the shared components (toggle, footer, card, states), loaded after the demo's own stylesheet; uses the colour tokens from `demo-base.css` |
| `theme.js` | Light and dark theme, set before the first paint (load it in `<head>` without `defer`); the `theme_toggle()` macro in the header switches it and the choice is remembered in `localStorage` |
| `demo-ui.js` | Card dismissal, remembered per browser in `localStorage` (every access in try/catch; without storage the card stays) |
| `profile.json` | Name and links for the footer, built from the portfolio site's data (`portfolio-site/src/data/site.ts`) |

```bash
python demo-ui/sync.py          # copy into the demos
python demo-ui/sync.py --check  # what CI runs
```

After changing the name or links in `site.ts`: `npm run build && npm run check-profile -- --write` in `portfolio-site`, then `python demo-ui/sync.py`. The portfolio CI fails while `profile.json` and the site data differ.

To add a demo, add its paths to `TARGETS` in `sync.py`, import the macros with `{% import "demo_ui.html" as ui %}`, load `demo-base.css` before the demo's own stylesheet and `demo-ui.css` after it.
