"""Renders page.html into public/index.html with the shared demo UI macros (demo_ui.html, from demo-ui/).

    pip install jinja2
    python build.py          # write public/index.html
    python build.py --check  # exit 1 if public/index.html is out of date (CI)

The built page is committed, so the Vercel project needs no Python: its build only writes config.js.
"""

import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
OUT = HERE / "public" / "index.html"
PORTFOLIO_URL = "https://syed-tajdar.vercel.app"

# The same defaults as the LEADS_DEMO_* settings in Workflow A (Normalize lead).
LIMITS = {"per_visitor": 5, "window_minutes": 10, "daily_cap": 100}

POINTS = [
    "An AI model reads each enquiry, in English or Arabic, and rates it hot, warm or cold with a one-line reason.",
    "A repeat enquiry is matched by phone or email and updates the saved lead instead of adding a second one. Hot leads alert sales on Telegram.",
    "The reply email is written for real and shown on this page instead of sent, so this public form can't be used "
    "to send messages to someone else's address. In a real setup the same step sends it.",
]

# {n} is replaced in the browser by a random number, so each visitor starts with a new lead.
EXAMPLES = [
    {"label": "Hot rental", "name": "Sara Ahmed", "phone": "+971 50 000 {n}", "email": "sara.{n}@example.com",
     "message": "Looking for a 2 bedroom to rent in Dubai Marina, budget 120k a year. We need to move within 3 weeks, can someone call me today?"},
    {"label": "Buyer in Arabic", "name": "خالد", "phone": "", "email": "khalid.{n}@example.com",
     "message": "أبحث عن شقة بغرفتين للشراء في الخليج التجاري خلال شهرين، والميزانية حوالي مليون ونصف درهم."},
    {"label": "Just browsing", "name": "Lena", "phone": "", "email": "lena.{n}@example.com",
     "message": "Just curious what rents look like in JVC for next year, no rush at all."},
    {"label": "Spam", "name": "Rank Pro", "phone": "", "email": "offers.{n}@example.com",
     "message": "We guarantee first page Google ranking and 500 backlinks for your agency website. Reply for our price list."},
]


def render() -> str:
    env = Environment(loader=FileSystemLoader(HERE), autoescape=True, undefined=StrictUndefined,
                      trim_blocks=False, lstrip_blocks=False, keep_trailing_newline=True)
    profile = json.loads((HERE / "profile.json").read_text(encoding="utf-8"))
    return env.get_template("page.html").render(
        profile=profile, portfolio_url=PORTFOLIO_URL, limits=LIMITS, points=POINTS, examples=EXAMPLES)


def main(argv: list[str]) -> int:
    html = render()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != html:
            print("public/index.html is out of date, run `python build.py`")
            return 1
        return 0
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
