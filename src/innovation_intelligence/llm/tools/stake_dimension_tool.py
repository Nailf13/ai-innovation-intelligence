# src/innovation_intelligence/llm/tools/health_stake_tool.py
"""
Health stake (enjeu) assessment tool using Gemini (GCP) tool-use pattern.

This tool is intentionally DIFFERENT from trend dimensions:
- Stakes are structural / long-lived problems, not "adopted".
- We assess 3 pertinent dimensions only:
  1) CRITICALITY (how strategically/clinically important)
  2) URGENCY (how time-pressing / accelerating)
  3) ACTIONABILITY (how feasible to act with credible food/dairy innovation)

This module handles:
- Prompt building for stake assessment
- Tool definition for structured output (Gemini function calling)
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
# Stake Dimension Definitions (HEALTH STAKES ONLY)
# ---------------------------------------------------------------------
class StakeDimensionType(str, Enum):
    """Three key dimensions for a health stake (enjeu)."""
    CRITICALITY = "criticality"
    URGENCY = "urgency"
    ACTIONABILITY = "actionability"


class CriticalityLevel(str, Enum):
    """How critical the stake is (burden + strategic importance)."""
    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"


class UrgencyLevel(str, Enum):
    """How time-sensitive the stake is."""
    LONG_TERM = "Long-term"
    MID_TERM = "Mid-term"
    IMMEDIATE = "Immediate"


class ActionabilityLevel(str, Enum):
    """How feasible it is to act credibly (food-first, not hype-driven)."""
    HARD = "Hard to address"
    MODERATE = "Moderately addressable"
    HIGH = "Highly addressable"


@dataclass
class StakeEvidence:
    """Evidence supporting a stake assessment."""
    text: str
    source: str
    speaker: Optional[str] = None
    page: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    similarity_score: Optional[float] = None


@dataclass
class StakeAssessmentResult:
    """Result of a stake assessment."""
    dimension_type: StakeDimensionType
    value: str
    evidence: List[StakeEvidence]
    confidence: Optional[float] = None


# ---------------------------------------------------------------------
# Stake dimension hints for RAG query enhancement
# ---------------------------------------------------------------------
STAKE_DIMENSION_HINTS: Dict[StakeDimensionType, str] = {
    StakeDimensionType.CRITICALITY: "burden, prevalence, severity, costs, public health priority, chronic disease, morbidity",
    StakeDimensionType.URGENCY: "accelerating, rising fast, now, immediate pressure, near-term risk, policy push, post-pandemic",
    StakeDimensionType.ACTIONABILITY: "food-first, feasible levers, credible claims, evidence, formulation, consumer clarity, differentiation",
}


# ----------------------------------------------------------------------
# Tool definition (Gemini format)
# ----------------------------------------------------------------------
def _create_tool_function(name: str, description: str, schema: Dict[str, Any]) -> types.Tool:
    """Helper to convert a JSON schema to a Gemini types.Tool object."""
    function_declaration = types.FunctionDeclaration(
        name=name,
        description=description,
        parameters=types.Schema(**schema),
    )
    return types.Tool(function_declarations=[function_declaration])


def create_stake_assessment_tool(dimension: StakeDimensionType) -> types.Tool:
    """Tool definition for assessing a single dimension of a HEALTH STAKE."""
    if dimension == StakeDimensionType.CRITICALITY:
        value_enum = [s.value for s in CriticalityLevel]
    elif dimension == StakeDimensionType.URGENCY:
        value_enum = [s.value for s in UrgencyLevel]
    elif dimension == StakeDimensionType.ACTIONABILITY:
        value_enum = [s.value for s in ActionabilityLevel]
    else:
        raise ValueError(f"Unknown stake dimension type: {dimension}")

    schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "stake_name": {"type": "string"},
            "dimension": {
                "type": "string",
                "enum": [d.value for d in StakeDimensionType],
            },
            "value": {
                "type": "string",
                "enum": value_enum,
                "description": "Chosen value for the requested stake dimension.",
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
        "required": ["stake_name", "dimension", "value", "evidence"],
    }

    return _create_tool_function(
        name="assess_health_stake",
        description="Assess the requested HEALTH STAKE dimension and return value/evidence/confidence.",
        schema=schema,
    )


# ---------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------
def _get_stake_dimension_options(dimension: StakeDimensionType) -> str:
    """Get the valid options for a stake dimension type."""
    if dimension == StakeDimensionType.CRITICALITY:
        return "Choose one: Low | Moderate | High"
    if dimension == StakeDimensionType.URGENCY:
        return "Choose one: Long-term | Mid-term | Immediate"
    if dimension == StakeDimensionType.ACTIONABILITY:
        return "Choose one: Hard to address | Moderately addressable | Highly addressable"
    raise ValueError(f"Unknown stake dimension type: {dimension}")


def build_stake_assessment_payload(
    *,
    stake_name: str,
    stake_description: str,
    dimension: StakeDimensionType,
    context: str,
    sources: List[str],
    period: Optional[str] = None,
    max_context_chars: int = 200_000,
) -> Dict[str, Any]:
    """
    Build the Gemini payload for HEALTH STAKE assessment.

    Returns:
        Dict with keys: contents, tool_defs, system_instruction
    """
    period = period or settings.aws.period_name

    assess_tool = create_stake_assessment_tool(dimension)
    dimension_options = _get_stake_dimension_options(dimension)

    system_instruction = """You are a healthcare innovation analyst.
You are assessing HEALTH STAKES (enjeux) ONLY (NOT trends).

Rules:
- Do NOT talk about adoption curves for stakes
- Base your assessment STRICTLY on the provided RAG context
- Extract verbatim evidence quotes to support your assessment
- Be conservative: if evidence is weak, reflect lower confidence
- Output MUST be a function call to assess_health_stake (no free-form answer)"""

    user_prompt = f"""Assess the {dimension.value.upper()} dimension for this HEALTH STAKE (enjeu).

HEALTH STAKE:
Name: {stake_name}
Description: {stake_description}

DIMENSION: {dimension.value.upper()}
{dimension_options}

TASK:
- Analyze the RAG context below to determine the appropriate {dimension.value} level for the HEALTH STAKE
- Provide 2-5 verbatim evidence quotes (max 800 chars each) from the context
- Include source metadata (speaker, page, timestamps) from the context headers when available
- Assign a confidence score (0.0-1.0) based on evidence strength

PERIOD: {period}
SOURCES: {", ".join(sources[:10])}

RAG CONTEXT:
{context[:max_context_chars]}

Call the assess_health_stake function with your analysis.
"""

    return {
        "contents": [user_prompt],
        "tool_defs": [assess_tool],
        "system_instruction": system_instruction,
    }


# ---------------------------------------------------------------------
# Response parsing (Gemini)
# ---------------------------------------------------------------------
def parse_stake_assessment_response(response: types.GenerateContentResponse) -> StakeAssessmentResult:
    """Parse Gemini function call response into a StakeAssessmentResult."""
    if not getattr(response, "function_calls", None):
        if getattr(response, "text", None):
            snippet = (response.text or "")[:200]
            log.warning(f"[STAKE] Model returned text instead of function call: {snippet}...")
        raise ValueError("No function call found in response")

    call = response.function_calls[0]
    if call.name != "assess_health_stake":
        raise ValueError(f"Unexpected function call: {call.name}")

    tool_input = dict(call.args)

    dim_str = (tool_input.get("dimension") or "").lower().strip()
    try:
        dimension_type = StakeDimensionType(dim_str)
    except ValueError as e:
        raise ValueError(f"Invalid stake dimension type: {dim_str}") from e

    evidence_list: List[StakeEvidence] = []
    for ev in tool_input.get("evidence", []) or []:
        evidence_list.append(
            StakeEvidence(
                text=(ev.get("text") or ""),
                source=(ev.get("source") or "unknown"),
                speaker=ev.get("speaker"),
                page=ev.get("page"),
                start_time=ev.get("start"),
                end_time=ev.get("end"),
            )
        )

    return StakeAssessmentResult(
        dimension_type=dimension_type,
        value=tool_input.get("value", "Unknown"),
        evidence=evidence_list,
        confidence=tool_input.get("confidence"),
    )


# ---------------------------------------------------------------------
# High-level assessment function (Gemini)
# ---------------------------------------------------------------------
def assess_health_stake(
    *,
    stake_name: str,
    stake_description: str,
    dimension: StakeDimensionType,
    context: str,
    sources: List[str],
    client: Optional[GeminiClient] = None,
    period: Optional[str] = None,
) -> StakeAssessmentResult:
    """
    Assess a single dimension for a HEALTH STAKE using RAG context (Gemini on GCP).

    Returns:
        StakeAssessmentResult with value and evidence
    """
    if not context.strip():
        log.warning(f"[STAKE] No context for {stake_name}/{dimension.value}, returning unknown")
        return StakeAssessmentResult(
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

    payload = build_stake_assessment_payload(
        stake_name=stake_name,
        stake_description=stake_description,
        dimension=dimension,
        context=context,
        sources=sources,
        period=period,
    )

    log.info(f"[STAKE] Assessing {dimension.value} for stake: {stake_name[:60]}...")

    response = client.invoke(
        contents=payload["contents"],
        tool_defs=payload["tool_defs"],
        system_instruction=payload["system_instruction"],
        allowed_function_names=["assess_health_stake"],
    )

    result = parse_stake_assessment_response(response)

    log.info(
        f"[STAKE] {dimension.value}={result.value} "
        f"(confidence={result.confidence or 'N/A'}, evidence={len(result.evidence)})"
    )

    return result
