# src/innovation_intelligence/llm/tools/dimension_tool.py
"""
Dimension assessment tool for insights using Claude tool-use pattern.

This module handles:
- Building prompts for dimension assessment (adoption, expectation, progress)
- Tool definition for structured LLM output
- Response parsing with evidence extraction
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from innovation_intelligence.llm.bedrock_client import BedrockClient
from innovation_intelligence.logger import get_logger
from innovation_intelligence.config import settings

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Dimension Definitions
# ---------------------------------------------------------------------
class DimensionType(str, Enum):
    """Types of dimensions that can be assessed for an insight."""
    ADOPTION = "adoption"
    EXPECTATION = "expectation"
    PROGRESS = "progress"


class AdoptionStage(str, Enum):
    """Adoption stages for a trend/insight."""
    NASCENT = "Nascent experimentation"
    EARLY = "Early adoption"
    CROSSING = "Crossing the chasm"
    ESTABLISHED = "Established practice"


class ExpectationLevel(str, Enum):
    """Expectation/sentiment levels."""
    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"


class ProgressHorizon(str, Enum):
    """Timeline horizons for progress."""
    NEAR_TERM = "Near-term (0-12 months)"
    MID_TERM = "Mid-term (1-3 years)"
    LONG_TERM = "Long-term (3+ years)"


@dataclass
class DimensionEvidence:
    """Evidence supporting a dimension assessment."""
    text: str
    source: str
    speaker: Optional[str] = None
    page: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    similarity_score: Optional[float] = None


@dataclass
class DimensionResult:
    """Result of a dimension assessment."""
    dimension_type: DimensionType
    value: str
    evidence: List[DimensionEvidence]
    confidence: Optional[float] = None


# ---------------------------------------------------------------------
# Dimension hints for RAG query enhancement
# ---------------------------------------------------------------------
DIMENSION_HINTS: Dict[DimensionType, str] = {
    DimensionType.ADOPTION: "current practice, implementation, case examples, usage rate, market adoption",
    DimensionType.EXPECTATION: "sentiment, attitudes, perception, expectations, interest, beliefs, consumer outlook",
    DimensionType.PROGRESS: "timeline, roadmap, barriers, constraints, future outlook, development stage",
}


# ---------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------
def create_dimension_assessment_tool() -> Dict[str, Any]:
    """
    Tool definition for assessing a single dimension of an insight.
    """
    return {
        "tools": [
            {
                "name": "assess_dimension",
                "description": (
                    "Assess a specific dimension (adoption, expectation, or progress) "
                    "for a health trend or insight based on provided context."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "insight_name": {
                            "type": "string",
                            "description": "Name of the insight being assessed.",
                        },
                        "dimension": {
                            "type": "string",
                            "enum": ["adoption", "expectation", "progress"],
                            "description": "The dimension being assessed.",
                        },
                        "value": {
                            "type": "string",
                            "description": "The assessed value for this dimension.",
                        },
                        "evidence": {
                            "type": "array",
                            "description": "Evidence quotes supporting the assessment.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {
                                        "type": "string",
                                        "description": "Verbatim quote from context (max 800 chars).",
                                    },
                                    "source": {
                                        "type": "string",
                                        "description": "Source identifier (filename, document).",
                                    },
                                    "speaker": {
                                        "type": "string",
                                        "description": "Speaker name if from podcast.",
                                    },
                                    "page": {
                                        "type": "integer",
                                        "description": "Page number if from document.",
                                    },
                                    "start": {
                                        "type": "number",
                                        "description": "Start time in seconds if from podcast.",
                                    },
                                    "end": {
                                        "type": "number",
                                        "description": "End time in seconds if from podcast.",
                                    },
                                },
                                "required": ["text", "source"],
                            },
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence score 0.0-1.0 based on evidence strength.",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Brief explanation of the assessment rationale.",
                        },
                    },
                    "required": ["insight_name", "dimension", "value", "evidence"],
                },
            }
        ],
        "tool_choice": {"type": "tool", "name": "assess_dimension"},
    }


# ---------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------
def _get_dimension_options(dimension: DimensionType) -> str:
    """Get the valid options for a dimension type."""
    if dimension == DimensionType.ADOPTION:
        return "Choose one: Nascent experimentation | Early adoption | Crossing the chasm | Established practice"
    elif dimension == DimensionType.EXPECTATION:
        return "Choose one: Low | Moderate | High"
    elif dimension == DimensionType.PROGRESS:
        return "Choose one: Near-term (0-12 months) | Mid-term (1-3 years) | Long-term (3+ years)"
    else:
        raise ValueError(f"Unknown dimension type: {dimension}")


def build_dimension_payload(
    *,
    insight_name: str,
    insight_description: str,
    dimension: DimensionType,
    context: str,
    sources: List[str],
    period: Optional[str] = None,
    max_context_chars: int = 200_000,
) -> Dict[str, Any]:
    """
    Build the API payload for dimension assessment.

    Args:
        insight_name: Name of the insight to assess
        insight_description: Description of the insight
        dimension: Which dimension to assess
        context: Retrieved RAG context
        sources: List of source identifiers
        period: Analysis period (e.g., "2025")
        max_context_chars: Maximum context length

    Returns:
        Complete API payload dict
    """
    tools_cfg = create_dimension_assessment_tool()
    period = period or settings.aws.period_name

    system_prompt = """You are a healthcare innovation analyst specializing in assessing trends and insights across multiple dimensions.

Your assessment approach:
- Base your assessment STRICTLY on the provided RAG context
- Extract verbatim evidence quotes to support your assessment
- Consider frequency, emphasis, and recency of mentions
- Be conservative - if evidence is weak, acknowledge lower confidence"""

    dimension_options = _get_dimension_options(dimension)

    user_prompt = f"""Assess the {dimension.value.upper()} dimension for this health insight.

INSIGHT:
Name: {insight_name}
Description: {insight_description}

DIMENSION: {dimension.value.upper()}
{dimension_options}

TASK:
- Analyze the RAG context below to determine the appropriate {dimension.value} level
- Provide 2-5 verbatim evidence quotes (max 800 chars each) from the context
- Include source metadata (speaker, page, timestamps) from the context headers
- Assign a confidence score (0.0-1.0) based on evidence strength

PERIOD: {period}
SOURCES: {", ".join(sources[:10])}

RAG CONTEXT:
{context[:max_context_chars]}

Call the assess_dimension tool with your analysis.
"""

    return {
        "anthropic_version": "bedrock-2023-05-31",
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": tools_cfg["tools"],
        "tool_choice": tools_cfg["tool_choice"],
        "max_tokens": 2048,
        "temperature": 0.0,
    }


# ---------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------
def parse_dimension_response(response: Dict[str, Any]) -> DimensionResult:
    """
    Parse the LLM response into a DimensionResult.

    Args:
        response: Raw API response

    Returns:
        Parsed DimensionResult

    Raises:
        ValueError: If response is malformed
    """
    if "error" in response:
        msg = response.get("error", {}).get("message", "Unknown error")
        raise ValueError(f"API error: {msg}")

    content = response.get("content", [])
    if not content:
        raise ValueError("Empty response content")

    tool_blocks = [b for b in content if b.get("type") == "tool_use"]
    if not tool_blocks:
        text_blocks = [b for b in content if b.get("type") == "text"]
        if text_blocks:
            snippet = text_blocks[0].get("text", "")[:200]
            log.warning(f"[DIMENSION] Model returned text instead of tool_use: {snippet}...")
        raise ValueError("No tool_use block found in response")

    tool_block = tool_blocks[0]
    tool_input = tool_block.get("input", {}) or {}

    # Parse dimension type
    dim_str = tool_input.get("dimension", "").lower()
    try:
        dimension_type = DimensionType(dim_str)
    except ValueError:
        raise ValueError(f"Invalid dimension type: {dim_str}")

    # Parse evidence
    evidence_list: List[DimensionEvidence] = []
    for ev in tool_input.get("evidence", []):
        evidence_list.append(
            DimensionEvidence(
                text=ev.get("text", ""),
                source=ev.get("source", "unknown"),
                speaker=ev.get("speaker"),
                page=ev.get("page"),
                start_time=ev.get("start"),
                end_time=ev.get("end"),
            )
        )

    return DimensionResult(
        dimension_type=dimension_type,
        value=tool_input.get("value", "Unknown"),
        evidence=evidence_list,
        confidence=tool_input.get("confidence"),
    )


# ---------------------------------------------------------------------
# High-level assessment function
# ---------------------------------------------------------------------
def assess_dimension(
    *,
    insight_name: str,
    insight_description: str,
    dimension: DimensionType,
    context: str,
    sources: List[str],
    client: Optional[BedrockClient] = None,
    period: Optional[str] = None,
) -> DimensionResult:
    """
    Assess a single dimension for an insight using RAG context.

    Args:
        insight_name: Name of the insight
        insight_description: Description of the insight
        dimension: Which dimension to assess
        context: RAG-retrieved context
        sources: List of source identifiers
        client: Optional BedrockClient (creates default if None)
        period: Analysis period label

    Returns:
        DimensionResult with value and evidence
    """
    if not context.strip():
        log.warning(f"[DIMENSION] No context for {insight_name}/{dimension.value}, returning unknown")
        return DimensionResult(
            dimension_type=dimension,
            value="Unknown",
            evidence=[],
            confidence=0.0,
        )

    client = client or BedrockClient(
        model_id=settings.aws.bedrock_model_id,
        region=settings.aws.region,
        profile=settings.aws.profile,
    )

    payload = build_dimension_payload(
        insight_name=insight_name,
        insight_description=insight_description,
        dimension=dimension,
        context=context,
        sources=sources,
        period=period,
    )

    log.info(f"[DIMENSION] Assessing {dimension.value} for: {insight_name[:50]}...")

    response = client.invoke(payload)
    result = parse_dimension_response(response)

    log.info(
        f"[DIMENSION] {dimension.value}={result.value} "
        f"(confidence={result.confidence or 'N/A'}, evidence={len(result.evidence)})"
    )

    return result
