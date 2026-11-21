"""Dependency-free lexical ranking helpers for bounded memory recall."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

DEFAULT_MEMORY_CHUNK_CHARS = 1200
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")
_HEADING_PATTERN = re.compile(r"^#{1,6}\s+.+$")


def split_memory_text(
    content: str,
    *,
    max_chunk_chars: int = DEFAULT_MEMORY_CHUNK_CHARS,
) -> tuple[str, ...]:
    """Split Markdown at headings and paragraphs while retaining heading context."""
    if max_chunk_chars < 1:
        raise ValueError("max_chunk_chars must be positive.")
    chunks: list[str] = []
    heading: str | None = None
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        body = "\n".join(paragraph).strip()
        prefix = f"{heading}\n" if heading else ""
        body_chunk_chars = max(1, max_chunk_chars - len(prefix))
        for start in range(0, len(body), body_chunk_chars):
            part = body[start : start + body_chunk_chars]
            chunk = f"{prefix}{part}".strip()
            if chunk:
                chunks.append(chunk)
        paragraph.clear()

    for line in content.splitlines():
        stripped = line.strip()
        if _HEADING_PATTERN.match(stripped):
            flush_paragraph()
            heading = stripped
        elif not stripped:
            flush_paragraph()
        else:
            paragraph.append(stripped)
    flush_paragraph()
    if not chunks and content.strip():
        chunks.append(content.strip()[:max_chunk_chars])
    return tuple(chunks)


def rank_text_indexes(query: str, texts: Sequence[str]) -> tuple[int, ...]:
    """Return matching text indexes by BM25-style score, preserving ties."""
    query_terms = _terms(query)
    if not query_terms or not texts:
        return ()
    document_terms = tuple(_terms(text) for text in texts)
    document_frequency: Counter[str] = Counter()
    for terms in document_terms:
        document_frequency.update(terms.keys())
    average_length = sum(map(len, document_terms)) / max(1, len(document_terms))
    scored: list[tuple[float, int]] = []
    for index, terms in enumerate(document_terms):
        length = len(terms)
        score = 0.0
        for term, query_frequency in query_terms.items():
            frequency = terms.get(term, 0)
            if not frequency:
                continue
            inverse_document_frequency = math.log1p(
                (len(texts) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            normalization = frequency + 1.2 * (0.25 + 0.75 * length / max(1.0, average_length))
            score += query_frequency * inverse_document_frequency * frequency * 2.2 / normalization
        if score > 0:
            scored.append((score, index))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(index for _, index in scored)


def _terms(text: str) -> Counter[str]:
    terms: Counter[str] = Counter()
    for token in _TOKEN_PATTERN.findall(text.casefold()):
        if token.isascii():
            terms[token] += 1
            continue
        if len(token) == 1:
            terms[token] += 1
        for index in range(len(token) - 1):
            terms[token[index : index + 2]] += 1
    return terms


__all__ = ["DEFAULT_MEMORY_CHUNK_CHARS", "rank_text_indexes", "split_memory_text"]
