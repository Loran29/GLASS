"""PDF full-text chunk indexer for the hybrid RAG system.

Extracts text from the academic PDFs in Papers/CaseStudy/, splits
each page into ~600-char paragraph-aware chunks, and returns a flat
list of PDFChunk objects that can be indexed alongside the structured
KB items.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from knowledge.models import LiteratureReference

logger = logging.getLogger(__name__)


@dataclass
class PDFChunk:
    """One text chunk extracted from an academic PDF."""

    paper_id: int
    paper_title: str
    paper_authors: str
    paper_year: int
    domain: str
    page: int
    chunk_index: int
    text: str
    kb_text: str  # formatted for BM25/dense indexing (includes abstract context)
    abstract_context: str = ""  # first ~500 chars of the paper, prepended to kb_text


def _chunk_page_text(text: str, chunk_size: int = 600) -> list[str]:
    """Split page text into paragraph-aware chunks with 1-paragraph overlap."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for i, para in enumerate(paragraphs):
        if current_len + len(para) + 1 > chunk_size and current_parts:
            chunks.append(" ".join(current_parts))
            # Overlap: keep last paragraph as start of next chunk.
            current_parts = [current_parts[-1]] if current_parts else []
            current_len = len(current_parts[0]) if current_parts else 0

        current_parts.append(para)
        current_len += len(para) + 1

    if current_parts:
        chunks.append(" ".join(current_parts))

    return chunks


def _extract_pdf_filename(source_location: str) -> str | None:
    """Extract the PDF filename from a source_location string."""
    if not source_location or "Papers/CaseStudy/" not in source_location:
        return None
    # Source location format: "Papers/CaseStudy/filename.pdf — some notes"
    # Separator can be " — " (em dash) or ", pp. X" or just end of string.
    part = source_location.split("Papers/CaseStudy/")[-1]
    # Strip everything after the first " — " or " - " separator
    for sep in (" — ", " – ", " - "):
        if sep in part:
            part = part.split(sep)[0].strip()
            break
    # Also strip comma-based page references if present
    if "," in part and ".pdf" in part:
        pdf_end = part.index(".pdf") + 4
        part = part[:pdf_end]
    filename = part.strip()
    if not filename.lower().endswith(".pdf"):
        filename = filename + ".pdf"
    return filename


def _extract_abstract_context(reader: Any, max_chars: int = 500) -> str:
    """Extract the first meaningful text block from a PDF as paper-level context.

    Prepended to every chunk's ``kb_text`` so embeddings capture the
    paper's topic even for out-of-context chunks (tables, results sections).
    """
    for page_idx in range(min(2, len(reader.pages))):
        try:
            raw = reader.pages[page_idx].extract_text() or ""
        except Exception:
            continue
        cleaned = " ".join(raw.split())
        if len(cleaned) >= 150:
            return cleaned[:max_chars]
    return ""


def _title_words(text: str) -> set[str]:
    """Lowercase alphanumeric words from a title or filename."""
    return set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def _match_by_title(title: str, pdf_paths: list[Path]) -> Path | None:
    """Find the best-matching PDF for a paper title using Jaccard word overlap."""
    title_w = _title_words(title)
    if not title_w:
        return None
    best_path: Path | None = None
    best_score = 0.0
    for pdf in pdf_paths:
        stem_w = _title_words(pdf.stem)
        if not stem_w:
            continue
        score = len(title_w & stem_w) / len(title_w | stem_w)
        if score > best_score:
            best_score = score
            best_path = pdf
    # Require at least 30% word overlap to accept the match.
    return best_path if best_score >= 0.30 else None


def load_pdf_chunks(
    papers_dir: Path,
    literature: list[LiteratureReference],
) -> list[PDFChunk]:
    """Load and chunk all PDFs referenced in the literature list.

    Parameters
    ----------
    papers_dir:
        Directory containing the PDF files (e.g. Papers/CaseStudy/).
    literature:
        LiteratureReference list from the knowledge base.
    """
    try:
        import pypdf  # type: ignore
    except ImportError:
        logger.warning("pypdf not installed — PDF indexing disabled. Run: pip install pypdf")
        return []

    all_pdf_paths = list(papers_dir.glob("*.pdf")) if papers_dir.exists() else []
    all_chunks: list[PDFChunk] = []

    for lit in literature:
        # --- Resolve PDF path: source_location first, then title match ---
        pdf_path: Path | None = None
        filename = _extract_pdf_filename(lit.source_location or "")
        if filename:
            candidate = papers_dir / filename
            if candidate.exists():
                pdf_path = candidate
            else:
                logger.debug("Paper %d: source_location path not found (%s), trying title match.", lit.paper_id, filename)

        if pdf_path is None:
            pdf_path = _match_by_title(lit.title, all_pdf_paths)
            if pdf_path:
                logger.debug("Paper %d (%s): matched by title → %s", lit.paper_id, lit.title[:40], pdf_path.name)
            else:
                logger.debug("Paper %d (%s): no PDF found, skipping.", lit.paper_id, lit.title[:40])
                continue

        try:
            reader = pypdf.PdfReader(str(pdf_path))
        except Exception as exc:
            logger.warning("Paper %d: failed to open %s: %s", lit.paper_id, pdf_path.name, exc)
            continue

        chunk_counter = 0
        authors_short = lit.authors.split(";")[0].split(",")[0].strip()
        prefix = f"[Paper {lit.paper_id} | {authors_short} {lit.year} | {lit.domain}]"
        abstract_context = _extract_abstract_context(reader)

        for page_num, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                continue

            raw_chunks = _chunk_page_text(page_text)
            for chunk_text in raw_chunks:
                if len(chunk_text) < 100:
                    continue  # skip headers, page numbers, etc.

                if abstract_context:
                    kb_text = f"{prefix} CONTEXT: {abstract_context} CHUNK: {chunk_text}"
                else:
                    kb_text = f"{prefix} {chunk_text}"
                all_chunks.append(PDFChunk(
                    paper_id=lit.paper_id,
                    paper_title=lit.title,
                    paper_authors=lit.authors,
                    paper_year=lit.year,
                    domain=lit.domain,
                    page=page_num,
                    chunk_index=chunk_counter,
                    text=chunk_text,
                    kb_text=kb_text,
                    abstract_context=abstract_context,
                ))
                chunk_counter += 1

        if chunk_counter > 0:
            logger.debug("Paper %d (%s): indexed %d chunks.", lit.paper_id, lit.title[:40], chunk_counter)

    return all_chunks
