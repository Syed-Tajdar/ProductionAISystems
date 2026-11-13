"""Chunk a parsed document by headings.

Each section (the text under one heading) becomes one chunk when it fits in max_chars. Longer
sections are split at paragraph, then sentence, boundaries; each following window repeats the
end of the previous one (up to overlap_chars) so a fact that spans the cut is still in one chunk.
Every chunk keeps its heading path, for example "4. Maintenance > 4.2 Response times".
"""

import hashlib
import re
from dataclasses import dataclass

from rag.documents import Block, ParsedDocument

SENTENCE = re.compile(r"(?<=[.!?؟])\s+")


@dataclass
class Chunk:
    index: int
    section: str
    heading_path: str
    content: str
    page: int | None
    page_end: int | None

    def embedding_text(self, title: str) -> str:
        """What gets embedded: the document title and heading path give the text its context."""
        return f"{title}\n{self.heading_path}\n\n{self.content}" if self.heading_path else f"{title}\n\n{self.content}"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pieces(text: str, max_chars: int) -> list[str]:
    """A paragraph longer than max_chars is cut into sentences, and sentences into words if needed."""
    if len(text) <= max_chars:
        return [text]
    out, current = [], ""
    for sentence in SENTENCE.split(text):
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            out.append(sentence[:cut])
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > max_chars:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def _tail(text: str, limit: int) -> str:
    """The last whole words of text, at most limit characters."""
    if len(text) <= limit:
        return text
    cut = text.find(" ", len(text) - limit)
    return text[cut + 1:] if cut != -1 else text[-limit:]


def _windows(paragraphs: list[tuple[str, int | None]], max_chars: int, overlap: int) -> list[list[tuple[str, int | None]]]:
    # Leave room for the overlap so a carried tail plus one piece still fits.
    piece_max = max(max_chars - overlap, max_chars // 2)
    pieces = [(p, page) for text, page in paragraphs for p in _pieces(text, piece_max)]
    windows, current, size = [], [], 0
    for text, page in pieces:
        if current and size + 2 + len(text) > max_chars:
            windows.append(current)
            carried = []
            for prev_text, prev_page in reversed(current):  # repeat the end of the last window
                room = overlap - sum(len(t) + 2 for t, _ in carried)
                if room <= 40:
                    break
                carried.insert(0, (_tail(prev_text, room), prev_page))
                if len(prev_text) > room:
                    break
            while carried and sum(len(t) + 2 for t, _ in carried) + len(text) + 2 > max_chars:
                carried.pop(0)
            current, size = carried, sum(len(t) + 2 for t, _ in carried)
        current.append((text, page))
        size += len(text) + 2
    if current:
        windows.append(current)
    return windows


def chunk_document(doc: ParsedDocument, max_chars: int = 1200, overlap_chars: int = 200) -> list[Chunk]:
    chunks: list[Chunk] = []
    stack: list[Block] = []
    paragraphs: list[tuple[str, int | None]] = []

    def flush():
        if not paragraphs:
            return
        path = [b.text for b in stack if not (b.level == 1 and b.text == doc.title)]
        section = path[-1] if path else doc.title
        for window in _windows(paragraphs, max_chars, overlap_chars):
            pages = [p for _, p in window if p is not None]
            chunks.append(Chunk(len(chunks), section, " > ".join(path), "\n\n".join(t for t, _ in window),
                                min(pages) if pages else None, max(pages) if pages else None))
        paragraphs.clear()

    for block in doc.blocks:
        if block.kind == "heading":
            flush()
            while stack and stack[-1].level >= block.level:
                stack.pop()
            stack.append(block)
        else:
            paragraphs.append((block.text, block.page))
    flush()
    return chunks
