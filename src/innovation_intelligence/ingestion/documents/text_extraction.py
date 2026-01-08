from __future__ import annotations

from pathlib import Path
from typing import Optional

import pymupdf


def extract_text_from_pdf(
    pdf_path: Path,
    *,
    max_pages: Optional[int] = None,
) -> str:
    """
    Extract raw text from a PDF file using PyMuPDF.

    Args:
        pdf_path: Path to the PDF file.
        max_pages: If set, stop after this many pages (for testing / throttling).

    Returns:
        A single string with page texts separated by two newlines.
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = pymupdf.open(pdf_path)
    texts: list[str] = []

    try:
        for i, page in enumerate(doc):
            if max_pages is not None and i >= max_pages:
                break

            page_text = page.get_text("text") or ""
            if page_text.strip():
                texts.append(page_text.strip())
    finally:
        doc.close()

    return "\n\n\n".join(texts)


def pdf_to_text_file(
    pdf_path: Path,
    out_path: Path,
    *,
    max_pages: Optional[int] = None,
) -> Path:
    """
    Extract text from a PDF and save it to a UTF-8 text file.

    Returns:
        Path to the written text file.
    """
    text = extract_text_from_pdf(pdf_path, max_pages=max_pages)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path
