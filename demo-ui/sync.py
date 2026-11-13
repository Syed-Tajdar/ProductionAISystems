"""Copies the shared demo UI into each demo, which deploys on its own and cannot read this folder.

    python demo-ui/sync.py          # write the copies
    python demo-ui/sync.py --check  # exit 1 if a copy differs (CI)
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# file in demo-ui/ -> where each demo keeps its copy
TARGETS = {
    "call-qa-agent": {"demo_ui.html": "app/templates/demo_ui.html", "demo-base.css": "app/static/demo-base.css",
                      "demo-ui.css": "app/static/demo-ui.css",
                      "demo-ui.js": "app/static/demo-ui.js", "theme.js": "app/static/theme.js",
                      "profile.json": "app/profile.json"},
    "rag-mcp": {"demo_ui.html": "rag/templates/demo_ui.html", "demo-base.css": "rag/static/demo-base.css",
                "demo-ui.css": "rag/static/demo-ui.css",
                "demo-ui.js": "rag/static/demo-ui.js", "theme.js": "rag/static/theme.js", "profile.json": "rag/profile.json"},
    # Static page: landing/build.py renders it with the macros into landing/public/index.html.
    "n8n-leads": {"demo_ui.html": "landing/demo_ui.html", "demo-base.css": "landing/public/demo-base.css",
                  "demo-ui.css": "landing/public/demo-ui.css",
                  "demo-ui.js": "landing/public/demo-ui.js", "theme.js": "landing/public/theme.js",
                  "profile.json": "landing/profile.json"},
}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for app, files in TARGETS.items():
        for source, target in files.items():
            want, path = (HERE / source).read_bytes(), ROOT / app / target
            if path.exists() and path.read_bytes() == want:
                continue
            if check:
                stale.append(f"{app}/{target}")
            else:
                path.write_bytes(want)
                print(f"updated {app}/{target}")
    if stale:
        print("out of date, run `python demo-ui/sync.py`:\n  " + "\n  ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
