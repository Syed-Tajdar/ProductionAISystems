"""Load and validate YAML rubrics, and register them in the rubrics table."""

from pathlib import Path

import yaml

RUBRIC_DIR = Path(__file__).resolve().parents[1] / "rubrics"
ITEM_FIELDS = {"id", "title", "weight", "critical", "pass_if", "fail_if"}


class RubricError(ValueError):
    pass


def load_rubric(slug: str, directory: Path = RUBRIC_DIR) -> dict:
    path = directory / f"{slug}.yaml"
    if not path.is_file():
        raise RubricError(f"unknown rubric {slug!r}")
    rubric = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate(rubric, slug)
    return rubric


def available(directory: Path = RUBRIC_DIR) -> list[str]:
    return sorted(p.stem for p in directory.glob("*.yaml"))


def validate(rubric: dict, slug: str) -> None:
    if rubric.get("id") != slug:
        raise RubricError(f"{slug}: id must match the file name")
    items = rubric.get("items") or []
    if not items:
        raise RubricError(f"{slug}: no items")
    ids = [i.get("id") for i in items]
    if len(ids) != len(set(ids)):
        raise RubricError(f"{slug}: duplicate item ids")
    for item in items:
        missing = ITEM_FIELDS - set(item)
        if missing:
            raise RubricError(f"{slug}.{item.get('id')}: missing {', '.join(sorted(missing))}")
        if not isinstance(item["weight"], (int, float)) or item["weight"] <= 0:
            raise RubricError(f"{slug}.{item['id']}: weight must be a positive number")
    if not 0 < rubric.get("pass_threshold", 0) <= 100:
        raise RubricError(f"{slug}: pass_threshold must be between 1 and 100")


def ensure_rubric_row(db, rubric: dict) -> str:
    """Rubric versions are immutable rows, so scores always point at the rubric they used."""
    row = db.get_rubric(rubric["id"], rubric["version"])
    if row:
        return row["id"]
    return db.insert_rubric(slug=rubric["id"], name=rubric["name"], version=rubric["version"], items=rubric["items"])["id"]
