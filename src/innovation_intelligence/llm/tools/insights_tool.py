"""
Health insight extraction from transcripts using Gemini tool-use.

This module handles:
- Chunking long transcripts to process full content
- Extracting health trends and stakes via LLM tool calls
- Deduplicating insights across chunks
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np

from google.genai import types

from innovation_intelligence.llm.gemini_client import GeminiClient
from innovation_intelligence.logger import get_logger
from innovation_intelligence.config import settings

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
DEFAULT_CHUNK_SIZE = 250_000
DEFAULT_CHUNK_OVERLAP = 2_000  # Overlap to avoid cutting insights

# Insight extraction limits per chunk
MIN_INSIGHTS_PER_CHUNK = 1
MAX_INSIGHTS_PER_CHUNK = 10
TARGET_INSIGHTS_PER_CHUNK = "1-10"


# ---------------------------------------------------------------------
# Chunking utilities
# ---------------------------------------------------------------------
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
        similarity_threshold: Minimum cosine similarity to consider duplicate (default 0.90)

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
# Tool definition (Gemini format)
# ----------------------------------------------------------------------
def _create_tool_function(
    name: str,
    description: str,
    schema: Dict[str, Any]
) -> types.Tool:
    """
    Helper to convert a JSON schema to a Gemini types.Tool object.
    """
    function_declaration = types.FunctionDeclaration(
        name=name,
        description=description,
        parameters=types.Schema(**schema),
    )

    return types.Tool(
        function_declarations=[function_declaration]
    )


def create_extract_health_insights_tool() -> types.Tool:
    """
    Tool definition for extracting health trends and stakes from transcripts.
    Includes schema constraints for insight count (minItems/maxItems).
    """
    schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Transcript identifier (no extension).",
            },
            "insights": {
                "type": "array",
                "description": (
                    f"List of {TARGET_INSIGHTS_PER_CHUNK} most significant health "
                    "trends and stakes, ranked by importance. Focus on quality over "
                    "quantity - include only insights with clear textual evidence."
                ),
                "minItems": MIN_INSIGHTS_PER_CHUNK,
                "maxItems": MAX_INSIGHTS_PER_CHUNK,
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
                            "description": (
                                "'trend' = consistent pattern of change over time; "
                                "'health_stake' = critical risk, issue, or opportunity."
                            ),
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
                "description": (
                    "Brief explanation of selection criteria, any ambiguities, "
                    "or notable omissions (insights that were borderline)."
                ),
            },
        },
        "required": ["filename", "insights"],
    }

    return _create_tool_function(
        name="extract_health_insights",
        description=(
            f"Extract the {TARGET_INSIGHTS_PER_CHUNK} most significant health-related "
            "trends and health stakes from a transcript. Prioritize by: frequency of "
            "mention, explicit emphasis, and clinical/strategic importance. "
            "Each item includes name, description, type, and evidence."
        ),
        schema=schema,
    )


# ----------------------------------------------------------------------
# Payload building
# ----------------------------------------------------------------------
def build_insights_payload(
    *,
    filename: str,
    transcript_text: str,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build the arguments for Gemini insight extraction.

    Args:
        filename: Transcript identifier
        transcript_text: Text content (already chunked if needed)
        chunk_index: Current chunk number (1-indexed, for logging)
        total_chunks: Total number of chunks

    Returns:
        Dict with keys: contents, tool_defs, system_instruction
    """
    extract_tool = create_extract_health_insights_tool()

    system_instruction = f"""You are a health policy analyst specializing in identifying significant trends and strategic health stakes from medical and public health documents.

Your extraction approach:
- Extract the {TARGET_INSIGHTS_PER_CHUNK} most significant insights from the text
- Prioritize items by: (1) frequency of mention, (2) explicit emphasis, (3) clinical/strategic significance
- Use precise, domain-specific terminology from the source material
- Distinguish between evolving patterns (trends) and critical risks/opportunities (health stakes)
- Ground every extraction in explicit textual evidence
- Maintain a balanced distribution between trends and health stakes in the final output

Quality guidelines:
- Prefer fewer, high-quality insights over many weak ones
- Each insight must have clear textual support
- Avoid overly generic or vague insights
- Skip insights that lack concrete evidence in the transcript"""

    # Add chunk context if processing in parts
    chunk_context = ""
    if chunk_index is not None and total_chunks is not None and total_chunks > 1:
        chunk_context = f"""

Note: This is part {chunk_index} of {total_chunks} of the full transcript.
- Extract insights from THIS SECTION only
- Maintain the same quality standards regardless of chunk position
- Later chunks may contain follow-up discussion of earlier topics"""

    user_prompt = f"""Analyze this health-related transcript and extract the most significant insights.

Definitions:
- Health Stake: Critical issue, risk, or opportunity that affects health outcomes (e.g., emerging disease threats, healthcare access gaps, regulatory changes).
- Trend: Consistent, observable pattern of change over time (e.g., rising obesity rates, shift toward telemedicine, declining vaccination uptake).

Task:
1. Identify the {TARGET_INSIGHTS_PER_CHUNK} most significant insights (trends and health stakes) in this text
2. Rank by importance: frequency of mention, explicit emphasis, clinical/strategic significance
3. Use precise terminology from the transcript
4. Translate insights to English if the source transcript is in another language
{chunk_context}

Filename: {filename}

Transcript:
{transcript_text}

Call the extract_health_insights function with your analysis. Focus on quality over quantity."""

    contents = [user_prompt]

    return {
        "contents": contents,
        "tool_defs": [extract_tool],
        "system_instruction": system_instruction,
    }


# ----------------------------------------------------------------------
# Response parsing
# ----------------------------------------------------------------------
def parse_insights_tool_response(response: types.GenerateContentResponse) -> Dict[str, Any]:
    """
    Extract the tool input from Gemini's function call response.
    Returns the dict with keys: filename, insights, extraction_notes?
    """
    if not response.function_calls:
        # Check for text response (model didn't use tool)
        if response.text:
            snippet = response.text[:200]
            log.warning(f"[INSIGHTS] Model returned text instead of function call: {snippet}...")
        raise ValueError("No function call found in response")

    call = response.function_calls[0]

    if call.name != "extract_health_insights":
        raise ValueError(f"Unexpected function call: {call.name}")

    # Convert to dict
    tool_input = dict(call.args)

    insights = tool_input.get("insights", [])
    log.info("[INSIGHTS] Tool returned %d insights", len(insights))

    return tool_input


# ----------------------------------------------------------------------
# Single chunk extraction
# ----------------------------------------------------------------------
def _extract_from_chunk(
    transcript_text: str,
    filename: str,
    client: GeminiClient,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
    retry_attempts: int = 3,
    backoff: float = 2.0,
) -> Dict[str, Any]:
    """
    Extract insights from a single chunk of text with retry logic.

    Args:
        transcript_text: Chunk text
        filename: Transcript identifier
        client: Gemini client
        chunk_index: Current chunk (1-indexed)
        total_chunks: Total chunks
        retry_attempts: Number of retry attempts for parsing failures
        backoff: Base wait time between retries (in seconds)

    Returns:
        Parsed tool output dict

    Raises:
        ValueError: After all retry attempts fail
    """
    payload = build_insights_payload(
        filename=filename,
        transcript_text=transcript_text,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
    )

    last_error = None

    for attempt in range(1, retry_attempts + 1):
        try:
            response = client.invoke(
                contents=payload["contents"],
                tool_defs=payload["tool_defs"],
                system_instruction=payload["system_instruction"],
                allowed_function_names=["extract_health_insights"],
            )

            return parse_insights_tool_response(response)

        except ValueError as e:
            last_error = e
            error_msg = str(e)

            if attempt == retry_attempts:
                log.error(
                    "[INSIGHTS] All %d attempts failed for chunk %s: %s",
                    retry_attempts,
                    chunk_index or "single",
                    error_msg
                )
                raise

            wait_time = backoff * attempt
            log.warning(
                "[INSIGHTS] Attempt %d/%d failed: %s. Retrying in %.1fs...",
                attempt,
                retry_attempts,
                error_msg,
                wait_time
            )
            time.sleep(wait_time)

    # Should never reach here, but just in case
    raise last_error or ValueError("Extraction failed unexpectedly")


# ----------------------------------------------------------------------
# High-level helper: text → raw insights dict (with chunking)
# ----------------------------------------------------------------------
def extract_health_insights_from_text(
    transcript_text: str,
    *,
    filename: str,
    client: GeminiClient | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    retry_attempts: int = 3,
    backoff: float = 2.0,
) -> Dict[str, Any]:
    """
    Extract health insights from transcript text.

    For long transcripts, automatically chunks the text, extracts from
    each chunk, and deduplicates the results.

    Args:
        transcript_text: Full transcript text
        filename: Transcript identifier
        client: Optional pre-configured GeminiClient
        chunk_size: Max characters per chunk
        chunk_overlap: Overlap between chunks
        retry_attempts: Number of retry attempts for each chunk extraction
        backoff: Base wait time between retries (in seconds)

    Returns:
        Dict with keys: filename, insights, extraction_notes
    """
    client = client or GeminiClient(
        project=settings.gcp.project_id,
        location=settings.gcp.location,
        model_id=settings.gcp.gemini_model_id,
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
                retry_attempts=retry_attempts,
                backoff=backoff,
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
            log.error("[INSIGHTS] Failed to process chunk %d after %d retries: %s", i, retry_attempts, e)
            all_notes.append(f"[Chunk {i}] Error after {retry_attempts} retries: {str(e)}")

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