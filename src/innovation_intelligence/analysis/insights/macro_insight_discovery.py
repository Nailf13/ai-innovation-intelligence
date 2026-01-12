"""
Macro insight discovery via semantic clustering.

This module handles:
- Clustering UnitInsights by embedding similarity
- LLM-based naming for macro insights
- Persisting MacroInsight entities to database
- Incremental matching: new UnitInsights first match existing MacroInsight centroids

Key behaviors:
- A MacroInsight is created only when at least 2 UnitInsights are semantically similar
- Isolated UnitInsights remain unassigned as candidates for future clustering
- Incremental updates: new UnitInsights match existing centroids before forming new groups
- Centroid is updated when injecting new UnitInsights into existing MacroInsights
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
MIN_CLUSTER_SIZE = 2  # Minimum UnitInsights required to form a MacroInsight


# Data Structures (for internal use)
@dataclass
class MacroInsightCandidate:
    """Intermediate representation before DB persistence."""

    label: str
    description: Optional[str]
    unit_insight_ids: List[int]
    centroid: List[float]


@dataclass
class MacroInsightUpdate:
    """Represents an update to an existing MacroInsight."""

    macro_insight_id: int
    new_unit_insight_ids: List[int]
    updated_centroid: List[float]


# Similarity Helpers
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors (assumes normalized)."""
    return float(np.dot(a, b))


def normalize_vector(vec: np.ndarray) -> np.ndarray:
    """Normalize a vector to unit length."""
    norm = np.linalg.norm(vec)
    if norm > 0:
        return vec / norm
    return vec


def compute_centroid(vectors: List[np.ndarray]) -> np.ndarray:
    """Compute normalized centroid from a list of vectors."""
    centroid = np.mean(vectors, axis=0)
    return normalize_vector(centroid)


def match_against_existing_macros(
    session: Session,
    unit_insights: List[UnitInsight],
    similarity_threshold: float,
) -> tuple[List[MacroInsightUpdate], List[UnitInsight]]:
    """
    Match new UnitInsights against existing MacroInsight centroids.

    For each UnitInsight, find the best matching existing MacroInsight.
    If similarity exceeds threshold, schedule injection into that MacroInsight.

    Args:
        session: Database session
        unit_insights: List of unassigned UnitInsight DB models
        similarity_threshold: Minimum cosine similarity for matching

    Returns:
        Tuple of:
        - List of MacroInsightUpdate objects (grouped by macro_insight_id)
        - List of UnitInsights that did not match any existing MacroInsight
    """
    # Fetch all existing MacroInsights with their centroids
    existing_macros = session.query(MacroInsight).all()

    if not existing_macros:
        return [], unit_insights

    # Build macro centroid map
    macro_centroids: dict[int, np.ndarray] = {}
    for macro in existing_macros:
        if macro.centroid_embedding:
            vec = np.array(macro.centroid_embedding, dtype=np.float32)
            macro_centroids[macro.id] = normalize_vector(vec)

    if not macro_centroids:
        return [], unit_insights

    # Track which UnitInsights get assigned to which MacroInsight
    assignments: dict[int, List[int]] = {}  # macro_id -> [unit_insight_ids]
    unmatched: List[UnitInsight] = []

    for ui in unit_insights:
        if ui.embedding is None:
            log.warning("[MACRO] UnitInsight %d has no embedding, skipping", ui.id)
            unmatched.append(ui)
            continue

        vec = np.array(ui.embedding, dtype=np.float32)
        vec = normalize_vector(vec)

        # Find best matching MacroInsight
        best_macro_id: Optional[int] = None
        best_similarity = 0.0

        for macro_id, centroid in macro_centroids.items():
            sim = cosine_similarity(vec, centroid)
            if sim >= similarity_threshold and sim > best_similarity:
                best_similarity = sim
                best_macro_id = macro_id

        if best_macro_id is not None:
            if best_macro_id not in assignments:
                assignments[best_macro_id] = []
            assignments[best_macro_id].append(ui.id)
            log.info(
                "[MACRO] UnitInsight %d matched MacroInsight %d (sim=%.3f)",
                ui.id, best_macro_id, best_similarity
            )
        else:
            unmatched.append(ui)

    # Build MacroInsightUpdate objects with updated centroids
    updates: List[MacroInsightUpdate] = []

    for macro_id, new_ui_ids in assignments.items():
        # Get existing UnitInsights in this MacroInsight
        existing_uis = session.query(UnitInsight).filter(
            UnitInsight.macro_insight_id == macro_id
        ).all()

        # Collect all embeddings (existing + new)
        all_vectors: List[np.ndarray] = []

        for ui in existing_uis:
            if ui.embedding:
                vec = np.array(ui.embedding, dtype=np.float32)
                all_vectors.append(normalize_vector(vec))

        # Add new UnitInsight embeddings
        new_uis = session.query(UnitInsight).filter(UnitInsight.id.in_(new_ui_ids)).all()
        for ui in new_uis:
            if ui.embedding:
                vec = np.array(ui.embedding, dtype=np.float32)
                all_vectors.append(normalize_vector(vec))

        # Compute updated centroid
        if all_vectors:
            updated_centroid = compute_centroid(all_vectors)
            updates.append(MacroInsightUpdate(
                macro_insight_id=macro_id,
                new_unit_insight_ids=new_ui_ids,
                updated_centroid=updated_centroid.tolist(),
            ))

    return updates, unmatched


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
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> tuple[List[tuple[List[int], np.ndarray]], List[int]]:
    """
    Cluster unit insights by embedding similarity using greedy algorithm.

    Only creates clusters with at least `min_cluster_size` members.
    Isolated UnitInsights are returned separately as unassigned.

    Args:
        unit_insights: List of UnitInsight DB models (must have embeddings)
        similarity_threshold: Minimum cosine similarity for clustering
        min_cluster_size: Minimum number of UnitInsights to form a cluster (default 2)

    Returns:
        Tuple of:
        - List of (unit_insight_ids, centroid_vector) tuples for valid clusters
        - List of isolated UnitInsight IDs that remain unassigned
    """
    if not unit_insights:
        return [], []

    # Build ID -> normalized vector map
    vectors: dict[int, np.ndarray] = {}
    for ui in unit_insights:
        if ui.embedding is None:
            log.warning("[MACRO] UnitInsight %d has no embedding, skipping", ui.id)
            continue
        vec = np.array(ui.embedding, dtype=np.float32)
        vectors[ui.id] = normalize_vector(vec)

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

        # Only create cluster if it meets minimum size requirement
        if len(cluster_ids) >= min_cluster_size:
            centroid = compute_centroid(cluster_vecs)
            clusters.append((cluster_ids, centroid))
            used.update(cluster_ids)

    # Collect isolated (unassigned) UnitInsight IDs
    isolated_ids = [ui.id for ui in unit_insights if ui.id in vectors and ui.id not in used]

    return clusters, isolated_ids


@dataclass
class IncrementalDiscoveryResult:
    """Result of incremental macro insight discovery."""

    # New MacroInsights to create (clusters of >= MIN_CLUSTER_SIZE)
    new_candidates: List[MacroInsightCandidate]

    # Updates to existing MacroInsights (new UnitInsights injected)
    updates: List[MacroInsightUpdate]

    # UnitInsight IDs that remain unassigned (isolated, no matches)
    isolated_ids: List[int]


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

    Note: This function only returns NEW MacroInsight candidates. For incremental
    updates that also inject into existing MacroInsights, use
    `discover_macro_insights_incremental`.

    Args:
        session: Database session
        unit_insight_ids: Optional list of UnitInsight IDs to process (default: all unassigned)
        similarity_threshold: Minimum cosine similarity for clustering (default 0.72)
        use_llm_naming: Use LLM to generate labels (default True)
        generate_descriptions: Also generate descriptions via LLM (default False)
        bedrock: Optional pre-configured BedrockClient

    Returns:
        List of MacroInsightCandidate objects (only clusters with >= 2 UnitInsights)
    """
    result = discover_macro_insights_incremental(
        session,
        unit_insight_ids=unit_insight_ids,
        similarity_threshold=similarity_threshold,
        use_llm_naming=use_llm_naming,
        generate_descriptions=generate_descriptions,
        bedrock=bedrock,
    )
    return result.new_candidates


def discover_macro_insights_incremental(
    session: Session,
    *,
    unit_insight_ids: Optional[List[int]] = None,
    similarity_threshold: float = 0.72,
    use_llm_naming: bool = True,
    generate_descriptions: bool = False,
    bedrock: Optional[BedrockClient] = None,
) -> IncrementalDiscoveryResult:
    """
    Incremental macro insight discovery that supports continuous ingestion.

    Algorithm:
    1. Fetch unassigned UnitInsights
    2. Match each against existing MacroInsight centroids
    3. If similarity >= threshold, inject into existing MacroInsight (update centroid)
    4. For remaining unmatched UnitInsights, cluster among themselves
    5. Only create new MacroInsights for clusters with >= 2 members
    6. Isolated UnitInsights remain unassigned for future clustering

    Args:
        session: Database session
        unit_insight_ids: Optional list of UnitInsight IDs to process (default: all unassigned)
        similarity_threshold: Minimum cosine similarity for clustering (default 0.72)
        use_llm_naming: Use LLM to generate labels (default True)
        generate_descriptions: Also generate descriptions via LLM (default False)
        bedrock: Optional pre-configured BedrockClient

    Returns:
        IncrementalDiscoveryResult with new candidates, updates, and isolated IDs
    """
    # Fetch unassigned unit insights
    query = session.query(UnitInsight).filter(UnitInsight.macro_insight_id.is_(None))
    if unit_insight_ids is not None:
        query = query.filter(UnitInsight.id.in_(unit_insight_ids))
    unit_insights = query.all()

    if not unit_insights:
        log.info("[MACRO] No unassigned UnitInsights found")
        return IncrementalDiscoveryResult(
            new_candidates=[],
            updates=[],
            isolated_ids=[],
        )

    log.info("[MACRO] Processing %d unassigned UnitInsights", len(unit_insights))

    # Step 1: Match against existing MacroInsight centroids
    updates, unmatched = match_against_existing_macros(
        session, unit_insights, similarity_threshold
    )

    if updates:
        log.info(
            "[MACRO] %d UnitInsights matched existing MacroInsights (%d updates)",
            sum(len(u.new_unit_insight_ids) for u in updates),
            len(updates),
        )

    if not unmatched:
        log.info("[MACRO] All UnitInsights matched existing MacroInsights")
        return IncrementalDiscoveryResult(
            new_candidates=[],
            updates=updates,
            isolated_ids=[],
        )

    log.info("[MACRO] %d UnitInsights did not match existing MacroInsights", len(unmatched))

    # Step 2: Cluster unmatched UnitInsights among themselves
    clusters, isolated_ids = cluster_unit_insights(
        unmatched, similarity_threshold, min_cluster_size=MIN_CLUSTER_SIZE
    )

    if isolated_ids:
        log.info(
            "[MACRO] %d UnitInsights remain isolated (candidates for future clustering)",
            len(isolated_ids),
        )

    if not clusters:
        log.info("[MACRO] No new clusters formed from unmatched UnitInsights")
        return IncrementalDiscoveryResult(
            new_candidates=[],
            updates=updates,
            isolated_ids=isolated_ids,
        )

    log.info("[MACRO] Found %d new clusters from unmatched UnitInsights", len(clusters))

    # Build ID -> UnitInsight map for unmatched
    ui_map = {ui.id: ui for ui in unmatched}

    # Initialize Bedrock if needed
    if use_llm_naming or generate_descriptions:
        bedrock = bedrock or BedrockClient()

    candidates: List[MacroInsightCandidate] = []

    for cluster_ids, centroid in clusters:
        cluster_units = [ui_map[uid] for uid in cluster_ids]

        # Generate label (always use LLM for clusters since they have >= 2 members)
        if use_llm_naming:
            try:
                label = generate_macro_label(cluster_units, bedrock)
            except Exception as e:
                log.warning("[MACRO] LLM naming failed, using fallback: %s", e)
                label = cluster_units[0].name
        else:
            label = cluster_units[0].name

        # Generate description via LLM for multi-member clusters
        description = None
        if generate_descriptions and bedrock:
            try:
                description = generate_macro_description(label, cluster_units, bedrock)
            except Exception as e:
                log.warning("[MACRO] Description generation failed: %s", e)

        candidates.append(
            MacroInsightCandidate(
                label=label,
                description=description,
                unit_insight_ids=cluster_ids,
                centroid=centroid.tolist(),
            )
        )

        log.info("[MACRO] New cluster '%s' <- %d unit insights", label, len(cluster_ids))

    return IncrementalDiscoveryResult(
        new_candidates=candidates,
        updates=updates,
        isolated_ids=isolated_ids,
    )


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

    log.info("[MACRO] Persisted %d new MacroInsights", len(created))
    return created


def apply_macro_insight_updates(
    session: Session,
    updates: List[MacroInsightUpdate],
) -> List[MacroInsight]:
    """
    Apply updates to existing MacroInsights.

    Injects new UnitInsights into existing MacroInsights and updates centroids.

    Args:
        session: Database session
        updates: List of MacroInsightUpdate objects

    Returns:
        List of updated MacroInsight DB objects
    """
    updated_macros: List[MacroInsight] = []

    for update in updates:
        # Fetch the existing MacroInsight
        macro = session.query(MacroInsight).get(update.macro_insight_id)
        if not macro:
            log.warning(
                "[MACRO] MacroInsight %d not found, skipping update",
                update.macro_insight_id,
            )
            continue

        # Update the centroid
        macro.centroid_embedding = update.updated_centroid

        # Link new UnitInsights to this MacroInsight
        session.query(UnitInsight).filter(
            UnitInsight.id.in_(update.new_unit_insight_ids)
        ).update(
            {UnitInsight.macro_insight_id: macro.id},
            synchronize_session="fetch",
        )

        updated_macros.append(macro)
        log.info(
            "[MACRO] Updated MacroInsight %d '%s' with %d new UnitInsights",
            macro.id, macro.name, len(update.new_unit_insight_ids),
        )

    session.commit()

    # Refresh to get final state
    for macro in updated_macros:
        session.refresh(macro)

    if updated_macros:
        log.info("[MACRO] Updated %d existing MacroInsights", len(updated_macros))

    return updated_macros


@dataclass
class PersistenceResult:
    """Result of incremental macro insight persistence."""

    # Newly created MacroInsights
    created: List[MacroInsight]

    # Updated existing MacroInsights
    updated: List[MacroInsight]

    # UnitInsight IDs that remain unassigned
    isolated_ids: List[int]

    @property
    def total_macros_affected(self) -> int:
        """Total number of MacroInsights created or updated."""
        return len(self.created) + len(self.updated)


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

    This function maintains backward compatibility by returning only newly created
    MacroInsights. For full incremental results including updates, use
    `discover_and_persist_macro_insights_incremental`.

    Steps:
    1. Fetch unassigned UnitInsights from DB
    2. Match against existing MacroInsight centroids (inject if similar)
    3. Cluster remaining unmatched UnitInsights
    4. Only create new MacroInsights for clusters with >= 2 members
    5. Isolated UnitInsights remain unassigned for future clustering

    Args:
        session: Database session
        unit_insight_ids: Optional list of UnitInsight IDs to process (default: all unassigned)
        similarity_threshold: Minimum cosine similarity for clustering
        use_llm_naming: Use LLM to generate labels
        generate_descriptions: Also generate descriptions via LLM
        bedrock: Optional pre-configured BedrockClient

    Returns:
        List of newly created MacroInsight DB objects
    """
    result = discover_and_persist_macro_insights_incremental(
        session,
        unit_insight_ids=unit_insight_ids,
        similarity_threshold=similarity_threshold,
        use_llm_naming=use_llm_naming,
        generate_descriptions=generate_descriptions,
        bedrock=bedrock,
    )
    return result.created


def discover_and_persist_macro_insights_incremental(
    session: Session,
    *,
    unit_insight_ids: Optional[List[int]] = None,
    similarity_threshold: float = 0.72,
    use_llm_naming: bool = True,
    generate_descriptions: bool = False,
    bedrock: Optional[BedrockClient] = None,
) -> PersistenceResult:
    """
    Incremental macro insight discovery and persistence.

    Full incremental workflow:
    1. Fetch unassigned UnitInsights from DB
    2. Match against existing MacroInsight centroids
       - If similarity >= threshold, inject into existing MacroInsight and update centroid
    3. Cluster remaining unmatched UnitInsights
    4. Only create new MacroInsights for clusters with >= 2 members
    5. Isolated UnitInsights remain unassigned for future clustering

    Args:
        session: Database session
        unit_insight_ids: Optional list of UnitInsight IDs to process (default: all unassigned)
        similarity_threshold: Minimum cosine similarity for clustering
        use_llm_naming: Use LLM to generate labels
        generate_descriptions: Also generate descriptions via LLM
        bedrock: Optional pre-configured BedrockClient

    Returns:
        PersistenceResult with created, updated MacroInsights, and isolated IDs
    """
    # Discover candidates and updates
    discovery_result = discover_macro_insights_incremental(
        session,
        unit_insight_ids=unit_insight_ids,
        similarity_threshold=similarity_threshold,
        use_llm_naming=use_llm_naming,
        generate_descriptions=generate_descriptions,
        bedrock=bedrock,
    )

    created: List[MacroInsight] = []
    updated: List[MacroInsight] = []

    # Apply updates to existing MacroInsights
    if discovery_result.updates:
        updated = apply_macro_insight_updates(session, discovery_result.updates)

    # Persist new MacroInsight candidates
    if discovery_result.new_candidates:
        created = persist_macro_insights(session, discovery_result.new_candidates)

    # Log summary
    total_assigned = (
        sum(len(c.unit_insight_ids) for c in discovery_result.new_candidates)
        + sum(len(u.new_unit_insight_ids) for u in discovery_result.updates)
    )
    log.info(
        "[MACRO] Summary: %d UnitInsights assigned, %d MacroInsights created, "
        "%d MacroInsights updated, %d UnitInsights remain isolated",
        total_assigned,
        len(created),
        len(updated),
        len(discovery_result.isolated_ids),
    )

    return PersistenceResult(
        created=created,
        updated=updated,
        isolated_ids=discovery_result.isolated_ids,
    )
