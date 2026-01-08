"""
Macro insight discovery via semantic clustering.

This module handles:
- Clustering UnitInsights by embedding similarity
- LLM-based naming for macro insights
- Persisting MacroInsight entities to database
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from sqlalchemy.orm import Session

from innovation_intelligence.db.models import MacroInsight, UnitInsight
from innovation_intelligence.llm.bedrock_client import BedrockClient
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.75

# Data Structures (for internal use)
@dataclass
class MacroInsightCandidate:
    """Intermediate representation before DB persistence."""

    label: str
    description: Optional[str]
    unit_insight_ids: List[int]
    centroid: List[float]


# Similarity Helpers
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors (assumes normalized)."""
    return float(np.dot(a, b))


# LLM Naming
def _parse_llm_label_response(response: dict) -> str:
    """Parse Claude response to extract label."""
    content = response.get("content", [])
    for block in content:
        if block.get("type") == "text":
            text = block.get("text", "").strip()

            # Try JSON parsing first
            try:
                parsed = json.loads(text)
                label = parsed.get("label", "").strip()
                if label:
                    return label
            except json.JSONDecodeError:
                pass

            # Fallback: extract from markdown or plain text
            # Look for "label": "..." pattern
            match = re.search(r'"label"\s*:\s*"([^"]+)"', text)
            if match:
                return match.group(1).strip()

            # Last resort: return cleaned text if short enough
            if len(text) < 100 and text:
                # Remove quotes and common prefixes
                cleaned = text.strip('"\'').strip()
                if cleaned:
                    return cleaned

    raise ValueError("Failed to extract label from LLM response")


def generate_macro_label(
    unit_insights: List[UnitInsight],
    bedrock: BedrockClient,
) -> str:
    """
    Generate a concise macro insight label from grouped unit insights using LLM.

    Args:
        unit_insights: List of UnitInsight DB models
        bedrock: Bedrock client instance

    Returns:
        Concise label string (5-8 words)
    """
    items = "\n".join(f"- {ui.name}: {ui.description}" for ui in unit_insights)

    prompt = f"""You are a healthcare strategy analyst.

Below is a group of closely related health insights extracted from podcasts and documents.

Your task:
- Produce ONE concise macro insight label (5-8 words max)
- Use professional, strategic health language
- Capture the shared underlying theme
- Do NOT repeat full unit insight names
- Do NOT use generic fillers (e.g. "various", "multiple")

Unit insights:
{items}

Return ONLY valid JSON:
{{ "label": "..." }}"""

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120,
        "temperature": 0.2,
    }

    response = bedrock.invoke(payload)
    return _parse_llm_label_response(response)


def generate_macro_description(
    label: str,
    unit_insights: List[UnitInsight],
    bedrock: BedrockClient,
) -> str:
    """
    Generate a description for a macro insight.

    Args:
        label: The macro insight label
        unit_insights: List of UnitInsight DB models
        bedrock: Bedrock client instance

    Returns:
        Description string (1-2 sentences)
    """
    items = "\n".join(f"- {ui.name}" for ui in unit_insights[:5])

    prompt = f"""You are a healthcare strategy analyst.

Macro insight label: {label}

Constituent unit insights:
{items}

Write a 1-2 sentence description that explains what this macro insight represents.
Be specific and professional. Do not use generic language and go straight to the point (do not start with "this macro represent..")

Return ONLY the description text, no JSON or formatting."""

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.3,
    }

    response = bedrock.invoke(payload)
    content = response.get("content", [])
    for block in content:
        if block.get("type") == "text":
            return block.get("text", "").strip()

    return ""


# Core Clustering Algorithm
def cluster_unit_insights(
    unit_insights: List[UnitInsight],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> List[tuple[List[int], np.ndarray]]:
    """
    Cluster unit insights by embedding similarity using greedy algorithm.

    Args:
        unit_insights: List of UnitInsight DB models (must have embeddings)
        similarity_threshold: Minimum cosine similarity for clustering

    Returns:
        List of (unit_insight_ids, centroid_vector) tuples
    """
    if not unit_insights:
        return []

    # Build ID -> normalized vector map
    vectors: dict[int, np.ndarray] = {}
    for ui in unit_insights:
        if ui.embedding is None:
            log.warning("[MACRO] UnitInsight %d has no embedding, skipping", ui.id)
            continue
        vec = np.array(ui.embedding, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        vectors[ui.id] = vec

    used: set[int] = set()
    clusters: List[tuple[List[int], np.ndarray]] = []

    # Greedy clustering
    for ui in unit_insights:
        if ui.id in used or ui.id not in vectors:
            continue

        base_vec = vectors[ui.id]
        cluster_ids = [ui.id]
        cluster_vecs = [base_vec]

        for other in unit_insights:
            if other.id in used or other.id == ui.id or other.id not in vectors:
                continue

            sim = cosine_similarity(base_vec, vectors[other.id])
            if sim >= similarity_threshold:
                cluster_ids.append(other.id)
                cluster_vecs.append(vectors[other.id])

        # Compute centroid
        centroid = np.mean(cluster_vecs, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        clusters.append((cluster_ids, centroid))
        used.update(cluster_ids)

    return clusters


# Public API
def discover_macro_insights(
    session: Session,
    *,
    unit_insight_ids: Optional[List[int]] = None,
    similarity_threshold: float = 0.72,
    use_llm_naming: bool = True,
    generate_descriptions: bool = False,
    bedrock: Optional[BedrockClient] = None,
) -> List[MacroInsightCandidate]:
    """
    Discover macro insights from unassigned UnitInsights in the database.

    This is a pure discovery function that returns candidates without persisting.
    Use `discover_and_persist_macro_insights` for full persistence.

    Args:
        session: Database session
        unit_insight_ids: Optional list of UnitInsight IDs to process (default: all unassigned)
        similarity_threshold: Minimum cosine similarity for clustering (default 0.72)
        use_llm_naming: Use LLM to generate labels (default True)
        generate_descriptions: Also generate descriptions via LLM (default False)
        bedrock: Optional pre-configured BedrockClient

    Returns:
        List of MacroInsightCandidate objects
    """
    # Fetch unassigned unit insights
    query = session.query(UnitInsight).filter(UnitInsight.macro_insight_id.is_(None))
    if unit_insight_ids is not None:
        query = query.filter(UnitInsight.id.in_(unit_insight_ids))
    unit_insights = query.all()

    if not unit_insights:
        log.info("[MACRO] No unassigned UnitInsights found")
        return []

    log.info("[MACRO] Processing %d unassigned UnitInsights", len(unit_insights))

    # Build ID -> UnitInsight map
    ui_map = {ui.id: ui for ui in unit_insights}

    # Cluster
    clusters = cluster_unit_insights(unit_insights, similarity_threshold)
    log.info("[MACRO] Found %d clusters", len(clusters))

    # Initialize Bedrock if needed
    if use_llm_naming or generate_descriptions:
        bedrock = bedrock or BedrockClient()

    candidates: List[MacroInsightCandidate] = []

    for cluster_ids, centroid in clusters:
        cluster_units = [ui_map[uid] for uid in cluster_ids]

        # Generate label
        if use_llm_naming and len(cluster_units) > 1:
            try:
                label = generate_macro_label(cluster_units, bedrock)
            except Exception as e:
                log.warning("[MACRO] LLM naming failed, using fallback: %s", e)
                label = cluster_units[0].name
        else:
            label = cluster_units[0].name

        # Generate description
        description = None
        if len(cluster_units) > 1:
            # Multiple unit insights: generate description via LLM
            if generate_descriptions and bedrock:
                try:
                    description = generate_macro_description(label, cluster_units, bedrock)
                except Exception as e:
                    log.warning("[MACRO] Description generation failed: %s", e)
        else:
            # Single unit insight: reuse its description
            description = cluster_units[0].description

        candidates.append(
            MacroInsightCandidate(
                label=label,
                description=description,
                unit_insight_ids=cluster_ids,
                centroid=centroid.tolist(),
            )
        )

        log.info("[MACRO] '%s' <- %d unit insights", label, len(cluster_ids))

    return candidates


def persist_macro_insights(
    session: Session,
    candidates: List[MacroInsightCandidate],
) -> List[MacroInsight]:
    """
    Persist macro insight candidates to the database.

    Creates MacroInsight records and updates UnitInsight.macro_insight_id.

    Args:
        session: Database session
        candidates: List of MacroInsightCandidate objects

    Returns:
        List of created MacroInsight DB objects
    """
    created: List[MacroInsight] = []

    for candidate in candidates:
        # Create MacroInsight
        macro = MacroInsight(
            name=candidate.label,
            description=candidate.description,
            centroid_embedding=candidate.centroid,
        )
        session.add(macro)
        session.flush()  # Get ID

        # Update UnitInsights
        session.query(UnitInsight).filter(
            UnitInsight.id.in_(candidate.unit_insight_ids)
        ).update(
            {UnitInsight.macro_insight_id: macro.id},
            synchronize_session="fetch",
        )

        created.append(macro)

    session.commit()

    # Refresh to get final state
    for macro in created:
        session.refresh(macro)

    log.info("[MACRO] Persisted %d MacroInsights", len(created))
    return created


def discover_and_persist_macro_insights(
    session: Session,
    *,
    unit_insight_ids: Optional[List[int]] = None,
    similarity_threshold: float = 0.72,
    use_llm_naming: bool = True,
    generate_descriptions: bool = False,
    bedrock: Optional[BedrockClient] = None,
) -> List[MacroInsight]:
    """
    End-to-end macro insight discovery and persistence.

    Steps:
    1. Fetch unassigned UnitInsights from DB
    2. Cluster by embedding similarity
    3. Generate labels (and optionally descriptions) via LLM
    4. Persist MacroInsight records
    5. Update UnitInsight.macro_insight_id links

    Args:
        session: Database session
        unit_insight_ids: Optional list of UnitInsight IDs to process (default: all unassigned)
        similarity_threshold: Minimum cosine similarity for clustering
        use_llm_naming: Use LLM to generate labels
        generate_descriptions: Also generate descriptions via LLM
        bedrock: Optional pre-configured BedrockClient

    Returns:
        List of created MacroInsight DB objects
    """
    candidates = discover_macro_insights(
        session,
        unit_insight_ids=unit_insight_ids,
        similarity_threshold=similarity_threshold,
        use_llm_naming=use_llm_naming,
        generate_descriptions=generate_descriptions,
        bedrock=bedrock,
    )

    if not candidates:
        return []

    return persist_macro_insights(session, candidates)
