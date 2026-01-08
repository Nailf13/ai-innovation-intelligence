"""
Sophisticated chunking for podcast transcripts.

Strategy:
- Chunk primarily by speaker turns and time windows
- Merge short adjacent turns from the same speaker
- Target chunk size: 300-900 tokens (~1200-3600 chars assuming ~4 chars/token)
- Add semantic overlap (last sentence of previous chunk) for continuity
- Preserve metadata: speaker, start/end timestamps, source
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
# Character-based sizing (more practical than token counting)
# Assuming ~4 chars per token: 300-900 tokens = 1200-3600 chars
MIN_CHUNK_CHARS = 400       # ~100 tokens min
TARGET_CHUNK_CHARS = 2000   # ~500 tokens target
MAX_CHUNK_CHARS = 3600      # ~900 tokens max

# Minimum segment duration to consider as standalone
MIN_SEGMENT_DURATION_SECS = 3.0

# Maximum gap between segments to consider them continuous
MAX_GAP_SECS = 5.0


@dataclass
class PodcastChunk:
    """A chunk from a podcast transcript."""
    chunk_id: str
    text: str
    speaker: str
    speakers: List[str]          # All speakers in this chunk
    start: float                 # Start timestamp
    end: float                   # End timestamp
    source: str                  # Episode identifier
    episode_date: Optional[str] = None
    overlap_text: str = ""       # Text from previous chunk for context
    word_count: int = 0
    char_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "speaker": self.speaker,
            "speakers": self.speakers,
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


@dataclass
class SpeakerTurn:
    """A continuous speaking turn from one speaker."""
    speaker: str
    text: str
    start: float
    end: float
    segments: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def char_count(self) -> int:
        return len(self.text)


class PodcastChunker:
    """
    Chunker for podcast transcripts.

    Strategies:
    1. Group segments by speaker turns
    2. Merge short turns from same speaker
    3. Split long turns at sentence boundaries
    4. Add overlap for semantic continuity
    """

    def __init__(
        self,
        min_chars: int = MIN_CHUNK_CHARS,
        target_chars: int = TARGET_CHUNK_CHARS,
        max_chars: int = MAX_CHUNK_CHARS,
        add_overlap: bool = True,
        overlap_sentences: int = 1,
    ):
        self.min_chars = min_chars
        self.target_chars = target_chars
        self.max_chars = max_chars
        self.add_overlap = add_overlap
        self.overlap_sentences = overlap_sentences

    def _extract_turns(
        self,
        segments: List[Dict[str, Any]],
    ) -> List[SpeakerTurn]:
        """
        Group consecutive segments by speaker into turns.
        """
        if not segments:
            return []

        turns: List[SpeakerTurn] = []
        current_speaker = None
        current_texts: List[str] = []
        current_start = None
        current_end = None
        current_segments: List[Dict[str, Any]] = []

        for seg in segments:
            speaker = seg.get("speaker", seg.get("speaker_name", "UNKNOWN"))
            text = (seg.get("text") or "").strip()
            start = seg.get("start", 0)
            end = seg.get("end", start)

            if not text:
                continue

            # Check if this is a new speaker or significant gap
            is_new_speaker = speaker != current_speaker
            is_gap = current_end is not None and (start - current_end) > MAX_GAP_SECS

            if is_new_speaker or is_gap:
                # Save current turn
                if current_speaker is not None and current_texts:
                    turns.append(SpeakerTurn(
                        speaker=current_speaker,
                        text=" ".join(current_texts),
                        start=current_start,
                        end=current_end,
                        segments=current_segments,
                    ))

                # Start new turn
                current_speaker = speaker
                current_texts = [text]
                current_start = start
                current_end = end
                current_segments = [seg]
            else:
                # Continue current turn
                current_texts.append(text)
                current_end = end
                current_segments.append(seg)

        # Don't forget last turn
        if current_speaker is not None and current_texts:
            turns.append(SpeakerTurn(
                speaker=current_speaker,
                text=" ".join(current_texts),
                start=current_start,
                end=current_end,
                segments=current_segments,
            ))

        log.debug(f"[CHUNKER] Extracted {len(turns)} speaker turns from {len(segments)} segments")
        return turns

    def _merge_short_turns(
        self,
        turns: List[SpeakerTurn],
    ) -> List[SpeakerTurn]:
        """
        Merge very short adjacent turns from the same speaker.
        """
        if not turns:
            return []

        merged: List[SpeakerTurn] = []

        for turn in turns:
            if not merged:
                merged.append(turn)
                continue

            last = merged[-1]

            # Merge if same speaker and both are short
            same_speaker = last.speaker == turn.speaker
            both_short = last.char_count < self.min_chars and turn.char_count < self.min_chars
            gap_ok = (turn.start - last.end) < MAX_GAP_SECS

            if same_speaker and (both_short or gap_ok) and (last.char_count + turn.char_count) < self.max_chars:
                # Merge
                merged[-1] = SpeakerTurn(
                    speaker=last.speaker,
                    text=f"{last.text} {turn.text}",
                    start=last.start,
                    end=turn.end,
                    segments=last.segments + turn.segments,
                )
            else:
                merged.append(turn)

        log.debug(f"[CHUNKER] Merged {len(turns)} turns into {len(merged)}")
        return merged

    def _split_long_turn(
        self,
        turn: SpeakerTurn,
    ) -> List[Tuple[str, float, float]]:
        """
        Split a long turn into multiple chunks at sentence boundaries.

        Returns list of (text, start, end) tuples.
        """
        text = turn.text
        if len(text) <= self.max_chars:
            return [(text, turn.start, turn.end)]

        # Split on sentence boundaries
        # Look for periods followed by space and capital letter, or newlines
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])|(?<=\n)', text)

        chunks: List[Tuple[str, float, float]] = []
        current_text = ""
        duration = turn.duration
        total_chars = len(text)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if len(current_text) + len(sentence) > self.max_chars and current_text:
                # Save current chunk
                # Estimate timestamps based on character position
                start_ratio = (total_chars - len(text) + len(current_text) - len(current_text)) / max(total_chars, 1)
                end_ratio = (total_chars - len(text) + len(current_text)) / max(total_chars, 1)
                chunk_start = turn.start + (duration * start_ratio)
                chunk_end = turn.start + (duration * end_ratio)

                chunks.append((current_text.strip(), chunk_start, chunk_end))
                current_text = sentence
            else:
                current_text = f"{current_text} {sentence}".strip() if current_text else sentence

        # Last chunk
        if current_text:
            if chunks:
                chunk_start = chunks[-1][2]  # Start where last ended
            else:
                chunk_start = turn.start
            chunks.append((current_text.strip(), chunk_start, turn.end))

        return chunks

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

    def chunk(
        self,
        segments: List[Dict[str, Any]],
        source: str,
        episode_date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[PodcastChunk]:
        """
        Chunk podcast transcript segments.

        Args:
            segments: List of transcript segment dicts
            source: Episode identifier
            episode_date: Episode publication date
            metadata: Additional metadata to include

        Returns:
            List of PodcastChunk objects
        """
        metadata = metadata or {}

        # Step 1: Extract speaker turns
        turns = self._extract_turns(segments)
        if not turns:
            log.warning(f"[CHUNKER] No turns extracted from {len(segments)} segments")
            return []

        # Step 2: Merge short consecutive turns
        turns = self._merge_short_turns(turns)

        # Step 3: Create chunks from turns
        chunks: List[PodcastChunk] = []
        previous_text = ""
        chunk_idx = 0

        for turn in turns:
            # Split long turns if needed
            turn_parts = self._split_long_turn(turn)

            for part_text, part_start, part_end in turn_parts:
                # Get overlap from previous chunk
                overlap = ""
                if self.add_overlap and previous_text:
                    overlap = self._get_overlap_text(previous_text)

                word_count = len(part_text.split())
                char_count = len(part_text)

                chunk = PodcastChunk(
                    chunk_id=f"{source}_chunk_{chunk_idx:04d}",
                    text=part_text,
                    speaker=turn.speaker,
                    speakers=[turn.speaker],
                    start=part_start,
                    end=part_end,
                    source=source,
                    episode_date=episode_date,
                    overlap_text=overlap,
                    word_count=word_count,
                    char_count=char_count,
                    metadata=metadata,
                )
                chunks.append(chunk)

                previous_text = part_text
                chunk_idx += 1

        # Post-process: merge tiny chunks with neighbors
        chunks = self._merge_tiny_chunks(chunks)

        log.info(
            f"[CHUNKER] Created {len(chunks)} chunks from {len(segments)} segments "
            f"(avg {sum(c.char_count for c in chunks) // max(len(chunks), 1)} chars/chunk)"
        )

        return chunks

    def _merge_tiny_chunks(
        self,
        chunks: List[PodcastChunk],
    ) -> List[PodcastChunk]:
        """
        Merge chunks that are too small with their neighbors.
        """
        if len(chunks) <= 1:
            return chunks

        merged: List[PodcastChunk] = []

        for chunk in chunks:
            if not merged:
                merged.append(chunk)
                continue

            last = merged[-1]

            # Merge if current chunk is tiny and same speaker
            if chunk.char_count < self.min_chars and chunk.speaker == last.speaker:
                # Merge with previous
                combined_text = f"{last.text} {chunk.text}"
                if len(combined_text) <= self.max_chars:
                    merged[-1] = PodcastChunk(
                        chunk_id=last.chunk_id,
                        text=combined_text,
                        speaker=last.speaker,
                        speakers=list(set(last.speakers + chunk.speakers)),
                        start=last.start,
                        end=chunk.end,
                        source=last.source,
                        episode_date=last.episode_date,
                        overlap_text=last.overlap_text,
                        word_count=len(combined_text.split()),
                        char_count=len(combined_text),
                        metadata=last.metadata,
                    )
                    continue

            merged.append(chunk)

        return merged


def chunk_podcast_transcript(
    transcript_path: Path,
    source: Optional[str] = None,
    episode_date: Optional[str] = None,
    **chunker_kwargs,
) -> List[PodcastChunk]:
    """
    Convenience function to chunk a podcast transcript file.

    Args:
        transcript_path: Path to transcript JSON
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
        "speaker_map": data.get("speaker_map", {}),
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
        print(f"[{i}] {chunk.speaker} ({chunk.start:.1f}s - {chunk.end:.1f}s)")
        print(f"    {chunk.text[:100]}...")
        print(f"    ({chunk.char_count} chars, {chunk.word_count} words)")
        print()
