"""
Simplified chunking for podcast transcripts.

Strategy:
- Use text segments directly from transcription
- Each segment becomes a chunk with its original start/end timestamps
- Add semantic overlap (last sentence of previous chunk) for continuity
- Preserve metadata: start/end timestamps, source
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
# Number of sentences to use for overlap context
DEFAULT_OVERLAP_SENTENCES = 1


@dataclass
class PodcastChunk:
    """A chunk from a podcast transcript.

    Note: source format should be "podcast_name - episode_title"
    """
    chunk_id: str
    text: str
    start: float                 # Start timestamp
    end: float                   # End timestamp
    source: str                  # Format: "podcast_name - episode_title"
    episode_date: Optional[str] = None
    overlap_text: str = ""       # Text from previous chunk for context
    word_count: int = 0
    char_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "source": self.source,
            "episode_date": self.episode_date,
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


class PodcastChunker:
    """
    Chunker for podcast transcripts.

    Strategy:
    - Use segments directly from transcription
    - No speaker processing, no merging, no splitting
    - Add overlap for semantic continuity
    """

    def __init__(
        self,
        add_overlap: bool = True,
        overlap_sentences: int = DEFAULT_OVERLAP_SENTENCES,
    ):
        self.add_overlap = add_overlap
        self.overlap_sentences = overlap_sentences

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

        return overlap.strip()

    def chunk(
        self,
        segments: List[Dict[str, Any]],
        source: str,
        episode_date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[PodcastChunk]:
        """
        Chunk podcast transcript segments.

        Each segment from the transcript becomes a chunk with its original boundaries.
        Sentences are never broken as segments typically contain complete sentences/phrases.

        Args:
            segments: List of transcript segment dicts with 'text', 'start', 'end'
            source: Episode identifier
            episode_date: Episode publication date
            metadata: Additional metadata to include

        Returns:
            List of PodcastChunk objects
        """
        metadata = metadata or {}

        if not segments:
            log.warning(f"[CHUNKER] No segments provided")
            return []

        chunks: List[PodcastChunk] = []
        previous_text = ""

        for idx, seg in enumerate(segments):
            text = (seg.get("text") or "").strip()
            if not text:
                continue

            start = seg.get("start", 0)
            end = seg.get("end", start)

            # Get overlap from previous chunk
            overlap = ""
            if self.add_overlap and previous_text:
                overlap = self._get_overlap_text(previous_text)

            word_count = len(text.split())
            char_count = len(text)

            chunk = PodcastChunk(
                chunk_id=f"{source}_chunk_{idx:04d}",
                text=text,
                start=start,
                end=end,
                source=source,
                episode_date=episode_date,
                overlap_text=overlap,
                word_count=word_count,
                char_count=char_count,
                metadata=metadata,
            )
            chunks.append(chunk)
            previous_text = text

        log.info(
            f"[CHUNKER] Created {len(chunks)} chunks from {len(segments)} segments "
            f"(avg {sum(c.char_count for c in chunks) // max(len(chunks), 1)} chars/chunk)"
        )

        return chunks


def chunk_podcast_transcript(
    transcript_path: Path,
    source: Optional[str] = None,
    episode_date: Optional[str] = None,
    **chunker_kwargs,
) -> List[PodcastChunk]:
    """
    Convenience function to chunk a podcast transcript file (local file only).

    NOTE: This function is for CLI/testing with local files only.
    For production use with GCS, use chunk_from_gcs() or PodcastChunker.chunk() directly.

    Args:
        transcript_path: Path to LOCAL transcript JSON file
        source: Source identifier (defaults to filename)
        episode_date: Episode date
        **chunker_kwargs: Arguments for PodcastChunker

    Returns:
        List of PodcastChunk objects
    """
    transcript_path = Path(transcript_path)
    source = source or transcript_path.stem

    with open(transcript_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    segments = data.get("segments", [])
    if not segments:
        log.warning(f"No segments in {transcript_path.name}")
        return []

    # Try to get episode metadata
    metadata = {
        "episode_title": data.get("episode_title", source),
    }

    if episode_date is None:
        episode_date = data.get("episode_date")

    chunker = PodcastChunker(**chunker_kwargs)
    return chunker.chunk(
        segments,
        source=source,
        episode_date=episode_date,
        metadata=metadata,
    )


def chunk_from_gcs(
    gcs_transcript_uri: str,
    source: str,
    episode_date: Optional[str] = None,
    **chunker_kwargs,
) -> List[PodcastChunk]:
    """
    Chunk a podcast transcript from GCS (GCS-first production use).

    Args:
        gcs_transcript_uri: GCS URI (gs://bucket/podcasts/transcripts/episode.json)
        source: Episode identifier (format: "podcast_name - episode_title")
        episode_date: Episode publication date
        **chunker_kwargs: Arguments for PodcastChunker

    Returns:
        List of PodcastChunk objects

    Example:
        chunks = chunk_from_gcs(
            gcs_transcript_uri="gs://bucket/podcasts/transcripts/episode_123.json",
            source="Huberman Lab - Episode 123",
            episode_date="2024-01-15"
        )
    """
    from innovation_intelligence.ingestion.gcs_service import GCSStorageService

    gcs_service = GCSStorageService()
    data = gcs_service.read_json(gcs_transcript_uri)

    segments = data.get("segments", [])
    if not segments:
        log.warning(f"No segments in {gcs_transcript_uri}")
        return []

    # Extract episode metadata
    metadata = {
        "episode_title": data.get("episode_title", source),
    }

    if episode_date is None:
        episode_date = data.get("episode_date")

    chunker = PodcastChunker(**chunker_kwargs)
    return chunker.chunk(
        segments,
        source=source,
        episode_date=episode_date,
        metadata=metadata,
    )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python podcast_chunker.py <transcript.json>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    chunks = chunk_podcast_transcript(file_path)

    print(f"\n{'='*60}")
    print(f"Chunked: {file_path.name}")
    print(f"Total chunks: {len(chunks)}")
    print(f"{'='*60}\n")

    for i, chunk in enumerate(chunks[:5]):
        print(f"[{i}] ({chunk.start:.1f}s - {chunk.end:.1f}s)")
        print(f"    {chunk.text[:100]}...")
        print(f"    ({chunk.char_count} chars, {chunk.word_count} words)")
        print()
