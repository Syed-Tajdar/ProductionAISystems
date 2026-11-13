"""Ingest a folder: parse, chunk, embed (through the cache) and store. Unchanged files are skipped."""

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import yaml
from docx.opc.exceptions import OpcError
from pypdf.errors import PyPdfError

from rag.chunking import chunk_document, content_hash
from rag.db import document_hash, prune_documents, save_document
from rag.documents import parse_file, scan
from rag.embeddings import Embedder

log = logging.getLogger(__name__)
# What one file can fail with: unreadable or malformed (text, PDF, DOCX, front matter), or its lookup in the database.
FILE_ERRORS = (OSError, ValueError, KeyError, zipfile.BadZipFile, yaml.YAMLError, PyPdfError, OpcError, psycopg.Error)


@dataclass
class IngestReport:
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    chunks: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


async def ingest_folder(conn, embedder: Embedder, folder: Path, *, max_chars: int = 1200, overlap_chars: int = 200,
                        prune: bool = False, force: bool = False) -> IngestReport:
    """Parse and chunk every changed file first, then embed all their chunks together, so the
    provider sees as few batch requests as possible (one per 100 chunks with Gemini)."""
    report = IngestReport()
    paths = scan(folder)
    pending = []  # (relative path, document, chunks, embedding texts)
    for path in paths:
        rel = str(path.relative_to(folder))
        try:
            doc = parse_file(path, folder)
            if not force and await document_hash(conn, doc.path) == doc.content_hash:
                report.skipped.append(rel)
                continue
            chunks = chunk_document(doc, max_chars, overlap_chars)
            if not chunks:
                raise ValueError("no text found")
            pending.append((rel, doc, chunks, [c.embedding_text(doc.title) for c in chunks]))
        except FILE_ERRORS as e:  # one bad file must not stop the others
            report.failed[rel] = f"{type(e).__name__}: {e}"[:300]
            log.warning("%s failed: %s", rel, e)

    texts = [t for *_, doc_texts in pending for t in doc_texts]
    titles = [doc.title for _, doc, _, doc_texts in pending for _ in doc_texts]
    vectors = await embedder.embed(texts, "document", titles) if texts else []
    offset = 0
    for rel, doc, chunks, doc_texts in pending:
        doc_vectors = vectors[offset:offset + len(doc_texts)]
        offset += len(doc_texts)
        await save_document(conn, doc, chunks, [content_hash(t) for t in doc_texts], doc_vectors)
        report.ingested.append(rel)
        report.chunks += len(chunks)
        log.info("%s: %d chunks", rel, len(chunks))
    if prune:
        report.removed = await prune_documents(conn, [str(p.relative_to(folder)) for p in paths])
    return report
