"""One Jinja environment for every page, with the helpers the shared layout (base.html) uses."""

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["portfolio_url"] = lambda: get_settings().portfolio_url
# Name and links for the footer, from the portfolio site's data (copied in by demo-ui/sync.py).
templates.env.globals["profile"] = json.loads((HERE / "profile.json").read_text(encoding="utf-8"))

# The "What this demo shows" card.
DEMO_POINTS = [
    "Each call is scored against a versioned rubric, and every verdict quotes the line it rests on.",
    "Quotes are checked against the transcript; a verdict whose quote is not there goes to review.",
    "Play a call and tap a quote or a timestamp to hear that moment.",
]
templates.env.globals["demo_points"] = DEMO_POINTS
# Shared macros (footer, demo card, states) as `ui` in every template.
templates.env.globals["ui"] = templates.env.get_template("demo_ui.html").module
