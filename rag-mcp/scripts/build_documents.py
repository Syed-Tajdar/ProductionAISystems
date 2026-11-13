"""Render the PDF and DOCX documents in documents/ from their Markdown sources in sources/.

    pip install -e ".[documents]"
    python scripts/build_documents.py

The English tenant handbook becomes a PDF and both leave policies become Word files, so the
ingestion pipeline is exercised on all three formats. The other documents stay Markdown.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.documents import Block, parse_markdown  # noqa: E402

TARGETS = {
    "tenant-handbook.en.md": ("en/tenant-handbook.pdf", "pdf"),
    "leave-policy.en.md": ("en/leave-policy.docx", "docx"),
    "leave-policy.ar.md": ("ar/leave-policy.docx", "docx"),
}


def table_cells(md: str) -> list[list[str]]:
    rows = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and not set(s.replace("|", "").strip()) <= {"-", " ", ":"}:
            rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows


def items(md: str):
    """(kind, payload) in document order: headings and paragraphs from the parser, tables as cells."""
    _, meta, _ = parse_markdown(md.encode())
    body = md.split("\n---", 1)[1] if md.startswith("---\n") else md
    chunks, table = [], []
    for line in body.splitlines() + [""]:
        if line.strip().startswith("|"):
            table.append(line)
            continue
        if table:
            chunks.append(("table", table_cells("\n".join(table))))
            table = []
        chunks.append(("line", line))
    out, para = [], []
    for kind, payload in chunks:
        if kind == "table":
            if para:
                out.append(("text", " ".join(para)))
                para = []
            out.append(("table", payload))
            continue
        s = payload.strip()
        if s.startswith("#"):
            if para:
                out.append(("text", " ".join(para)))
                para = []
            level = len(s) - len(s.lstrip("#"))
            out.append(("heading", Block("heading", s.lstrip("#").strip(), level=level)))
        elif not s:
            if para:
                out.append(("text", " ".join(para)))
                para = []
        else:
            para.append(s)
    return meta, out


def build_pdf(md: str, out: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    meta, parts = items(md)
    styles = {
        1: ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=6),
        2: ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=14, leading=18, spaceBefore=10, spaceAfter=4),
        3: ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=12, leading=15, spaceBefore=6, spaceAfter=3),
    }
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=13.5, spaceAfter=5)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=10.5)
    flow = []
    for kind, payload in parts:
        if kind == "heading":
            flow.append(Paragraph(payload.text, styles[min(payload.level, 3)]))
        elif kind == "text":
            flow.append(Paragraph(payload, body))
        else:
            widths = {3: [40 * mm, 60 * mm, 70 * mm], 4: [26 * mm, 60 * mm, 26 * mm, 58 * mm]}.get(len(payload[0]))
            t = Table([[Paragraph(c, cell) for c in row] for row in payload], colWidths=widths, repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            flow += [t, Spacer(1, 6)]

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 12 * mm, f"{meta.get('company', '')} | {meta.get('reference', '')} | Page {doc.page}")

    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=20 * mm, title=meta.get("title", ""), author=meta.get("company", ""))
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)


def build_docx(md: str, out: Path) -> None:
    import docx
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    meta, parts = items(md)
    rtl = meta.get("language") == "ar"
    d = docx.Document()
    d.core_properties.title = meta.get("title", "")
    d.core_properties.language = meta.get("language", "")
    d.core_properties.author = meta.get("company", "")

    def right_to_left(paragraph):
        if rtl:
            paragraph.paragraph_format.element.get_or_add_pPr().append(OxmlElement("w:bidi"))
            for run in paragraph.runs:
                rpr = run._element.get_or_add_rPr()
                rpr.append(OxmlElement("w:rtl"))

    for kind, payload in parts:
        if kind == "heading":
            p = d.add_heading(payload.text, level=0 if payload.level == 1 else payload.level - 1)
        elif kind == "text":
            p = d.add_paragraph(payload)
        else:
            t = d.add_table(rows=len(payload), cols=len(payload[0]))
            t.style = "Table Grid"
            if rtl:
                t._tbl.tblPr.append(OxmlElement("w:bidiVisual"))
            for r, row in enumerate(payload):
                for c, text in enumerate(row):
                    t.cell(r, c).text = text
                    for p in t.cell(r, c).paragraphs:
                        right_to_left(p)
            continue
        right_to_left(p)
    section = d.sections[0]
    footer = section.footer.paragraphs[0]
    footer.text = f"{meta.get('company', '')} | {meta.get('reference', '')}"
    d.add_paragraph()
    d.save(out)
    del qn


def main() -> None:
    for src, (target, kind) in TARGETS.items():
        md = (ROOT / "sources" / src).read_text(encoding="utf-8")
        out = ROOT / "documents" / target
        out.parent.mkdir(parents=True, exist_ok=True)
        (build_pdf if kind == "pdf" else build_docx)(md, out)
        print(f"{src} -> documents/{target}")


if __name__ == "__main__":
    main()
