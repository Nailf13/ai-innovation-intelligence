"""
Sophisticated chunking for documents (PDFs, text files).

Strategy:
- Chunk by structural boundaries (headings, paragraphs)
- Target chunk size: 300-900 tokens (~1200-3600 chars)
- Always split at sentence boundaries (never break sentences in the middle)
- Add semantic overlap for continuity
- Preserve metadata: source, page number, section
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
MIN_CHUNK_CHARS = 400       # ~100 tokens min
TARGET_CHUNK_CHARS = 2000   # ~500 tokens target
MAX_CHUNK_CHARS = 3600      # ~900 tokens max

# Regex patterns for structure detection
HEADING_PATTERNS = [
    r'^#+\s+.+$',                          # Markdown headings
    r'^[A-Z][A-Z\s]{3,50}$',               # ALL CAPS headings
    r'^\d+\.\s+[A-Z].+$',                  # Numbered sections: "1. Introduction"
    r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,5}:',  # Title case with colon
    r'^(?:Chapter|Section|Part)\s+\d+',    # Chapter/Section/Part headers
]

# Page break patterns
PAGE_BREAK_PATTERNS = [
    r'\n{3,}',                             # Multiple newlines
    r'\x0c',                               # Form feed character
    r'(?:^|\n)---+(?:\n|$)',              # Markdown horizontal rules
]


@dataclass
class DocumentChunk:
    """A chunk from a document."""
    chunk_id: str
    text: str
    source: str                          # Document identifier
    document_date: Optional[str] = None
    page: Optional[int] = None           # Page number if available
    section: Optional[str] = None        # Section/heading if detected
    overlap_text: str = ""               # Text from previous chunk
    word_count: int = 0
    char_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source": self.source,
            "document_date": self.document_date,
            "page": self.page,
            "section": self.section,
            "overlap_text": self.overlap_text,
            "word_count": self.word_count,
            "char_count": self.char_count,
            "metadata": self.metadata,
        }

    @property
    def full_text(self) -> str:
        """Text with overlap prepended for embedding."""
        if self.overlap_text:
            return f"{self.overlap_text}\n\n{self.text}"
        return self.text


@dataclass
class TextBlock:
    """A structural block of text (paragraph, section, etc.)."""
    text: str
    block_type: str  # "heading", "paragraph", "list", "other"
    page: Optional[int] = None
    section: Optional[str] = None

    @property
    def char_count(self) -> int:
        return len(self.text)


class DocumentChunker:
    """
    Chunker for documents.

    Strategies:
    1. Detect structural boundaries (headings, paragraphs)
    2. Group paragraphs into target-sized chunks
    3. Respect section boundaries where possible
    4. Always split at sentence boundaries (never break sentences)
    5. Add overlap for semantic continuity
    """

    def __init__(
        self,
        min_chars: int = MIN_CHUNK_CHARS,
        target_chars: int = TARGET_CHUNK_CHARS,
        max_chars: int = MAX_CHUNK_CHARS,
        add_overlap: bool = True,
        overlap_sentences: int = 2,
        respect_headings: bool = True,
    ):
        self.min_chars = min_chars
        self.target_chars = target_chars
        self.max_chars = max_chars
        self.add_overlap = add_overlap
        self.overlap_sentences = overlap_sentences
        self.respect_headings = respect_headings

        # Compile patterns
        self.heading_pattern = re.compile(
            '|'.join(HEADING_PATTERNS),
            re.MULTILINE
        )
        self.page_break_pattern = re.compile(
            '|'.join(PAGE_BREAK_PATTERNS)
        )

    def _is_heading(self, line: str) -> bool:
        """Check if a line looks like a heading."""
        line = line.strip()
        if not line or len(line) > 200:  # Too long for heading
            return False
        return bool(self.heading_pattern.match(line))

    def _detect_page_number(self, text: str) -> Optional[int]:
        """Try to extract page number from text."""
        # Look for patterns like "Page 5" or "- 5 -" or just "5" at end of page
        patterns = [
            r'Page\s+(\d+)',
            r'-\s*(\d+)\s*-',
            r'^\s*(\d{1,3})\s*$',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    pass
        return None

    def _split_into_blocks(
        self,
        text: str,
    ) -> List[TextBlock]:
        """
        Split document text into structural blocks.
        """
        blocks: List[TextBlock] = []

        # First split by page breaks
        pages = self.page_break_pattern.split(text)

        current_section = None
        estimated_page = 1

        for page_text in pages:
            page_text = page_text.strip()
            if not page_text:
                continue

            # Try to detect actual page number
            detected_page = self._detect_page_number(page_text)
            if detected_page:
                estimated_page = detected_page

            # Split page into paragraphs
            paragraphs = re.split(r'\n\s*\n', page_text)

            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                # Check if it's a heading
                if self._is_heading(para):
                    current_section = para
                    blocks.append(TextBlock(
                        text=para,
                        block_type="heading",
                        page=estimated_page,
                        section=current_section,
                    ))
                else:
                    blocks.append(TextBlock(
                        text=para,
                        block_type="paragraph",
                        page=estimated_page,
                        section=current_section,
                    ))

            estimated_page += 1

        log.debug(f"[CHUNKER] Split into {len(blocks)} blocks")
        return blocks

    def _get_overlap_text(
        self,
        previous_text: str,
    ) -> str:
        """
        Extract the last N sentences from previous chunk for overlap.
        """
        if not previous_text:
            return ""

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', previous_text)
        if not sentences:
            return ""

        # Take last N sentences
        overlap_sentences = sentences[-self.overlap_sentences:]
        overlap = " ".join(overlap_sentences)

        # Limit overlap length
        max_overlap = self.min_chars // 2
        if len(overlap) > max_overlap:
            overlap = overlap[-max_overlap:]

        return overlap.strip()

    def _split_long_block(
        self,
        text: str,
    ) -> List[str]:
        """
        Split a long text block at sentence boundaries.
        Ensures complete sentences are never broken in the middle.
        """
        if len(text) <= self.max_chars:
            return [text]

        # Split on sentence boundaries with better regex to handle more cases
        # Matches periods, exclamation marks, question marks followed by space and capital letter
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)

        chunks: List[str] = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # If adding this sentence would exceed max AND we have content, save current chunk
            if current and (len(current) + len(sentence) + 1) > self.max_chars:
                chunks.append(current.strip())
                current = sentence
            else:
                # Add sentence to current chunk
                current = f"{current} {sentence}".strip() if current else sentence

        # Don't forget the last chunk
        if current:
            chunks.append(current.strip())

        return chunks

    def chunk_from_segments(
        self,
        segments: List[Dict[str, Any]],
        source: str,
        document_date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[DocumentChunk]:
        """
        Chunk document from segments with page information (preferred method).

        Args:
            segments: List of segments with 'text' and 'page' fields
            source: Document identifier
            document_date: Document date
            metadata: Additional metadata

        Returns:
            List of DocumentChunk objects with accurate page numbers
        """
        metadata = metadata or {}

        # Convert segments to blocks with accurate page numbers
        blocks: List[TextBlock] = []
        current_section = None

        for segment in segments:
            page_text = segment.get("text", "")
            page_num = segment.get("page", 1)

            if not page_text.strip():
                continue

            # Split page into paragraphs
            paragraphs = re.split(r'\n\s*\n', page_text)

            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                # Check if it's a heading
                if self._is_heading(para):
                    current_section = para
                    blocks.append(TextBlock(
                        text=para,
                        block_type="heading",
                        page=page_num,
                        section=current_section,
                    ))
                else:
                    blocks.append(TextBlock(
                        text=para,
                        block_type="paragraph",
                        page=page_num,
                        section=current_section,
                    ))

        if not blocks:
            log.warning(f"[CHUNKER] No blocks extracted from segments")
            return []

        log.debug(f"[CHUNKER] Split into {len(blocks)} blocks from {len(segments)} pages")

        # Use the standard chunking logic with accurate page numbers
        return self._group_blocks_into_chunks(blocks, source, document_date, metadata)

    def chunk(
        self,
        text: str,
        source: str,
        document_date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[DocumentChunk]:
        """
        Chunk document text (legacy method, uses heuristic page detection).

        For better page accuracy, use chunk_from_segments() instead.

        Args:
            text: Full document text
            source: Document identifier
            document_date: Document date
            metadata: Additional metadata

        Returns:
            List of DocumentChunk objects
        """
        metadata = metadata or {}

        # Step 1: Split into structural blocks
        blocks = self._split_into_blocks(text)
        if not blocks:
            log.warning(f"[CHUNKER] No blocks extracted from document")
            return []

        return self._group_blocks_into_chunks(blocks, source, document_date, metadata)

    def _group_blocks_into_chunks(
        self,
        blocks: List[TextBlock],
        source: str,
        document_date: Optional[str],
        metadata: Dict[str, Any],
    ) -> List[DocumentChunk]:
        """
        Group text blocks into chunks (extracted for reuse).

        Args:
            blocks: List of text blocks with page info
            source: Document identifier
            document_date: Document date
            metadata: Additional metadata

        Returns:
            List of DocumentChunk objects
        """

        # Step 2: Group blocks into chunks
        chunks: List[DocumentChunk] = []
        current_texts: List[str] = []
        current_chars = 0
        current_page = None
        current_section = None
        previous_text = ""
        chunk_idx = 0

        def save_current_chunk():
            nonlocal chunks, current_texts, current_chars, chunk_idx, previous_text

            if not current_texts:
                return

            chunk_text = "\n\n".join(current_texts)

            # Get overlap
            overlap = ""
            if self.add_overlap and previous_text:
                overlap = self._get_overlap_text(previous_text)

            chunk = DocumentChunk(
                chunk_id=f"{source}_chunk_{chunk_idx:04d}",
                text=chunk_text,
                source=source,
                document_date=document_date,
                page=current_page,
                section=current_section,
                overlap_text=overlap,
                word_count=len(chunk_text.split()),
                char_count=len(chunk_text),
                metadata=metadata,
            )
            chunks.append(chunk)

            previous_text = chunk_text
            current_texts = []
            current_chars = 0
            chunk_idx += 1

        for block in blocks:
            # Check if we should start a new chunk
            is_heading = block.block_type == "heading"
            would_exceed = current_chars + block.char_count > self.max_chars
            is_large_enough = current_chars >= self.target_chars

            # Start new chunk on heading (if enabled) or if current is full
            if (self.respect_headings and is_heading and current_texts) or \
               (would_exceed and current_texts):
                save_current_chunk()

            # Handle very long blocks
            if block.char_count > self.max_chars:
                # Save current first
                if current_texts:
                    save_current_chunk()

                # Split the long block
                split_texts = self._split_long_block(block.text)
                for split_text in split_texts:
                    current_texts = [split_text]
                    current_chars = len(split_text)
                    current_page = block.page
                    current_section = block.section
                    save_current_chunk()
                continue

            # Add block to current chunk
            current_texts.append(block.text)
            current_chars += block.char_count
            if block.page:
                current_page = block.page
            if block.section:
                current_section = block.section

        # Don't forget last chunk
        save_current_chunk()

        # Post-process: merge tiny chunks
        chunks = self._merge_tiny_chunks(chunks)

        log.info(
            f"[CHUNKER] Created {len(chunks)} chunks from {len(blocks)} blocks "
            f"(avg {sum(c.char_count for c in chunks) // max(len(chunks), 1)} chars/chunk)"
        )

        return chunks

    def _merge_tiny_chunks(
        self,
        chunks: List[DocumentChunk],
    ) -> List[DocumentChunk]:
        """
        Merge chunks that are too small with their neighbors.
        """
        if len(chunks) <= 1:
            return chunks

        merged: List[DocumentChunk] = []

        for chunk in chunks:
            if not merged:
                merged.append(chunk)
                continue

            last = merged[-1]

            # Merge if current chunk is tiny
            if chunk.char_count < self.min_chars:
                combined_text = f"{last.text}\n\n{chunk.text}"
                if len(combined_text) <= self.max_chars:
                    merged[-1] = DocumentChunk(
                        chunk_id=last.chunk_id,
                        text=combined_text,
                        source=last.source,
                        document_date=last.document_date,
                        page=last.page,
                        section=last.section or chunk.section,
                        overlap_text=last.overlap_text,
                        word_count=len(combined_text.split()),
                        char_count=len(combined_text),
                        metadata=last.metadata,
                    )
                    continue

            merged.append(chunk)

        return merged


def chunk_document(
    document_path: Path,
    source: Optional[str] = None,
    document_date: Optional[str] = None,
    **chunker_kwargs,
) -> List[DocumentChunk]:
    """
    Convenience function to chunk a document file.

    Supports .txt files directly, PDFs require pre-extraction.

    Args:
        document_path: Path to document (txt file or extracted text)
        source: Source identifier (defaults to filename)
        document_date: Document date
        **chunker_kwargs: Arguments for DocumentChunker

    Returns:
        List of DocumentChunk objects
    """
    document_path = Path(document_path)
    source = source or document_path.stem

    # Read text content
    with open(document_path, "r", encoding="utf-8") as f:
        text = f.read()

    if not text.strip():
        log.warning(f"Empty document: {document_path.name}")
        return []

    chunker = DocumentChunker(**chunker_kwargs)
    return chunker.chunk(
        text=text,
        source=source,
        document_date=document_date,
    )


def chunk_document_from_text(
    text: str,
    source: str,
    document_date: Optional[str] = None,
    **chunker_kwargs,
) -> List[DocumentChunk]:
    """
    Chunk document from text string directly.

    Args:
        text: Document text content
        source: Source identifier
        document_date: Document date
        **chunker_kwargs: Arguments for DocumentChunker

    Returns:
        List of DocumentChunk objects
    """
    if not text.strip():
        log.warning(f"Empty document text for: {source}")
        return []

    chunker = DocumentChunker(**chunker_kwargs)
    return chunker.chunk(
        text=text,
        source=source,
        document_date=document_date,
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python document_chunker.py <document.txt>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    chunks = chunk_document(file_path)

    print(f"\n{'='*60}")
    print(f"Chunked: {file_path.name}")
    print(f"Total chunks: {len(chunks)}")
    print(f"{'='*60}\n")

    for i, chunk in enumerate(chunks[:5]):
        print(f"[{i}] Page {chunk.page}, Section: {chunk.section or 'N/A'}")
        print(f"    {chunk.text[:100]}...")
        print(f"    ({chunk.char_count} chars, {chunk.word_count} words)")
        print()
