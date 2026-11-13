"""Checks that the repository secrets a setup step needs are set, and masks values derived from them.

    python .github/scripts/check_secrets.py <migrations|ingest-rag|seed-demo-calls|all>

GitHub masks each secret's full value in logs. Parts of one (the database host, user, password,
project ref) would not be masked on their own, so they are registered with ::add-mask:: here.
Prints only secret names, never values.
"""

import os
import sys
from urllib.parse import unquote, urlsplit

NEEDS = {
    "migrations": ["SUPABASE_DB_URL"],
    "ingest-rag": ["SUPABASE_DB_URL", "GEMINI_API_KEY"],
    "seed-demo-calls": ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY"],
}
NEEDS["all"] = sorted({name for names in NEEDS.values() for name in names})


def mask(value: str | None) -> None:
    if value and len(value) >= 4:
        print(f"::add-mask::{value}")


def mask_derived() -> None:
    if db := os.environ.get("SUPABASE_DB_URL"):
        parts = urlsplit(db)
        for value in (parts.password, unquote(parts.password or ""), parts.hostname):
            mask(value)
        if parts.username and "." in parts.username:  # pooler user: postgres.<project ref>
            mask(parts.username)
            mask(parts.username.split(".", 1)[1])
    if api := os.environ.get("SUPABASE_URL"):
        host = urlsplit(api).hostname or ""
        mask(host)
        mask(host.split(".", 1)[0])  # project ref


def main(step: str) -> int:
    if step not in NEEDS:
        print(f"::error::unknown step {step!r}")
        return 2
    mask_derived()
    missing = [name for name in NEEDS[step] if not os.environ.get(name, "").strip()]
    if missing:
        print(f"::error::missing repository secrets for '{step}': {', '.join(missing)}. "
              "Add them under Settings > Secrets and variables > Actions (see the DEPLOY.md files).")
        return 1
    db = os.environ.get("SUPABASE_DB_URL", "")
    if db:
        parts = urlsplit(db)
        if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
            print("::error::SUPABASE_DB_URL is not a postgresql:// connection string")
            return 1
        if not parts.hostname.endswith("pooler.supabase.com"):
            print("::warning::SUPABASE_DB_URL does not point at the Supabase pooler. GitHub's runners have no IPv6, "
                  "and the direct database host is IPv6-only on the free plan: use Connect > Session pooler.")
    print(f"secrets present for '{step}': {', '.join(NEEDS[step])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "all"))
