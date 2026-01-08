# src/innovation_intelligence/llm/tools/speaker_identification_tool.py
"""
Speaker identification tool for podcast transcripts.

Identifies speakers in podcast transcripts based on:
- Episode title (often contains guest names)
- Representative excerpts from the transcript (intro, middle, later segments)

Uses Claude via Bedrock with tool-use pattern for structured output.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from innovation_intelligence.config import settings
from innovation_intelligence.llm.bedrock_client import BedrockClient
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
# Target total characters for representative excerpt
DEFAULT_EXCERPT_CHARS = 150_000

# How many segments to sample from different parts of the transcript
INTRO_SEGMENTS = 100      # First ~100 segments (intro/opening)
MIDDLE_SEGMENTS = 50      # Some segments from the middle
LATE_SEGMENTS = 50        # Some segments from later in the episode

# Minimum confidence to consider a speaker identified
MIN_CONFIDENCE_THRESHOLD = 0.5


@dataclass
class SpeakerMapping:
    """Result of speaker identification."""
    speaker_id: str
    name: str
    confidence: float = 0.0
    first_name: str | None = None
    last_name: str | None = None
    role: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "speaker_id": self.speaker_id,
            "name": self.name,
            "confidence": self.confidence,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "role": self.role,
        }

    def has_full_name(self) -> bool:
        """Check if both first and last name are provided and non-empty."""
        return bool(self.first_name and self.last_name)

    def get_display_name(self) -> str:
        """
        Get the display name following priority:
        1. First + Last name if both present
        2. Role if available
        3. UNKNOWN
        """
        if self.has_full_name():
            return f"{self.first_name} {self.last_name}"
        if self.role and self.role.upper() != "UNKNOWN":
            return self.role
        return "UNKNOWN"


@dataclass
class SpeakerIdentificationResult:
    """Full result of speaker identification for an episode."""
    episode_title: str
    speaker_map: Dict[str, str]  # speaker_id -> name
    raw_mappings: List[SpeakerMapping] = field(default_factory=list)

    def get_name(self, speaker_id: str) -> str:
        """Get speaker name, defaulting to original ID if unknown."""
        return self.speaker_map.get(speaker_id, speaker_id)


# ---------------------------------------------------------------------
# Transcript loading and formatting
# ---------------------------------------------------------------------
def load_transcript_segments(file_path: Path) -> List[Dict[str, Any]]:
    """
    Load segments from a transcript JSON file.

    Args:
        file_path: Path to the transcript JSON

    Returns:
        List of segment dictionaries with 'text', 'speaker', 'start', 'end'
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    segments = data.get("segments", [])
    if not segments:
        log.warning(f"No segments found in {file_path.name}")
        return []

    return segments


def select_representative_segments(
    segments: List[Dict[str, Any]],
    max_chars: int = DEFAULT_EXCERPT_CHARS,
) -> List[Dict[str, Any]]:
    """
    Select representative segments from different parts of the transcript.

    Strategy:
    - Include intro segments (first N)
    - Include some middle segments
    - Include some late segments
    - Balance to stay under max_chars

    Args:
        segments: All transcript segments
        max_chars: Maximum total characters for the excerpt

    Returns:
        Selected representative segments
    """
    if not segments:
        return []

    total_segments = len(segments)

    # Calculate indices for different sections
    intro_end = min(INTRO_SEGMENTS, total_segments)
    middle_start = total_segments // 3
    middle_end = min(middle_start + MIDDLE_SEGMENTS, total_segments)
    late_start = (2 * total_segments) // 3
    late_end = min(late_start + LATE_SEGMENTS, total_segments)

    # Collect segments from each section
    selected = []

    # Always include intro (most important for speaker identification)
    selected.extend(segments[:intro_end])

    # Add middle section if enough segments
    if middle_start > intro_end and middle_end <= total_segments:
        selected.extend(segments[middle_start:middle_end])

    # Add late section if enough segments
    if late_start > middle_end and late_end <= total_segments:
        selected.extend(segments[late_start:late_end])

    # Trim if over character limit
    total_chars = sum(len(s.get("text", "")) for s in selected)
    if total_chars > max_chars:
        # Keep intro priority, trim from end
        trimmed = []
        chars_so_far = 0
        for seg in selected:
            text_len = len(seg.get("text", ""))
            if chars_so_far + text_len > max_chars:
                break
            trimmed.append(seg)
            chars_so_far += text_len
        selected = trimmed

    log.info(
        "[SPEAKER_ID] Selected %d/%d segments (%d chars) for identification",
        len(selected), total_segments, sum(len(s.get("text", "")) for s in selected)
    )

    return selected


def merge_segments_by_speaker(
    segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge consecutive segments from the same speaker.

    This creates speaker "turns" which are more natural for the LLM to analyze.

    Args:
        segments: List of segment dictionaries

    Returns:
        List of merged segment dictionaries with combined text
    """
    if not segments:
        return []

    merged = []
    current_speaker = None
    current_texts = []
    current_start = None
    current_end = None

    for seg in segments:
        speaker = seg.get("speaker", "UNKNOWN")
        text = seg.get("text", "").strip()

        if not text:
            continue

        if speaker == current_speaker:
            # Same speaker, append text
            current_texts.append(text)
            current_end = seg.get("end", current_end)
        else:
            # New speaker, save current and start new
            if current_speaker is not None and current_texts:
                merged.append({
                    "speaker": current_speaker,
                    "text": " ".join(current_texts),
                    "start": current_start,
                    "end": current_end,
                })

            current_speaker = speaker
            current_texts = [text]
            current_start = seg.get("start")
            current_end = seg.get("end")

    # Don't forget the last speaker
    if current_speaker is not None and current_texts:
        merged.append({
            "speaker": current_speaker,
            "text": " ".join(current_texts),
            "start": current_start,
            "end": current_end,
        })

    log.info(
        "[SPEAKER_ID] Merged %d segments into %d speaker turns",
        len(segments), len(merged)
    )

    return merged


def format_transcript_for_identification(
    merged_segments: List[Dict[str, Any]],
) -> str:
    """
    Format merged segments into a readable transcript for the LLM.

    Args:
        merged_segments: List of merged segment dictionaries

    Returns:
        Formatted transcript string
    """
    lines = []
    for seg in merged_segments:
        speaker = seg.get("speaker", "UNKNOWN")
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{speaker}]\n{text}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------
def create_speaker_identification_tool() -> Dict[str, Any]:
    """
    Tool definition for identifying speakers in a podcast transcript.

    Maps speaker IDs (e.g., SPEAKER_00) to human-readable names.
    Requires both first_name and last_name; falls back to role or UNKNOWN.
    """
    return {
        "tools": [{
            "name": "identify_speakers",
            "description": (
                "Identify and label each speaker in the podcast transcript. "
                "You MUST provide both first_name AND last_name if you can identify them. "
                "If you cannot determine the full name, provide the role instead. "
                "Use 'UNKNOWN' for role only if you cannot identify anything about the speaker."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "speakers": {
                        "type": "array",
                        "description": "Mapping of each speaker ID to their identified name/role",
                        "items": {
                            "type": "object",
                            "properties": {
                                "speaker_id": {
                                    "type": "string",
                                    "description": "Original speaker ID (e.g., SPEAKER_00)"
                                },
                                "first_name": {
                                    "type": "string",
                                    "description": (
                                        "Speaker's first name if identifiable (e.g., 'Peter', 'Andrew'). "
                                        "Leave empty or null if unknown."
                                    )
                                },
                                "last_name": {
                                    "type": "string",
                                    "description": (
                                        "Speaker's last name if identifiable (e.g., 'Attia', 'Huberman'). "
                                        "Leave empty or null if unknown."
                                    )
                                },
                                "role": {
                                    "type": "string",
                                    "description": (
                                        "Speaker's role if name is not fully identifiable "
                                        "(e.g., 'Host', 'Guest Expert', 'Interviewer', 'Co-host'). "
                                        "Use 'UNKNOWN' only if nothing can be determined."
                                    )
                                },
                                "confidence": {
                                    "type": "number",
                                    "description": "Confidence score 0-1 (1 = certain, 0 = guessing)"
                                },
                                "reasoning": {
                                    "type": "string",
                                    "description": "Brief explanation of how you identified this speaker"
                                }
                            },
                            "required": ["speaker_id", "role", "confidence"]
                        }
                    }
                },
                "required": ["speakers"]
            }
        }],
        "tool_choice": {"type": "tool", "name": "identify_speakers"},
    }


def build_speaker_identification_payload(
    episode_title: str,
    transcript_text: str,
    podcast_name: str | None = None,
    max_tokens: int = 4000,
    temperature: float = 0.1,
) -> Dict[str, Any]:
    """
    Build the API payload for speaker identification.

    Args:
        episode_title: The episode title (often contains guest names)
        transcript_text: Formatted transcript excerpt
        podcast_name: The podcast name (helps identify hosts)
        max_tokens: Max tokens for response
        temperature: LLM temperature

    Returns:
        Complete API payload dict
    """
    tools_cfg = create_speaker_identification_tool()

    system_prompt = """You are an expert podcast analyst specializing in speaker identification.

Your task is to identify who is speaking in podcast transcripts based on:
1. The podcast name (identifies the show and often its regular host)
2. The episode title (often mentions guests)
3. Self-introductions in the transcript
4. Speaking patterns and content
5. References to other speakers by name
6. Host vs guest dynamics

CRITICAL NAMING RULES:
- You MUST provide BOTH first_name AND last_name if you can identify them
- Only provide a name if you are confident about BOTH first AND last name
- If you only know part of the name (e.g., only first name), leave both name fields empty and use role instead
- If you cannot determine the full name, provide a descriptive role (e.g., "Host", "Guest Expert", "Interviewer")
- Use "UNKNOWN" for role ONLY if you genuinely cannot identify anything about the speaker

Additional guidelines:
- Be conservative with confidence scores - only use >0.8 if you're very certain
- Multiple speaker IDs might refer to the same person due to diarization errors
- The podcast name can help you identify the host (e.g., "The Peter Attia Drive" -> host is Peter Attia)"""

    # Build context section
    context_parts = []
    if podcast_name:
        context_parts.append(f"PODCAST NAME: {podcast_name}")
    context_parts.append(f"EPISODE TITLE: {episode_title}")
    context_section = "\n".join(context_parts)

    user_prompt = f"""Analyze this podcast transcript excerpt and identify each speaker.

{context_section}

TRANSCRIPT EXCERPT (representative segments from intro, middle, and later parts):
{transcript_text}

For each SPEAKER_XX ID in the transcript, provide:
1. first_name AND last_name - ONLY if you can identify BOTH with confidence
2. role - ALWAYS provide this (Host, Guest, Interviewer, etc.) - use "UNKNOWN" only as last resort
3. confidence - your confidence level (0-1)

IMPORTANT: A speaker is only "named" if you provide BOTH first_name AND last_name.
If you only know "Dr. Smith" (no first name), leave names empty and use role "Guest Expert" or similar.

Call the identify_speakers tool with your analysis.
"""

    return {
        "anthropic_version": "bedrock-2023-05-31",
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": tools_cfg["tools"],
        "tool_choice": tools_cfg["tool_choice"],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


# ---------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------
def parse_speaker_identification_response(
    response: Dict[str, Any],
) -> List[SpeakerMapping]:
    """
    Parse the speaker identification tool response.

    Args:
        response: Raw API response

    Returns:
        List of SpeakerMapping objects
    """
    if "error" in response:
        msg = response.get("error", {}).get("message", "Unknown error")
        raise ValueError(f"API error: {msg}")

    content = response.get("content", [])
    if not content:
        raise ValueError("Empty response content")

    # Find tool_use block
    tool_blocks = [b for b in content if b.get("type") == "tool_use"]
    if not tool_blocks:
        text_blocks = [b for b in content if b.get("type") == "text"]
        if text_blocks:
            snippet = text_blocks[0].get("text", "")[:200]
            log.warning(f"[SPEAKER_ID] Model returned text instead of tool_use: {snippet}...")
        raise ValueError("No tool_use block found in response")

    tool_block = tool_blocks[0]
    if tool_block.get("name") != "identify_speakers":
        raise ValueError(f"Unexpected tool: {tool_block.get('name')}")

    tool_input = tool_block.get("input", {}) or {}
    speakers = tool_input.get("speakers", [])

    mappings = []
    for sp in speakers:
        first_name = sp.get("first_name") or None
        last_name = sp.get("last_name") or None
        role = sp.get("role") or None

        # Create mapping with new fields
        mapping = SpeakerMapping(
            speaker_id=sp.get("speaker_id", ""),
            name="",  # Will be set by get_display_name()
            confidence=sp.get("confidence", 0.0),
            first_name=first_name,
            last_name=last_name,
            role=role,
        )
        # Set the display name based on first+last name or role fallback
        mapping.name = mapping.get_display_name()
        mappings.append(mapping)

    log.info(f"[SPEAKER_ID] Parsed {len(mappings)} speaker mappings")
    return mappings


# ---------------------------------------------------------------------
# Main identification function
# ---------------------------------------------------------------------
def identify_speakers(
    episode_title: str,
    segments: List[Dict[str, Any]],
    *,
    podcast_name: str | None = None,
    client: BedrockClient | None = None,
    max_excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
) -> SpeakerIdentificationResult:
    """
    Identify speakers in a podcast transcript.

    Args:
        episode_title: The episode title
        segments: List of transcript segments
        podcast_name: The podcast name (helps identify hosts)
        client: Optional pre-configured BedrockClient
        max_excerpt_chars: Maximum characters for the excerpt

    Returns:
        SpeakerIdentificationResult with speaker mappings
    """
    client = client or BedrockClient(
        model_id=settings.aws.bedrock_model_id,
        region=settings.aws.region,
        profile=settings.aws.profile,
    )

    # Select representative segments
    selected = select_representative_segments(segments, max_excerpt_chars)

    # Merge by speaker for better readability
    merged = merge_segments_by_speaker(selected)

    # Format for LLM
    transcript_text = format_transcript_for_identification(merged)

    log.info(
        "[SPEAKER_ID] Identifying speakers for: %s (podcast: %s, %d chars)",
        episode_title[:50], podcast_name or "N/A", len(transcript_text)
    )

    # Build and invoke
    payload = build_speaker_identification_payload(
        episode_title=episode_title,
        transcript_text=transcript_text,
        podcast_name=podcast_name,
    )

    response = client.invoke(payload)
    mappings = parse_speaker_identification_response(response)

    # Build the final speaker map
    speaker_map: Dict[str, str] = {}
    for m in mappings:
        if m.confidence >= MIN_CONFIDENCE_THRESHOLD:
            speaker_map[m.speaker_id] = m.name
        else:
            # Low confidence - keep as UNKNOWN
            speaker_map[m.speaker_id] = "UNKNOWN"
            log.info(
                "[SPEAKER_ID] Low confidence (%.2f) for %s -> %s, using UNKNOWN",
                m.confidence, m.speaker_id, m.name
            )

    return SpeakerIdentificationResult(
        episode_title=episode_title,
        speaker_map=speaker_map,
        raw_mappings=mappings,
    )


def identify_speakers_from_file(
    transcript_path: Path,
    episode_title: str | None = None,
    podcast_name: str | None = None,
    *,
    client: BedrockClient | None = None,
) -> SpeakerIdentificationResult:
    """
    Identify speakers from a transcript JSON file.

    Args:
        transcript_path: Path to the transcript JSON file
        episode_title: Episode title (if None, uses filename)
        podcast_name: The podcast name (helps identify hosts)
        client: Optional pre-configured BedrockClient

    Returns:
        SpeakerIdentificationResult
    """
    transcript_path = Path(transcript_path)

    # Use filename as title if not provided
    if episode_title is None:
        episode_title = transcript_path.stem

    segments = load_transcript_segments(transcript_path)
    if not segments:
        log.warning(f"[SPEAKER_ID] No segments in {transcript_path.name}")
        return SpeakerIdentificationResult(
            episode_title=episode_title,
            speaker_map={},
            raw_mappings=[],
        )

    return identify_speakers(
        episode_title=episode_title,
        segments=segments,
        podcast_name=podcast_name,
        client=client,
    )


# ---------------------------------------------------------------------
# CLI / standalone usage
# ---------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python speaker_identification_tool.py <transcript.json> [episode_title] [podcast_name]")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    title = sys.argv[2] if len(sys.argv) > 2 else None
    podcast = sys.argv[3] if len(sys.argv) > 3 else None

    result = identify_speakers_from_file(file_path, title, podcast_name=podcast)

    print(f"\n{'='*60}")
    if podcast:
        print(f"Podcast: {podcast}")
    print(f"Episode: {result.episode_title}")
    print(f"{'='*60}")
    print("\nSpeaker Mappings:")
    for m in result.raw_mappings:
        name_info = f"{m.first_name} {m.last_name}" if m.has_full_name() else f"[role: {m.role}]"
        print(f"  {m.speaker_id} -> {m.name} ({name_info}, confidence: {m.confidence:.2f})")
    print(f"\nFinal Map: {json.dumps(result.speaker_map, indent=2)}")
