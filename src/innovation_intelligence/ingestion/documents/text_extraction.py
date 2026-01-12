from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Any

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


def extract_text_from_pdf_with_pages(
    pdf_path: Path,
    *,
    max_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Extract text from a PDF file with page information preserved.

    Args:
        pdf_path: Path to the PDF file.
        max_pages: If set, stop after this many pages (for testing / throttling).

    Returns:
        Dict with:
        - text: Full concatenated text
        - pages: List of dicts with page_number and text
        - segments: List of page segments for compatibility
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = pymupdf.open(pdf_path)
    pages: List[Dict[str, Any]] = []
    all_text_parts: List[str] = []

    try:
        for i, page in enumerate(doc):
            if max_pages is not None and i >= max_pages:
                break

            page_num = i + 1  # 1-indexed page numbers
            page_text = page.get_text("text") or ""

            if page_text.strip():
                pages.append({
                    "page_number": page_num,
                    "text": page_text.strip(),
                })
                all_text_parts.append(page_text.strip())
    finally:
        doc.close()

    full_text = "\n\n".join(all_text_parts)

    # Create segments format for compatibility with document chunker
    segments = [
        {"text": page["text"], "page": page["page_number"]}
        for page in pages
    ]

    return {
        "text": full_text,
        "pages": pages,
        "segments": segments,
        "_metadata": {
            "total_pages": len(pages),
            "extraction_method": "pymupdf_with_pages",
        }
    }


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
