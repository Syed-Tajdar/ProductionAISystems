from pathlib import Path

import pytest

from rag.chunking import chunk_document
from rag.documents import Block, ParsedDocument, detect_language, parse_file, parse_markdown, scan, table_rows

DOCS = Path(__file__).resolve().parents[1] / "documents"


def test_markdown_front_matter_headings_and_tables():
    md = b"""---
title: Sample Policy
language: en
---

# Sample Policy

Intro line one
continues here.

## 1. Fees

| Item | Fee |
| --- | --- |
| Access card | AED 250 |
| Parking tag | AED 150 |

After the table.
"""
    title, meta, blocks = parse_markdown(md)
    assert (title, meta["language"]) == ("Sample Policy", "en")
    assert [(b.kind, b.level, b.text) for b in blocks] == [
        ("heading", 1, "Sample Policy"),
        ("text", 0, "Intro line one continues here."),
        ("heading", 2, "1. Fees"),
        ("text", 0, "Item: Access card; Fee: AED 250\nItem: Parking tag; Fee: AED 150"),
        ("text", 0, "After the table."),
    ]


def test_table_rows_skip_empty_cells():
    assert table_rows([["A", "B"], ["x", ""], ["", ""]]) == "A: x"


def test_language_detection():
    assert detect_language("Rent is paid in advance") == "en"
    assert detect_language("يُدفع الإيجار مقدماً عن سنة العقد") == "ar"
    assert detect_language("رسوم 500 AED") == "ar"


def test_every_document_in_the_set_parses():
    found = {str(p.relative_to(DOCS)): parse_file(p, DOCS) for p in scan(DOCS)}
    assert sorted(found) == ["ar/leave-policy.docx", "ar/service-faq.md", "ar/tenant-handbook.md", "ar/travel-expenses-policy.md",
                             "en/amenities-guide.md", "en/leave-policy.docx", "en/service-faq.md", "en/tenant-handbook.pdf"]
    for path, doc in found.items():
        assert doc.language == path[:2], path
        assert doc.title and any(b.kind == "heading" for b in doc.blocks), path
        assert len(doc.content_hash) == 64


def test_pdf_headings_pages_and_tables():
    doc = parse_file(DOCS / "en/tenant-handbook.pdf", DOCS)
    assert (doc.format, doc.title) == ("pdf", "Tenant Handbook")
    headings = [(b.level, b.text) for b in doc.blocks if b.kind == "heading"]
    assert headings[:4] == [(1, "Tenant Handbook"), (2, "1. About this handbook"), (2, "2. Moving in"), (3, "2.1 Handover inspection")]
    assert {b.page for b in doc.blocks} == {1, 2, 3}
    text = "\n".join(b.text for b in doc.blocks)
    assert "Page 1" not in text  # running footer removed
    assert ("Priority: Emergency; Examples: Water leak, power cut in the unit, lift entrapment, gas smell; "
            "First response: Within 2 hours, 24/7") in text  # wrapped table cells joined again
    assert "Team: Maintenance; Email: maintenance@palmgrove.example; Phone: 800 7256; Hours: 24 hours" in text
    clauses = [b.text for b in doc.blocks if b.text.startswith("2.2.")]
    assert len(clauses) == 2  # one paragraph per numbered clause


def test_docx_headings_tables_and_arabic():
    en = parse_file(DOCS / "en/leave-policy.docx", DOCS)
    ar = parse_file(DOCS / "ar/leave-policy.docx", DOCS)
    assert (en.format, en.title, en.language) == ("docx", "Working Time and Leave Policy", "en")
    assert (ar.title, ar.language) == ("سياسة ساعات العمل والإجازات", "ar")
    assert [b.text for b in en.blocks if b.kind == "heading"][:3] == ["Working Time and Leave Policy", "1. Purpose and scope", "2. Working hours"]
    rows = next(b.text for b in en.blocks if b.text.startswith("Leave type:"))
    assert "Leave type: Annual leave; Entitlement: 25 working days per year, accrued monthly; Pay: Full pay" in rows
    assert all(b.page is None for b in en.blocks)


def test_unsupported_file(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(ValueError, match="unsupported"):
        parse_file(tmp_path / "notes.txt")
    assert scan(tmp_path) == []


# --- chunking -----------------------------------------------------------------

def doc(blocks, title="Handbook") -> ParsedDocument:
    return ParsedDocument("x.md", title, "en", "markdown", "h", blocks)


def test_one_chunk_per_section_with_heading_path():
    chunks = chunk_document(doc([
        Block("heading", "Handbook", 1), Block("text", "Intro."),
        Block("heading", "4. Maintenance", 2), Block("heading", "4.2 Response times", 3),
        Block("text", "Emergency within 2 hours.", page=2), Block("text", "Routine within 5 days.", page=3),
        Block("heading", "5. Rules", 2), Block("text", "Quiet hours 22:00 to 07:00."),
    ]))
    assert [(c.section, c.heading_path) for c in chunks] == [
        ("Handbook", ""), ("4.2 Response times", "4. Maintenance > 4.2 Response times"), ("5. Rules", "5. Rules")]
    assert chunks[1].content == "Emergency within 2 hours.\n\nRoutine within 5 days."
    assert (chunks[1].page, chunks[1].page_end) == (2, 3)
    assert chunks[1].embedding_text("Handbook") == "Handbook\n4. Maintenance > 4.2 Response times\n\nEmergency within 2 hours.\n\nRoutine within 5 days."


def test_long_section_is_split_with_overlap():
    paragraphs = [f"Clause {i}. " + "word " * 40 for i in range(1, 9)]  # about 210 characters each
    chunks = chunk_document(doc([Block("heading", "6. Moving out", 2)] + [Block("text", p.strip()) for p in paragraphs]),
                            max_chars=600, overlap_chars=200)
    assert len(chunks) >= 3
    assert all(len(c.content) <= 600 for c in chunks)
    assert all(c.section == "6. Moving out" for c in chunks)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.content[-40:] in b.content  # the end of each window is repeated in the next
    joined = " ".join(c.content for c in chunks)
    assert all(f"Clause {i}." in joined for i in range(1, 9))


def test_very_long_paragraph_is_cut_at_sentences():
    para = " ".join(f"Sentence number {i} has a few words in it." for i in range(60))
    chunks = chunk_document(doc([Block("heading", "1. Long", 2), Block("text", para)]), max_chars=500, overlap_chars=100)
    assert all(len(c.content) <= 500 for c in chunks)
    assert all(c.content.split("\n\n")[-1].endswith(".") for c in chunks)


def test_headings_without_text_make_no_chunks():
    assert chunk_document(doc([Block("heading", "2. Moving in", 2), Block("heading", "3. Rent", 2)])) == []


def test_document_set_chunks():
    for p in scan(DOCS):
        chunks = chunk_document(parse_file(p, DOCS))
        assert chunks and all(c.content.strip() and len(c.content) <= 1200 for c in chunks), p
