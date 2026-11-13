"""Parse Markdown, PDF and DOCX files into a flat list of heading and text blocks.

Headings come from Markdown '#' lines, DOCX heading styles, or, in PDFs, lines set in a larger
font than the body text. Table rows become "Header: value; Header: value" lines so each row
reads on its own after chunking. Only PDFs have page numbers.
"""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SUPPORTED = {".md": "markdown", ".markdown": "markdown", ".pdf": "pdf", ".docx": "docx"}
ARABIC = re.compile(r"[؀-ۿ]")
LETTERS = re.compile(r"[A-Za-z؀-ۿ]")


@dataclass
class Block:
    kind: str  # "heading" or "text"
    text: str
    level: int = 0
    page: int | None = None


@dataclass
class ParsedDocument:
    path: str
    title: str
    language: str
    format: str
    content_hash: str
    blocks: list[Block]
    meta: dict = field(default_factory=dict)


def detect_language(text: str) -> str:
    letters = len(LETTERS.findall(text)) or 1
    return "ar" if len(ARABIC.findall(text)) / letters > 0.3 else "en"


def table_rows(rows: list[list[str]]) -> str:
    """First row is the header. Each later row becomes 'Header: value; Header: value'."""
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        return "\n".join(" | ".join(r) for r in rows)
    header, body = rows[0], rows[1:]
    return "\n".join("; ".join(f"{h}: {v}" if h else v for h, v in zip(header, r, strict=False) if v) for r in body)


def parse_file(path: Path, root: Path | None = None) -> ParsedDocument:
    fmt = SUPPORTED.get(path.suffix.lower())
    if fmt is None:
        raise ValueError(f"unsupported file type: {path.name}")
    raw = path.read_bytes()
    title, meta, blocks = {"markdown": parse_markdown, "pdf": parse_pdf, "docx": parse_docx}[fmt](raw)
    text = " ".join(b.text for b in blocks)
    rel = str(path.relative_to(root)) if root else path.name
    return ParsedDocument(
        path=rel, title=title or path.stem.replace("-", " ").title(),
        language=meta.get("language") or detect_language(text), format=fmt,
        content_hash=hashlib.sha256(raw).hexdigest(), blocks=blocks, meta=meta)


# ------------------------------------------------------------------------ Markdown

def front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return yaml.safe_load(text[4:end]) or {}, text[end + 4:].lstrip("\n")
    return {}, text


def parse_markdown(raw: bytes) -> tuple[str, dict, list[Block]]:
    meta, body = front_matter(raw.decode("utf-8"))
    meta = {k: str(v) for k, v in meta.items()}
    blocks: list[Block] = []
    para: list[str] = []
    table: list[list[str]] = []

    def flush():
        if para:
            blocks.append(Block("text", " ".join(para)))
            para.clear()
        if table:
            blocks.append(Block("text", table_rows(table)))
            table.clear()

    for line in body.splitlines():
        s = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", s)
        if heading:
            flush()
            blocks.append(Block("heading", heading.group(2), level=len(heading.group(1))))
        elif s.startswith("|"):
            if para:
                blocks.append(Block("text", " ".join(para)))
                para.clear()
            cells = [c for c in s.strip("|").split("|")]
            if not all(re.fullmatch(r"\s*:?-{3,}:?\s*", c) for c in cells):
                table.append(cells)
        elif not s:
            flush()
        else:
            if table:
                flush()
            para.append(s)
    flush()
    title = meta.get("title") or next((b.text for b in blocks if b.kind == "heading" and b.level == 1), "")
    return title, meta, blocks


# ------------------------------------------------------------------------ PDF

def parse_pdf(raw: bytes) -> tuple[str, dict, list[Block]]:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    lines: list[dict] = []  # {page, y, size, parts: [(x, text, size)]}
    for page_no, page in enumerate(reader.pages, start=1):
        current: dict | None = None

        def visit(text, cm, tm, font_dict, font_size, page_no=page_no):
            nonlocal current
            if not text.strip():
                return
            x, y = tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5]
            size = round(font_size * (abs(tm[0]) or 1), 1)
            if current is None or abs(current["y"] - y) > 1.5:
                current = {"page": page_no, "y": y, "parts": []}
                lines.append(current)
            current["parts"].append((x, text.replace("\n", " ").strip(), size))

        page.extract_text(visitor_text=visit)

    if not lines:
        return "", {}, []
    # Running headers and footers repeat on every page (with a different page number): drop them.
    pages = len(reader.pages)
    if pages > 1:
        seen = Counter(re.sub(r"\d+", "#", " ".join(t for _, t, _ in ln["parts"])) for ln in lines)
        lines = [ln for ln in lines if seen[re.sub(r"\d+", "#", " ".join(t for _, t, _ in ln["parts"]))] < pages]
    chars = Counter()
    for ln in lines:
        for _, text, size in ln["parts"]:
            chars[size] += len(text)
    body = chars.most_common(1)[0][0]
    heading_sizes = sorted({s for s in chars if s > body * 1.1}, reverse=True)
    level = {s: i + 1 for i, s in enumerate(heading_sizes)}

    blocks: list[Block] = []
    para: list[str] = []
    rows: list[list[str]] = []
    columns: list[float] = []  # x of each table column, from the header row
    prev = None

    def flush(page):
        if para:
            blocks.append(Block("text", " ".join(para), page=page))
            para.clear()
        if rows:
            blocks.append(Block("text", table_rows(rows), page=page))
            rows.clear()

    for ln in lines:
        parts = sorted(ln["parts"])
        size = max(s for _, _, s in parts)
        cells = _cells(parts)
        page = ln["page"]
        gap = (prev["y"] - ln["y"]) if prev and prev["page"] == page else None
        if size in level:
            flush(prev["page"] if prev else page)
            text = " ".join(t for _, t in cells)
            if blocks and blocks[-1].kind == "heading" and blocks[-1].level == level[size] and gap is not None and gap < size * 1.6:
                blocks[-1].text += " " + text  # a heading that wrapped onto two lines
            else:
                blocks.append(Block("heading", text, level=level[size], page=page))
        elif rows and gap is not None and gap < body * 1.6 and (len(cells) < len(columns) or cells[0][0] > columns[0] + body):
            # A wrapped cell: add each piece to the column it lines up with.
            for x, text in cells:
                col = min(range(len(columns)), key=lambda i: abs(columns[i] - x))
                rows[-1][col] = f"{rows[-1][col]} {text}".strip()
        elif len(cells) > 1:
            if para:
                flush(page)
            if not rows:
                columns = [x for x, _ in cells]
            row = [""] * max(len(columns), len(cells))
            for x, text in cells:
                row[min(range(len(columns)), key=lambda i: abs(columns[i] - x))] = text
            rows.append(row)
        else:
            if rows or gap is None or gap > body * 1.6:
                flush(prev["page"] if prev else page)
            para.append(cells[0][1])
        prev = ln
    flush(prev["page"])
    title = reader.metadata.title if reader.metadata and reader.metadata.title else ""
    if not title:
        title = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), "")
    return title, {}, blocks


def _cells(parts: list[tuple]) -> list[tuple[float, str]]:
    """Join the text pieces of one line; a wide horizontal gap starts a new table cell."""
    cells: list[list] = []
    end = None
    for x, text, size in parts:
        if end is not None and x - end < size * 1.5:
            cells[-1][1] += ("" if cells[-1][1].endswith(" ") else " ") + text
        else:
            cells.append([x, text])
        end = x + len(text) * size * 0.5
    return [(x, t.strip()) for x, t in cells if t.strip()]


# ------------------------------------------------------------------------ DOCX

def parse_docx(raw: bytes) -> tuple[str, dict, list[Block]]:
    import io

    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(raw))
    blocks: list[Block] = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            m = re.match(r"Heading (\d)", style)
            if style == "Title":
                blocks.append(Block("heading", text, level=1))
            elif m:
                blocks.append(Block("heading", text, level=int(m.group(1)) + 1))
            else:
                blocks.append(Block("text", text))
        elif tag == "tbl":
            t = Table(el, d)
            blocks.append(Block("text", table_rows([[c.text for c in row.cells] for row in t.rows])))
    props = d.core_properties
    title = props.title or next((b.text for b in blocks if b.kind == "heading" and b.level == 1), "")
    meta = {"language": props.language} if props.language in ("en", "ar") else {}
    return title, meta, blocks


def scan(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUPPORTED and p.is_file())
