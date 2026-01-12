# src/innovation_intelligence/ingestion/podcasts/transcript_transformer.py
"""
Transcript transformer module for podcast episodes.

Handles:
- Loading transcript JSON files
- Saving transformed transcripts
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


@dataclass
class TransformedTranscript:
    """Result of transcript transformation."""
    episode_title: str
    original_path: Path
    segments: List[Dict[str, Any]]
    metadata: Dict[str, Any]


def load_transcript(file_path: Path) -> Dict[str, Any]:
    """
    Load a transcript JSON file from local filesystem.

    NOTE: This function is for CLI/testing with local files only.
    For production use with GCS, use load_transcript_from_gcs() or GCS service directly.

    Args:
        file_path: Path to the LOCAL transcript JSON file

    Returns:
        Full transcript data dictionary
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_transcript_from_gcs(gcs_uri: str) -> Dict[str, Any]:
    """
    Load a transcript JSON file from GCS (GCS-first production use).

    Args:
        gcs_uri: GCS URI (gs://bucket/podcasts/transcripts/episode.json)

    Returns:
        Full transcript data dictionary
    """
    from innovation_intelligence.ingestion.gcs_service import GCSStorageService

    gcs_service = GCSStorageService()
    return gcs_service.read_json(gcs_uri)


def transform_transcript(
    transcript_path: Path,
    episode_title: Optional[str] = None,
) -> TransformedTranscript:
    """
    Transform a transcript for processing (local file only).

    NOTE: This function is for CLI/testing with local files only.
    For production use with GCS, use transform_transcript_from_gcs().

    Args:
        transcript_path: Path to the LOCAL transcript JSON file
        episode_title: Episode title (uses filename if not provided)

    Returns:
        TransformedTranscript with segments
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
            metadata=data,
        )

    # Preserve other metadata from original transcript
    metadata = {k: v for k, v in data.items() if k != "segments"}

    return TransformedTranscript(
        episode_title=episode_title,
        original_path=transcript_path,
        segments=segments,
        metadata=metadata,
    )


def transform_transcript_from_gcs(
    gcs_uri: str,
    episode_title: str,
) -> TransformedTranscript:
    """
    Transform a transcript from GCS for processing (GCS-first production use).

    Args:
        gcs_uri: GCS URI (gs://bucket/podcasts/transcripts/episode.json)
        episode_title: Episode title (required)

    Returns:
        TransformedTranscript with segments

    Example:
        transformed = transform_transcript_from_gcs(
            gcs_uri="gs://bucket/podcasts/transcripts/episode_123.json",
            episode_title="Huberman Lab - Episode 123"
        )
    """
    log.info(f"[TRANSFORM] Loading transcript from GCS: {gcs_uri}")

    # Load transcript from GCS
    data = load_transcript_from_gcs(gcs_uri)
    segments = data.get("segments", [])

    if not segments:
        log.warning(f"[TRANSFORM] No segments in {gcs_uri}")
        return TransformedTranscript(
            episode_title=episode_title,
            original_path=Path(gcs_uri),  # Store GCS URI as path for reference
            segments=[],
            metadata=data,
        )

    # Preserve other metadata from original transcript
    metadata = {k: v for k, v in data.items() if k != "segments"}

    return TransformedTranscript(
        episode_title=episode_title,
        original_path=Path(gcs_uri),  # Store GCS URI as path for reference
        segments=segments,
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
) -> Path:
    """
    Transform a transcript and save to output directory.

    Args:
        transcript_path: Path to the input transcript
        output_dir: Directory for output files
        episode_title: Episode title (optional)

    Returns:
        Path to the saved transformed transcript
    """
    transformed = transform_transcript(
        transcript_path,
        episode_title,
    )

    output_path = Path(output_dir) / f"{transformed.original_path.stem}_transformed.json"
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
