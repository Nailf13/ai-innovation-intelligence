# src/innovation_intelligence/ingestion/podcasts/transcript_transformer.py
"""
Transcript transformer module for podcast episodes.

Handles:
- Loading transcript JSON files
- Applying speaker identification to replace speaker IDs with names
- Saving transformed transcripts
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from innovation_intelligence.logger import get_logger
from innovation_intelligence.llm.tools.speaker_identification_tool import (
    identify_speakers_from_file,
    SpeakerIdentificationResult,
)

log = get_logger(__name__)


@dataclass
class TransformedTranscript:
    """Result of transcript transformation."""
    episode_title: str
    original_path: Path
    segments: List[Dict[str, Any]]
    speaker_map: Dict[str, str]
    metadata: Dict[str, Any]


def load_transcript(file_path: Path) -> Dict[str, Any]:
    """
    Load a transcript JSON file.

    Args:
        file_path: Path to the transcript JSON

    Returns:
        Full transcript data dictionary
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_speaker_mapping(
    segments: List[Dict[str, Any]],
    speaker_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Apply speaker name mapping to transcript segments.

    Replaces 'speaker' field values with identified names.
    Adds 'speaker_name' field while preserving original 'speaker' as 'speaker_id'.

    Args:
        segments: List of segment dictionaries
        speaker_map: Mapping from speaker_id to speaker_name

    Returns:
        Transformed segments with speaker names
    """
    transformed = []

    for seg in segments:
        new_seg = seg.copy()

        # Get original speaker ID
        speaker_id = seg.get("speaker", "UNKNOWN")

        # Add speaker_name (resolved name)
        speaker_name = speaker_map.get(speaker_id, speaker_id)
        new_seg["speaker_name"] = speaker_name

        # Keep original speaker as speaker_id for reference
        new_seg["speaker_id"] = speaker_id

        # Update the main 'speaker' field to use the name
        new_seg["speaker"] = speaker_name

        # Also update words if present (WhisperX format)
        if "words" in new_seg:
            new_words = []
            for word in new_seg["words"]:
                new_word = word.copy()
                word_speaker_id = word.get("speaker", speaker_id)
                new_word["speaker_id"] = word_speaker_id
                new_word["speaker"] = speaker_map.get(word_speaker_id, word_speaker_id)
                new_words.append(new_word)
            new_seg["words"] = new_words

        transformed.append(new_seg)

    return transformed


def transform_transcript(
    transcript_path: Path,
    episode_title: Optional[str] = None,
    *,
    speaker_map: Optional[Dict[str, str]] = None,
    run_identification: bool = True,
) -> TransformedTranscript:
    """
    Transform a transcript by identifying and replacing speaker IDs.

    Args:
        transcript_path: Path to the transcript JSON
        episode_title: Episode title (uses filename if not provided)
        speaker_map: Pre-computed speaker mapping (skips identification if provided)
        run_identification: Whether to run speaker identification (default True)

    Returns:
        TransformedTranscript with updated segments
    """
    transcript_path = Path(transcript_path)

    if episode_title is None:
        episode_title = transcript_path.stem

    log.info(f"[TRANSFORM] Loading transcript: {transcript_path.name}")

    # Load original transcript
    data = load_transcript(transcript_path)
    segments = data.get("segments", [])

    if not segments:
        log.warning(f"[TRANSFORM] No segments in {transcript_path.name}")
        return TransformedTranscript(
            episode_title=episode_title,
            original_path=transcript_path,
            segments=[],
            speaker_map={},
            metadata=data,
        )

    # Get or compute speaker mapping
    if speaker_map is None and run_identification:
        log.info(f"[TRANSFORM] Running speaker identification for: {episode_title}")
        result = identify_speakers_from_file(transcript_path, episode_title)
        speaker_map = result.speaker_map
    elif speaker_map is None:
        # No identification, create identity mapping
        unique_speakers = set(seg.get("speaker", "UNKNOWN") for seg in segments)
        speaker_map = {s: s for s in unique_speakers}

    log.info(f"[TRANSFORM] Speaker map: {speaker_map}")

    # Apply mapping
    transformed_segments = apply_speaker_mapping(segments, speaker_map)

    # Preserve other metadata from original transcript
    metadata = {k: v for k, v in data.items() if k != "segments"}

    return TransformedTranscript(
        episode_title=episode_title,
        original_path=transcript_path,
        segments=transformed_segments,
        speaker_map=speaker_map,
        metadata=metadata,
    )


def save_transformed_transcript(
    transformed: TransformedTranscript,
    output_path: Path,
) -> Path:
    """
    Save a transformed transcript to JSON.

    Args:
        transformed: The transformed transcript
        output_path: Where to save the output

    Returns:
        Path to the saved file
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build output data
    output_data = {
        **transformed.metadata,
        "episode_title": transformed.episode_title,
        "speaker_map": transformed.speaker_map,
        "segments": transformed.segments,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    log.info(f"[TRANSFORM] Saved transformed transcript: {output_path}")
    return output_path


def transform_and_save(
    transcript_path: Path,
    output_dir: Path,
    episode_title: Optional[str] = None,
    *,
    speaker_map: Optional[Dict[str, str]] = None,
) -> Path:
    """
    Transform a transcript and save to output directory.

    Args:
        transcript_path: Path to the input transcript
        output_dir: Directory for output files
        episode_title: Episode title (optional)
        speaker_map: Pre-computed speaker mapping (optional)

    Returns:
        Path to the saved transformed transcript
    """
    transformed = transform_transcript(
        transcript_path,
        episode_title,
        speaker_map=speaker_map,
    )

    output_path = Path(output_dir) / f"{transformed.original_path.stem}_identified.json"
    return save_transformed_transcript(transformed, output_path)


def batch_transform_transcripts(
    input_dir: Path,
    output_dir: Path,
    *,
    pattern: str = "*.json",
) -> List[Path]:
    """
    Transform all transcripts in a directory.

    Args:
        input_dir: Directory containing transcript JSONs
        output_dir: Directory for output files
        pattern: Glob pattern for input files

    Returns:
        List of paths to transformed files
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    transcript_files = sorted(input_dir.glob(pattern))
    log.info(f"[TRANSFORM] Found {len(transcript_files)} transcripts in {input_dir}")

    output_paths = []
    for i, file_path in enumerate(transcript_files, 1):
        log.info(f"[TRANSFORM] Processing {i}/{len(transcript_files)}: {file_path.name}")

        try:
            output_path = transform_and_save(file_path, output_dir)
            output_paths.append(output_path)
        except Exception as e:
            log.error(f"[TRANSFORM] Failed to transform {file_path.name}: {e}")

    log.info(f"[TRANSFORM] Completed: {len(output_paths)}/{len(transcript_files)} transcripts")
    return output_paths


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python transcript_transformer.py <input_path> <output_dir> [episode_title]")
        print("  input_path: Single transcript JSON or directory of transcripts")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    title = sys.argv[3] if len(sys.argv) > 3 else None

    if input_path.is_dir():
        results = batch_transform_transcripts(input_path, output_dir)
        print(f"\nTransformed {len(results)} transcripts")
    else:
        result = transform_and_save(input_path, output_dir, title)
        print(f"\nTransformed: {result}")
