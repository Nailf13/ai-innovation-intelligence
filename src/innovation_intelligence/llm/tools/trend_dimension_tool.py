# src/innovation_intelligence/llm/tools/trend_dimension_tool.py
"""
Trend dimension assessment tool using Gemini (GCP) tool-use pattern.

This tool is for TRENDS ONLY (not health stakes).
Trends are assessed on 3 dimensions:
  1) ADOPTION - Implementation stage (Nascent → Established)
  2) EXPECTATION - Sentiment/outlook level (Low → High)
  3) PROGRESS - Timeline horizon (Near-term → Long-term)

This module handles:
- Building prompts for trend dimension assessment
- Tool definition for structured LLM output (Gemini function calling)
- Response parsing with evidence extraction
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from google.genai import types

from innovation_intelligence.llm.gemini_client import GeminiClient
from innovation_intelligence.logger import get_logger
from innovation_intelligence.config import settings

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Trend Dimension Definitions (TRENDS ONLY)
# ---------------------------------------------------------------------
class TrendDimensionType(str, Enum):
    """Types of dimensions that can be assessed for a TREND."""
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
class TrendDimensionEvidence:
    """Evidence supporting a trend dimension assessment."""
    text: str
    source: str
    speaker: Optional[str] = None
    page: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    similarity_score: Optional[float] = None


@dataclass
class TrendDimensionResult:
    """Result of a trend dimension assessment."""
    dimension_type: TrendDimensionType
    value: str
    evidence: List[TrendDimensionEvidence]
    confidence: Optional[float] = None


# ---------------------------------------------------------------------
# Trend dimension hints for RAG query enhancement
# ---------------------------------------------------------------------
TREND_DIMENSION_HINTS: Dict[TrendDimensionType, str] = {
    TrendDimensionType.ADOPTION: "current practice, implementation, case examples, usage rate, market adoption",
    TrendDimensionType.EXPECTATION: "sentiment, attitudes, perception, expectations, interest, beliefs, consumer outlook",
    TrendDimensionType.PROGRESS: "timeline, roadmap, barriers, constraints, future outlook, development stage",
}


# ----------------------------------------------------------------------
# Tool definition (Gemini format)
# ----------------------------------------------------------------------
def _create_tool_function(
    name: str,
    description: str,
    schema: Dict[str, Any],
) -> types.Tool:
    """
    Helper to convert a JSON schema to a Gemini types.Tool object.
    """
    function_declaration = types.FunctionDeclaration(
        name=name,
        description=description,
        parameters=types.Schema(**schema),
    )
    return types.Tool(function_declarations=[function_declaration])


def create_trend_assessment_tool(dimension: TrendDimensionType) -> types.Tool:
    """
    Tool definition for assessing a single dimension of a TREND.
    (Gemini function calling schema)
    """
    if dimension == TrendDimensionType.ADOPTION:
        value_enum = [
            "Nascent experimentation",
            "Early adoption",
            "Crossing the chasm",
            "Established practice",
        ]
    elif dimension == TrendDimensionType.EXPECTATION:
        value_enum = ["Low", "Moderate", "High"]
    elif dimension == TrendDimensionType.PROGRESS:
        value_enum = [
            "Near-term (0-12 months)",
            "Mid-term (1-3 years)",
            "Long-term (3+ years)",
        ]
    else:
        raise ValueError(f"Unknown trend dimension type: {dimension}")

    schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "insight_name": {"type": "string"},
            "dimension": {
                "type": "string",
                "enum": ["adoption", "expectation", "progress"],
            },
            "value": {
                "type": "string",
                "enum": value_enum,   # <-- key change
                "description": "Chosen value for the requested dimension.",
            },
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "source": {"type": "string"},
                        "speaker": {"type": "string"},
                        "page": {"type": "integer"},
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                    },
                    "required": ["text", "source"],
                },
            },
            "confidence": {"type": "number"},
            "reasoning": {"type": "string"},
        },
        "required": ["insight_name", "dimension", "value", "evidence"],
    }

    return _create_tool_function(
        name="assess_trend_dimension",
        description="Assess the requested trend dimension and return value/evidence/confidence.",
        schema=schema,
    )


# ---------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------
def _get_trend_dimension_options(dimension: TrendDimensionType) -> str:
    """Get the valid options for a trend dimension type."""
    if dimension == TrendDimensionType.ADOPTION:
        return "Choose one: Nascent experimentation | Early adoption | Crossing the chasm | Established practice"
    if dimension == TrendDimensionType.EXPECTATION:
        return "Choose one: Low | Moderate | High"
    if dimension == TrendDimensionType.PROGRESS:
        return "Choose one: Near-term (0-12 months) | Mid-term (1-3 years) | Long-term (3+ years)"
    raise ValueError(f"Unknown trend dimension type: {dimension}")


def build_trend_dimension_payload(
    *,
    trend_name: str,
    trend_description: str,
    dimension: TrendDimensionType,
    context: str,
    sources: List[str],
    period: Optional[str] = None,
    max_context_chars: int = 200_000,
) -> Dict[str, Any]:
    """
    Build the Gemini payload for TREND dimension assessment.

    Returns:
        Dict with keys: contents, tool_defs, system_instruction
    """
    period = period or settings.aws.period_name

    assess_tool = create_trend_assessment_tool(dimension)
    dimension_options = _get_trend_dimension_options(dimension)

    system_instruction = """You are a healthcare innovation analyst specializing in assessing TRENDS across multiple dimensions.

Your assessment approach:
- Base your assessment STRICTLY on the provided RAG context
- Extract verbatim evidence quotes to support your assessment
- Consider frequency, emphasis, and recency of mentions
- Be conservative - if evidence is weak, reflect lower confidence
- Output MUST be a function call to assess_trend_dimension (no free-form answer)"""

    user_prompt = f"""Assess the {dimension.value.upper()} dimension for this health TREND.

TREND:
Name: {trend_name}
Description: {trend_description}

DIMENSION: {dimension.value.upper()}
{dimension_options}

TASK:
- Analyze the RAG context below to determine the appropriate {dimension.value} level for this TREND
- Provide 2-5 verbatim evidence quotes (max 800 chars each) from the context
- Include source metadata (speaker, page, timestamps) from the context headers when available
- Assign a confidence score (0.0-1.0) based on evidence strength

PERIOD: {period}
SOURCES: {", ".join(sources[:10])}

RAG CONTEXT:
{context[:max_context_chars]}

Call the assess_trend_dimension function with your analysis.
"""

    return {
        "contents": [user_prompt],
        "tool_defs": [assess_tool],
        "system_instruction": system_instruction,
    }


# ---------------------------------------------------------------------
# Response parsing (Gemini)
# ---------------------------------------------------------------------
def parse_trend_dimension_response(response: types.GenerateContentResponse) -> TrendDimensionResult:
    """
    Parse Gemini function call response into a TrendDimensionResult.

    Raises:
        ValueError: If response is malformed or no function call is present
    """
    if not getattr(response, "function_calls", None):
        # Model didn't call a function; keep a helpful log snippet if any text exists.
        if getattr(response, "text", None):
            snippet = (response.text or "")[:200]
            log.warning(f"[TREND] Model returned text instead of function call: {snippet}...")
        raise ValueError("No function call found in response")

    call = response.function_calls[0]
    if call.name != "assess_trend_dimension":
        raise ValueError(f"Unexpected function call: {call.name}")

    tool_input = dict(call.args)  # google.genai returns Mapping-like args

    dim_str = (tool_input.get("dimension") or "").lower().strip()
    try:
        dimension_type = TrendDimensionType(dim_str)
    except ValueError as e:
        raise ValueError(f"Invalid trend dimension type: {dim_str}") from e

    evidence_list: List[TrendDimensionEvidence] = []
    for ev in tool_input.get("evidence", []) or []:
        evidence_list.append(
            TrendDimensionEvidence(
                text=(ev.get("text") or ""),
                source=(ev.get("source") or "unknown"),
                speaker=ev.get("speaker"),
                page=ev.get("page"),
                start_time=ev.get("start"),
                end_time=ev.get("end"),
            )
        )

    return TrendDimensionResult(
        dimension_type=dimension_type,
        value=tool_input.get("value", "Unknown"),
        evidence=evidence_list,
        confidence=tool_input.get("confidence"),
    )


# ---------------------------------------------------------------------
# High-level assessment function (Gemini)
# ---------------------------------------------------------------------
def assess_trend_dimension(
    *,
    trend_name: str,
    trend_description: str,
    dimension: TrendDimensionType,
    context: str,
    sources: List[str],
    client: Optional[GeminiClient] = None,
    period: Optional[str] = None,
) -> TrendDimensionResult:
    """
    Assess a single dimension for a TREND using RAG context (Gemini on GCP).

    Returns:
        TrendDimensionResult with value and evidence
    """
    if not context.strip():
        log.warning(f"[TREND] No context for {trend_name}/{dimension.value}, returning unknown")
        return TrendDimensionResult(
            dimension_type=dimension,
            value="Unknown",
            evidence=[],
            confidence=0.0,
        )

    client = client or GeminiClient(
        project=settings.gcp.project_id,
        location=settings.gcp.location,
        model_id=settings.gcp.gemini_model_id,
    )

    payload = build_trend_dimension_payload(
        trend_name=trend_name,
        trend_description=trend_description,
        dimension=dimension,
        context=context,
        sources=sources,
        period=period,
    )

    log.info(f"[TREND] Assessing {dimension.value} for: {trend_name[:50]}...")

    response = client.invoke(
        contents=payload["contents"],
        tool_defs=payload["tool_defs"],
        system_instruction=payload["system_instruction"],
        allowed_function_names=["assess_trend_dimension"],
    )

    result = parse_trend_dimension_response(response)

    log.info(
        f"[TREND] {dimension.value}={result.value} "
        f"(confidence={result.confidence or 'N/A'}, evidence={len(result.evidence)})"
    )

    return result
