# src/innovation_intelligence/llm/tools/insights_tool.py
"""
Health insight extraction from transcripts using Claude tool-use.

This module handles:
- Chunking long transcripts to process full content
- Extracting health trends and stakes via LLM tool calls
- Deduplicating insights across chunks
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from innovation_intelligence.llm.bedrock_client import BedrockClient
from innovation_intelligence.logger import get_logger
from innovation_intelligence.config import settings

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
# Max characters per chunk (leaving room for prompt overhead)
# ~100K chars ≈ 25K tokens, safe for Claude's context window
DEFAULT_CHUNK_SIZE = 250_000
DEFAULT_CHUNK_OVERLAP = 2_000  # Overlap to avoid cutting insights


@dataclass
class RawInsight:
    name: str
    description: str
    type: str         # "trend" | "health_stake"
    evidence: str


# ----------------------------------------------------------------------
# Chunking utilities
# ----------------------------------------------------------------------
def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[str]:
    """
    Split text into overlapping chunks.

    Args:
        text: Full transcript text
        chunk_size: Maximum characters per chunk
        overlap: Characters to overlap between chunks

    Returns:
        List of text chunks
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # If not the last chunk, try to break at a sentence boundary
        if end < len(text):
            # Look for sentence end within last 1000 chars of chunk
            search_start = max(end - 1000, start)
            last_period = text.rfind(". ", search_start, end)
            last_newline = text.rfind("\n", search_start, end)

            # Use the latest sentence boundary found
            break_point = max(last_period, last_newline)
            if break_point > start:
                end = break_point + 1

        chunks.append(text[start:end])

        # Move start, accounting for overlap
        start = end - overlap if end < len(text) else end

    log.info(
        "[CHUNK] Split %d chars into %d chunks (size=%d, overlap=%d)",
        len(text), len(chunks), chunk_size, overlap
    )

    return chunks


def deduplicate_insights(
    insights: List[Dict[str, Any]],
    similarity_threshold: float = 0.90,
) -> List[Dict[str, Any]]:
    """
    Remove duplicate insights based on embedding similarity.

    Uses cosine similarity between insight embeddings (name + description).
    Batch embeds all insights at once for efficiency.

    Args:
        insights: List of insight dictionaries
        similarity_threshold: Minimum cosine similarity to consider duplicate (default 0.75)

    Returns:
        Deduplicated list of insights
    """
    if not insights:
        return []

    # Import embedder here to avoid circular imports
    from innovation_intelligence.analysis.insights.embedder import embed_texts_batch

    # Prepare texts for batch embedding
    valid_insights: List[Dict[str, Any]] = []
    texts_to_embed: List[str] = []

    for insight in insights:
        name = insight.get("name", "").strip()
        description = insight.get("description", "").strip()

        if not name:
            continue

        # Embed name + description for semantic similarity
        text_to_embed = f"{name}. {description}" if description else name
        texts_to_embed.append(text_to_embed)
        valid_insights.append(insight)

    if not texts_to_embed:
        return []

    # Batch embed all texts at once (much faster)
    log.info("[DEDUP] Batch embedding %d insights", len(texts_to_embed))
    embeddings_list = embed_texts_batch(texts_to_embed)

    # Convert to numpy arrays (already normalized by embedder)
    embeddings = [np.array(emb, dtype=np.float32) for emb in embeddings_list]

    # Deduplicate using cosine similarity
    unique: List[Dict[str, Any]] = []
    unique_embeddings: List[np.ndarray] = []

    for insight, embedding in zip(valid_insights, embeddings):
        is_duplicate = False

        for existing_emb in unique_embeddings:
            # Cosine similarity (vectors are normalized)
            similarity = float(np.dot(embedding, existing_emb))

            if similarity >= similarity_threshold:
                is_duplicate = True
                log.debug(
                    "[DEDUP] Skipping '%s' (similarity=%.3f)",
                    insight.get("name", "")[:40],
                    similarity
                )
                break

        if not is_duplicate:
            unique.append(insight)
            unique_embeddings.append(embedding)

    log.info("[DEDUP] Reduced %d insights to %d unique (threshold=%.2f)",
             len(valid_insights), len(unique), similarity_threshold)
    return unique


# ----------------------------------------------------------------------
# Tool definition & payload
# ----------------------------------------------------------------------
def create_extract_health_insights_tool() -> Dict[str, Any]:
    """
    Tool definition for extracting health trends and stakes from transcripts.
    """
    return {
        "tools": [
            {
                "name": "extract_health_insights",
                "description": (
                    "Extract ALL key health-related trends and health stakes "
                    "from a transcript. Each item includes name, description, type, evidence."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Transcript identifier (no extension).",
                        },
                        "insights": {
                            "type": "array",
                            "description": "List of extracted health trends and stakes.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Concise label (3-8 words) using transcript terminology.",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "20-30 word summary grounded in the transcript.",
                                    },
                                    "type": {
                                        "type": "string",
                                        "enum": ["trend", "health_stake"],
                                    },
                                    "evidence": {
                                        "type": "string",
                                        "description": "Short note on where/how this appears in the transcript.",
                                    },
                                },
                                "required": ["name", "description", "type", "evidence"],
                            },
                        },
                        "extraction_notes": {
                            "type": "string",
                            "description": "Short explanation of selection criteria or ambiguities.",
                        },
                    },
                    "required": ["filename", "insights"],
                },
            }
        ],
        "tool_choice": {"type": "tool", "name": "extract_health_insights"},
    }


def build_insights_payload(
    *,
    filename: str,
    transcript_text: str,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build the API payload for insight extraction.

    Args:
        filename: Transcript identifier
        transcript_text: Text content (already chunked if needed)
        chunk_index: Current chunk number (1-indexed, for logging)
        total_chunks: Total number of chunks

    Returns:
        Complete API payload dict
    """
    tools_cfg = create_extract_health_insights_tool()

    system_prompt = """You are a health policy analyst specializing in identifying significant trends and strategic health stakes from medical and public health documents.

Your extraction approach:
- Prioritize items with clear evidence of importance (repetition, explicit emphasis, clinical significance)
- Use precise, domain-specific terminology from the source material
- Distinguish between evolving patterns (trends) and critical risks/opportunities (health stakes)
- Ground every extraction in explicit textual evidence"""

    # Add chunk context if processing in parts
    chunk_context = ""
    if chunk_index is not None and total_chunks is not None and total_chunks > 1:
        chunk_context = f"\n\nNote: This is part {chunk_index} of {total_chunks} of the full transcript. Extract insights from this section only."

    user_prompt = f"""Analyze this health-related transcript and extract the most significant insights.

Definitions:
- Health Stake: critical issue, risk, or opportunity that affects health outcomes.
- Trend: consistent, observable pattern of change over time.

Task:
- Identify ALL key insights (trends and health stakes) in this text.
- Use precise terminology from the transcript.
- Base everything strictly on the transcript content.
- Translate insights in english is the source transcript is in another language
{chunk_context}

Filename: {filename}

Transcript:
{transcript_text}

Call the extract_health_insights tool with your analysis.
"""

    return {
        "anthropic_version": "bedrock-2023-05-31",
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": tools_cfg["tools"],
        "tool_choice": tools_cfg["tool_choice"],
        "max_tokens": 4096,
        "temperature": 0.2,
    }


# ----------------------------------------------------------------------
# Response parsing
# ----------------------------------------------------------------------
def parse_insights_tool_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract the tool input from Claude's tool_use response.
    Returns the dict with keys: filename, insights, extraction_notes?
    """
    if "error" in response:
        msg = response.get("error", {}).get("message", "Unknown error")
        raise ValueError(f"API error: {msg}")

    content = response.get("content", [])
    if not content:
        raise ValueError("Empty response content")

    tool_blocks = [b for b in content if b.get("type") == "tool_use"]
    if not tool_blocks:
        # log text content for debugging
        text_blocks = [b for b in content if b.get("type") == "text"]
        if text_blocks:
            snippet = text_blocks[0].get("text", "")[:200]
            log.warning(f"[INSIGHTS] Model returned text instead of tool_use: {snippet}...")
        raise ValueError("No tool_use block found in response")

    tool_block = tool_blocks[0]
    tool_input = tool_block.get("input", {}) or {}

    if tool_block.get("name") == "extract_health_insights":
        insights = tool_input.get("insights", [])
        log.info(f"[INSIGHTS] Tool returned {len(insights)} insights")

    return tool_input


# ----------------------------------------------------------------------
# Single chunk extraction
# ----------------------------------------------------------------------
def _extract_from_chunk(
    transcript_text: str,
    filename: str,
    client: BedrockClient,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Extract insights from a single chunk of text.

    Args:
        transcript_text: Chunk text
        filename: Transcript identifier
        client: Bedrock client
        chunk_index: Current chunk (1-indexed)
        total_chunks: Total chunks

    Returns:
        Parsed tool output dict
    """
    payload = build_insights_payload(
        filename=filename,
        transcript_text=transcript_text,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
    )
    response = client.invoke(payload)
    return parse_insights_tool_response(response)


# ----------------------------------------------------------------------
# High-level helper: text → raw insights dict (with chunking)
# ----------------------------------------------------------------------
def extract_health_insights_from_text(
    transcript_text: str,
    *,
    filename: str,
    client: BedrockClient | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> Dict[str, Any]:
    """
    Extract health insights from transcript text.

    For long transcripts, automatically chunks the text, extracts from
    each chunk, and deduplicates the results.

    Args:
        transcript_text: Full transcript text
        filename: Transcript identifier
        client: Optional pre-configured BedrockClient
        chunk_size: Max characters per chunk
        chunk_overlap: Overlap between chunks

    Returns:
        Dict with keys: filename, insights, extraction_notes
    """
    client = client or BedrockClient(
        model_id=settings.aws.bedrock_model_id,
        region=settings.aws.region,
        profile=settings.aws.profile,
    )

    # Chunk the text
    chunks = chunk_text(transcript_text, chunk_size, chunk_overlap)

    all_insights: List[Dict[str, Any]] = []
    all_notes: List[str] = []

    # Process each chunk
    for i, chunk in enumerate(chunks, 1):
        log.info(
            "[INSIGHTS] Processing chunk %d/%d (%d chars)",
            i, len(chunks), len(chunk)
        )

        try:
            result = _extract_from_chunk(
                transcript_text=chunk,
                filename=filename,
                client=client,
                chunk_index=i if len(chunks) > 1 else None,
                total_chunks=len(chunks) if len(chunks) > 1 else None,
            )

            chunk_insights = result.get("insights", [])
            all_insights.extend(chunk_insights)

            if result.get("extraction_notes"):
                all_notes.append(f"[Chunk {i}] {result['extraction_notes']}")

            log.info(
                "[INSIGHTS] Chunk %d/%d: extracted %d insights",
                i, len(chunks), len(chunk_insights)
            )

        except Exception as e:
            log.error("[INSIGHTS] Failed to process chunk %d: %s", i, e)
            all_notes.append(f"[Chunk {i}] Error: {str(e)}")

    # Deduplicate if we had multiple chunks
    if len(chunks) > 1:
        unique_insights = deduplicate_insights(all_insights)
    else:
        unique_insights = all_insights

    log.info(
        "[INSIGHTS] Total: %d insights from %d chunks (%d after dedup)",
        len(all_insights), len(chunks), len(unique_insights)
    )

    return {
        "filename": filename,
        "insights": unique_insights,
        "extraction_notes": " | ".join(all_notes) if all_notes else "",
    }
