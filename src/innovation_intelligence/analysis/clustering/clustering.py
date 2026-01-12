"""
Strategic cluster assignment for macro insights.

This module handles:
- Embedding-based assignment of macro insights to strategic clusters
- Lazy loading and caching of cluster embeddings
- Database persistence of cluster assignments
- Support for dynamic "Other / Emerging" cluster
- Incremental updates when MacroInsight centroids change

The clustering hierarchy:
    UnitInsight → MacroInsight → Cluster (strategic)

Strategic clusters are predefined high-level categories. MacroInsights that
don't fit any predefined cluster (below similarity threshold) are assigned
to "Other / Emerging".

Incremental update process:
- When MacroInsight centroids are updated (new UnitInsights injected), re-evaluate
  their cluster assignments
- Track which assignments changed vs stayed the same
- Support both newly created and updated MacroInsights in a single operation
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

import numpy as np
from sqlalchemy.orm import Session

from innovation_intelligence.analysis.insights.embedder import embed_text
from innovation_intelligence.db.models import Cluster, MacroInsight, UnitInsight
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
DEFAULT_SIMILARITY_THRESHOLD = 0.5
OTHER_CLUSTER_NAME = "Other"


class ClusterDefinitionType(str, Enum):
    """Type of cluster definition source."""
    PREDEFINED = "predefined"
    DYNAMIC = "dynamic"


# ---------------------------------------------------------------------
# Predefined strategic clusters
# ---------------------------------------------------------------------
STRATEGIC_CLUSTER_DEFINITIONS: Dict[str, str] = {
    "Metabolic Health & Lifestyle-Driven Conditions": (
        "metabolic health diabetes obesity overweight insulin resistance blood sugar glucose "
        "weight management weight loss appetite satiety nutrition diet lifestyle chronic disease "
        "cardiovascular cholesterol lipids triglycerides hypertension prevention metabolic fitness "
        "protein fiber low sugar low carb glycemic index biohacking"
    ),
    "Mental, Emotional & Cognitive Well-Being": (
        "mental health cognition cognitive brain focus memory mood stress anxiety depression burnout "
        "sleep relaxation mindfulness emotional resilience psychology wellbeing nervous system "
        "neurotransmitters magnesium adaptogens omega-3 gut brain axis stress management"
    ),
    "Women's Health & Hormonal Balance": (
        "women health female health hormones hormonal balance menstrual cycle menopause perimenopause "
        "fertility pregnancy postpartum reproductive health estrogen progesterone pcos endometriosis "
        "bone density iron deficiency maternal health lifecycle endocrine"
    ),
    "Healthy Aging, Longevity & Vitality": (
        "aging longevity lifespan healthspan vitality senior elderly frailty sarcopenia autonomy "
        "anti aging prevention cellular health regeneration senescence telomeres NAD sirtuins "
        "cognitive decline mobility bone health muscle mass independence"
    ),
    "Immunity & Gut Health": (
        "immunity immune system gut health microbiome digestion digestive health inflammation "
        "probiotics prebiotics synbiotics fermented intestinal barrier leaky gut "
        "infection resistance respiratory immunity vitamins zinc vitamin D vitamin C "
        "autoimmune sensitivities microbiota"
    ),
    "Physical Resilience & Performance": (
        "physical resilience performance fitness strength endurance stamina recovery mobility "
        "coordination posture balance motor skills musculoskeletal joints tendons bones "
        "physical development children youth adults training rehabilitation injury prevention "
        "sedentary lifestyle body literacy functional capacity"
    ),
}


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class ClusterDefinition:
    """Immutable definition of a strategic cluster."""
    name: str
    description: str
    definition_type: ClusterDefinitionType = ClusterDefinitionType.PREDEFINED


@dataclass
class ClusterAssignment:
    """Result of assigning a macro insight to a strategic cluster."""
    macro_insight_id: int
    macro_insight_label: str
    cluster_name: str
    similarity_score: float
    is_other: bool = False


@dataclass
class ClusterAssignmentChange:
    """Represents a change in cluster assignment for a MacroInsight."""
    macro_insight_id: int
    macro_insight_label: str
    old_cluster_name: Optional[str]
    new_cluster_name: str
    similarity_score: float
    is_new_assignment: bool  # True if no previous cluster assignment
    is_changed: bool  # True if cluster changed (including None -> cluster)

    @property
    def is_other(self) -> bool:
        """Check if assigned to Other cluster."""
        return self.new_cluster_name == OTHER_CLUSTER_NAME


@dataclass
class ClusteringResult:
    """Aggregate result of a clustering operation."""
    assignments: List[ClusterAssignment] = field(default_factory=list)
    clusters_used: Dict[str, int] = field(default_factory=dict)
    total_processed: int = 0
    total_assigned_to_other: int = 0

    def add_assignment(self, assignment: ClusterAssignment) -> None:
        """Add an assignment and update counters."""
        self.assignments.append(assignment)
        self.total_processed += 1

        if assignment.is_other:
            self.total_assigned_to_other += 1

        if assignment.cluster_name not in self.clusters_used:
            self.clusters_used[assignment.cluster_name] = 0
        self.clusters_used[assignment.cluster_name] += 1


@dataclass
class IncrementalClusteringResult:
    """Result of an incremental clustering update operation."""

    # All assignment changes (new + updated + unchanged)
    changes: List[ClusterAssignmentChange] = field(default_factory=list)

    # Clusters used and their counts
    clusters_used: Dict[str, int] = field(default_factory=dict)

    # Counters
    total_processed: int = 0
    total_new_assignments: int = 0
    total_changed: int = 0
    total_unchanged: int = 0
    total_assigned_to_other: int = 0

    def add_change(self, change: ClusterAssignmentChange) -> None:
        """Add a change and update counters."""
        self.changes.append(change)
        self.total_processed += 1

        if change.is_new_assignment:
            self.total_new_assignments += 1

        if change.is_changed:
            self.total_changed += 1
        else:
            self.total_unchanged += 1

        if change.is_other:
            self.total_assigned_to_other += 1

        if change.new_cluster_name not in self.clusters_used:
            self.clusters_used[change.new_cluster_name] = 0
        self.clusters_used[change.new_cluster_name] += 1

    @property
    def assignments(self) -> List[ClusterAssignment]:
        """Convert changes to ClusterAssignment for backward compatibility."""
        return [
            ClusterAssignment(
                macro_insight_id=c.macro_insight_id,
                macro_insight_label=c.macro_insight_label,
                cluster_name=c.new_cluster_name,
                similarity_score=c.similarity_score,
                is_other=c.is_other,
            )
            for c in self.changes
        ]

    def to_clustering_result(self) -> ClusteringResult:
        """Convert to basic ClusteringResult for backward compatibility."""
        result = ClusteringResult()
        for change in self.changes:
            result.add_assignment(
                ClusterAssignment(
                    macro_insight_id=change.macro_insight_id,
                    macro_insight_label=change.macro_insight_label,
                    cluster_name=change.new_cluster_name,
                    similarity_score=change.similarity_score,
                    is_other=change.is_other,
                )
            )
        return result


# ---------------------------------------------------------------------
# Protocol for embedding provider (for testability)
# ---------------------------------------------------------------------
class EmbeddingProvider(Protocol):
    """Protocol for embedding text."""
    def __call__(self, text: str) -> List[float]: ...


# ---------------------------------------------------------------------
# Core similarity computation
# ---------------------------------------------------------------------
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Compute cosine similarity between two vectors.

    Assumes vectors are L2-normalized for efficiency.
    If not normalized, results will still be valid but less efficient.

    Args:
        a: First vector (numpy array)
        b: Second vector (numpy array)

    Returns:
        Cosine similarity in range [-1, 1]
    """
    return float(np.dot(a, b))


def normalize_vector(vec: np.ndarray) -> np.ndarray:
    """
    L2-normalize a vector.

    Args:
        vec: Input vector

    Returns:
        Normalized vector with unit L2 norm

    Raises:
        ValueError: If vector has zero norm
    """
    norm = np.linalg.norm(vec)
    if norm == 0:
        raise ValueError("Cannot normalize zero vector")
    return vec / norm


# ---------------------------------------------------------------------
# Cluster embedding cache
# ---------------------------------------------------------------------
class ClusterEmbeddingCache:
    """
    Lazy-loaded cache for strategic cluster embeddings.

    This class handles:
    - Lazy computation of embeddings on first access
    - Thread-safe singleton pattern via module-level instance
    - Support for custom embedding providers (for testing)
    """

    def __init__(
        self,
        definitions: Optional[Dict[str, str]] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        """
        Initialize the cache.

        Args:
            definitions: Cluster name -> description mapping
            embedding_provider: Function to embed text (default: embed_text)
        """
        self._definitions = definitions or STRATEGIC_CLUSTER_DEFINITIONS
        self._embedding_provider = embedding_provider or embed_text
        self._embeddings: Optional[Dict[str, np.ndarray]] = None

    @property
    def embeddings(self) -> Dict[str, np.ndarray]:
        """
        Get cluster embeddings, computing them lazily on first access.

        Returns:
            Dictionary mapping cluster name to normalized embedding vector
        """
        if self._embeddings is None:
            self._embeddings = self._compute_embeddings()
        return self._embeddings

    def _compute_embeddings(self) -> Dict[str, np.ndarray]:
        """Compute and cache embeddings for all cluster definitions."""
        log.info("[CLUSTER] Computing embeddings for %d strategic clusters", len(self._definitions))

        embeddings = {}
        for name, description in self._definitions.items():
            vec = np.array(self._embedding_provider(description), dtype=np.float32)
            embeddings[name] = normalize_vector(vec)
            log.debug("[CLUSTER] Embedded cluster: %s", name)

        return embeddings

    def get_embedding(self, cluster_name: str) -> Optional[np.ndarray]:
        """
        Get embedding for a specific cluster.

        Args:
            cluster_name: Name of the cluster

        Returns:
            Normalized embedding vector or None if cluster not found
        """
        return self.embeddings.get(cluster_name)

    @property
    def cluster_names(self) -> List[str]:
        """Get list of all cluster names."""
        return list(self._definitions.keys())

    def clear(self) -> None:
        """Clear cached embeddings (useful for testing)."""
        self._embeddings = None


# Module-level singleton cache
@lru_cache(maxsize=1)
def get_cluster_embedding_cache() -> ClusterEmbeddingCache:
    """Get the singleton cluster embedding cache."""
    return ClusterEmbeddingCache()


# ---------------------------------------------------------------------
# Core assignment logic
# ---------------------------------------------------------------------
def find_best_cluster(
    macro_embedding: np.ndarray,
    cluster_embeddings: Dict[str, np.ndarray],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Tuple[str, float, bool]:
    """
    Find the best matching cluster for a macro insight embedding.

    Args:
        macro_embedding: Normalized embedding vector of the macro insight
        cluster_embeddings: Dict mapping cluster names to embeddings
        similarity_threshold: Minimum similarity to assign to a cluster

    Returns:
        Tuple of (cluster_name, similarity_score, is_other)
        is_other is True if similarity was below threshold
    """
    if len(cluster_embeddings) == 0:
        return OTHER_CLUSTER_NAME, 0.0, True

    best_cluster: Optional[str] = None
    best_score: float = -1.0

    for name, cluster_vec in cluster_embeddings.items():
        score = cosine_similarity(macro_embedding, cluster_vec)
        if score > best_score:
            best_score = score
            best_cluster = name

    if best_score >= similarity_threshold and best_cluster is not None:
        return best_cluster, best_score, False

    return OTHER_CLUSTER_NAME, best_score, True


def assign_macro_insight(
    macro: MacroInsight,
    cluster_cache: ClusterEmbeddingCache,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> ClusterAssignment:
    """
    Assign a single macro insight to a strategic cluster.

    Args:
        macro: MacroInsight database model
        cluster_cache: Cache containing cluster embeddings
        similarity_threshold: Minimum similarity for cluster assignment

    Returns:
        ClusterAssignment with assignment details

    Raises:
        ValueError: If macro insight has no centroid embedding
    """
    if macro.centroid_embedding is None:
        raise ValueError(f"MacroInsight {macro.id} has no centroid embedding")

    # Get and normalize the macro insight embedding
    macro_vec = np.array(macro.centroid_embedding, dtype=np.float32)
    macro_vec = normalize_vector(macro_vec)

    # Find best cluster
    cluster_name, score, is_other = find_best_cluster(
        macro_vec,
        cluster_cache.embeddings,
        similarity_threshold,
    )

    return ClusterAssignment(
        macro_insight_id=macro.id,
        macro_insight_label=macro.name,
        cluster_name=cluster_name,
        similarity_score=score,
        is_other=is_other,
    )


def assign_macro_insight_with_change_tracking(
    macro: MacroInsight,
    cluster_cache: ClusterEmbeddingCache,
    session: Session,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> ClusterAssignmentChange:
    """
    Assign a macro insight to a cluster and track changes from previous assignment.

    Args:
        macro: MacroInsight database model
        cluster_cache: Cache containing cluster embeddings
        session: SQLAlchemy session (to resolve current cluster name)
        similarity_threshold: Minimum similarity for cluster assignment

    Returns:
        ClusterAssignmentChange with old/new cluster info

    Raises:
        ValueError: If macro insight has no centroid embedding
    """
    # Get current cluster name (if any)
    old_cluster_name: Optional[str] = None
    if macro.cluster_id is not None:
        current_cluster = session.query(Cluster).get(macro.cluster_id)
        if current_cluster:
            old_cluster_name = current_cluster.name

    # Compute new assignment
    assignment = assign_macro_insight(macro, cluster_cache, similarity_threshold)

    # Determine if this is a change
    is_new_assignment = old_cluster_name is None
    is_changed = old_cluster_name != assignment.cluster_name

    return ClusterAssignmentChange(
        macro_insight_id=macro.id,
        macro_insight_label=macro.name,
        old_cluster_name=old_cluster_name,
        new_cluster_name=assignment.cluster_name,
        similarity_score=assignment.similarity_score,
        is_new_assignment=is_new_assignment,
        is_changed=is_changed,
    )


# ---------------------------------------------------------------------
# Batch assignment
# ---------------------------------------------------------------------
def assign_macro_insights_to_clusters(
    macro_insights: Sequence[MacroInsight],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    cluster_cache: Optional[ClusterEmbeddingCache] = None,
) -> ClusteringResult:
    """
    Assign multiple macro insights to strategic clusters.

    This is the main public API for clustering without database persistence.

    Args:
        macro_insights: Sequence of MacroInsight database models
        similarity_threshold: Minimum cosine similarity for cluster assignment
        cluster_cache: Optional custom cache (uses singleton by default)

    Returns:
        ClusteringResult with all assignments and statistics
    """
    if not macro_insights:
        log.info("[CLUSTER] No macro insights to process")
        return ClusteringResult()

    cache = cluster_cache or get_cluster_embedding_cache()
    result = ClusteringResult()

    log.info(
        "[CLUSTER] Assigning %d macro insights (threshold=%.2f)",
        len(macro_insights),
        similarity_threshold,
    )

    for macro in macro_insights:
        try:
            assignment = assign_macro_insight(macro, cache, similarity_threshold)
            result.add_assignment(assignment)

            log.info(
                "[CLUSTER] '%s' → %s (%.3f)%s",
                assignment.macro_insight_label,
                assignment.cluster_name,
                assignment.similarity_score,
                " [OTHER]" if assignment.is_other else "",
            )
        except ValueError as e:
            log.warning("[CLUSTER] Skipping macro insight: %s", e)
            continue

    log.info(
        "[CLUSTER] Completed: %d processed, %d to 'Other'",
        result.total_processed,
        result.total_assigned_to_other,
    )

    return result


# ---------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------
def get_or_create_cluster(
    session: Session,
    cluster_name: str,
    description: Optional[str] = None,
) -> Cluster:
    """
    Get existing cluster or create new one.

    Args:
        session: SQLAlchemy session
        cluster_name: Name of the cluster
        description: Optional description for new clusters

    Returns:
        Cluster database model
    """
    cluster = session.query(Cluster).filter(Cluster.name == cluster_name).first()

    if cluster is None:
        cluster = Cluster(
            name=cluster_name,
            description=description or STRATEGIC_CLUSTER_DEFINITIONS.get(cluster_name),
        )
        session.add(cluster)
        session.flush()
        log.info("[CLUSTER] Created new cluster: %s", cluster_name)

    return cluster


def persist_cluster_assignments(
    session: Session,
    result: ClusteringResult,
) -> Dict[str, Cluster]:
    """
    Persist cluster assignments to the database.

    Updates MacroInsight.cluster_id for all assignments.

    Args:
        session: SQLAlchemy session
        result: ClusteringResult from assign_macro_insights_to_clusters

    Returns:
        Dictionary mapping cluster names to Cluster models
    """
    if not result.assignments:
        return {}

    # Get or create all required clusters
    cluster_map: Dict[str, Cluster] = {}
    for cluster_name in result.clusters_used.keys():
        cluster_map[cluster_name] = get_or_create_cluster(session, cluster_name)

    # Update macro insight cluster assignments
    for assignment in result.assignments:
        cluster = cluster_map[assignment.cluster_name]
        session.query(MacroInsight).filter(
            MacroInsight.id == assignment.macro_insight_id
        ).update(
            {MacroInsight.cluster_id: cluster.id},
            synchronize_session="fetch",
        )

    session.commit()

    log.info(
        "[CLUSTER] Persisted %d assignments to %d clusters",
        len(result.assignments),
        len(cluster_map),
    )

    return cluster_map


# ---------------------------------------------------------------------
# End-to-end public API
# ---------------------------------------------------------------------
def assign_and_persist_macro_insights(
    session: Session,
    macro_insight_ids: Optional[List[int]] = None,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> ClusteringResult:
    """
    End-to-end macro insight clustering and persistence.

    This is the main entry point for production use.

    Steps:
    1. Fetch unassigned MacroInsights from database
    2. Compute cluster assignments using embedding similarity
    3. Create/update Cluster records
    4. Update MacroInsight.cluster_id links

    Args:
        session: SQLAlchemy database session
        macro_insight_ids: Optional list of specific IDs to process
                          (default: all unassigned)
        similarity_threshold: Minimum similarity for cluster assignment

    Returns:
        ClusteringResult with assignments and statistics
    """
    # Build query for unassigned macro insights
    query = session.query(MacroInsight).filter(MacroInsight.cluster_id.is_(None))

    if macro_insight_ids is not None:
        query = query.filter(MacroInsight.id.in_(macro_insight_ids))

    macro_insights = query.all()

    if not macro_insights:
        log.info("[CLUSTER] No unassigned MacroInsights found")
        return ClusteringResult()

    # Perform assignment
    result = assign_macro_insights_to_clusters(macro_insights, similarity_threshold)

    # Persist to database
    persist_cluster_assignments(session, result)

    return result


# ---------------------------------------------------------------------
# Incremental update API
# ---------------------------------------------------------------------
def assign_macro_insights_incremental(
    session: Session,
    macro_insights: Sequence[MacroInsight],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    cluster_cache: Optional[ClusterEmbeddingCache] = None,
) -> IncrementalClusteringResult:
    """
    Assign macro insights to clusters with change tracking.

    This function tracks whether assignments changed from previous values,
    useful for incremental updates when MacroInsight centroids have been updated.

    Args:
        session: SQLAlchemy session (needed to resolve current cluster names)
        macro_insights: Sequence of MacroInsight database models
        similarity_threshold: Minimum cosine similarity for cluster assignment
        cluster_cache: Optional custom cache (uses singleton by default)

    Returns:
        IncrementalClusteringResult with change tracking information
    """
    if not macro_insights:
        log.info("[CLUSTER] No macro insights to process")
        return IncrementalClusteringResult()

    cache = cluster_cache or get_cluster_embedding_cache()
    result = IncrementalClusteringResult()

    log.info(
        "[CLUSTER] Incremental assignment of %d macro insights (threshold=%.2f)",
        len(macro_insights),
        similarity_threshold,
    )

    for macro in macro_insights:
        try:
            change = assign_macro_insight_with_change_tracking(
                macro, cache, session, similarity_threshold
            )
            result.add_change(change)

            # Log with change indication
            change_indicator = ""
            if change.is_new_assignment:
                change_indicator = " [NEW]"
            elif change.is_changed:
                change_indicator = f" [CHANGED from {change.old_cluster_name}]"

            log.info(
                "[CLUSTER] '%s' → %s (%.3f)%s%s",
                change.macro_insight_label,
                change.new_cluster_name,
                change.similarity_score,
                " [OTHER]" if change.is_other else "",
                change_indicator,
            )
        except ValueError as e:
            log.warning("[CLUSTER] Skipping macro insight: %s", e)
            continue

    log.info(
        "[CLUSTER] Incremental completed: %d processed, %d new, %d changed, %d unchanged, %d to 'Other'",
        result.total_processed,
        result.total_new_assignments,
        result.total_changed,
        result.total_unchanged,
        result.total_assigned_to_other,
    )

    return result


def persist_incremental_cluster_assignments(
    session: Session,
    result: IncrementalClusteringResult,
    only_changed: bool = False,
) -> Dict[str, Cluster]:
    """
    Persist incremental cluster assignments to the database.

    Args:
        session: SQLAlchemy session
        result: IncrementalClusteringResult from assign_macro_insights_incremental
        only_changed: If True, only persist assignments that actually changed

    Returns:
        Dictionary mapping cluster names to Cluster models
    """
    changes_to_persist = result.changes
    if only_changed:
        changes_to_persist = [c for c in result.changes if c.is_changed]

    if not changes_to_persist:
        log.info("[CLUSTER] No changes to persist")
        return {}

    # Get or create all required clusters
    cluster_map: Dict[str, Cluster] = {}
    cluster_names_needed = set(c.new_cluster_name for c in changes_to_persist)
    for cluster_name in cluster_names_needed:
        cluster_map[cluster_name] = get_or_create_cluster(session, cluster_name)

    # Update macro insight cluster assignments
    for change in changes_to_persist:
        cluster = cluster_map[change.new_cluster_name]
        session.query(MacroInsight).filter(
            MacroInsight.id == change.macro_insight_id
        ).update(
            {MacroInsight.cluster_id: cluster.id},
            synchronize_session="fetch",
        )

    session.commit()

    log.info(
        "[CLUSTER] Persisted %d assignments (%d changed) to %d clusters",
        len(changes_to_persist),
        sum(1 for c in changes_to_persist if c.is_changed),
        len(cluster_map),
    )

    return cluster_map


def update_cluster_assignments(
    session: Session,
    macro_insight_ids: List[int],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> IncrementalClusteringResult:
    """
    Re-evaluate and update cluster assignments for specific MacroInsights.

    Use this when MacroInsight centroids have been updated (e.g., after new
    UnitInsights were injected). Only assignments that actually change will
    be persisted.

    Args:
        session: SQLAlchemy database session
        macro_insight_ids: List of MacroInsight IDs to re-evaluate
        similarity_threshold: Minimum similarity for cluster assignment

    Returns:
        IncrementalClusteringResult with change tracking information
    """
    if not macro_insight_ids:
        log.info("[CLUSTER] No MacroInsight IDs provided for update")
        return IncrementalClusteringResult()

    # Fetch the specified MacroInsights (regardless of current cluster assignment)
    macro_insights = session.query(MacroInsight).filter(
        MacroInsight.id.in_(macro_insight_ids)
    ).all()

    if not macro_insights:
        log.info("[CLUSTER] No MacroInsights found for provided IDs")
        return IncrementalClusteringResult()

    log.info(
        "[CLUSTER] Re-evaluating cluster assignments for %d MacroInsights",
        len(macro_insights),
    )

    # Perform incremental assignment with change tracking
    result = assign_macro_insights_incremental(
        session, macro_insights, similarity_threshold
    )

    # Only persist changes (not unchanged assignments)
    persist_incremental_cluster_assignments(session, result, only_changed=True)

    return result


def assign_and_persist_macro_insights_incremental(
    session: Session,
    macro_insight_ids: Optional[List[int]] = None,
    include_assigned: bool = False,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> IncrementalClusteringResult:
    """
    End-to-end incremental macro insight clustering and persistence.

    This is the incremental version of assign_and_persist_macro_insights that
    tracks changes and can optionally re-evaluate already-assigned MacroInsights.

    Args:
        session: SQLAlchemy database session
        macro_insight_ids: Optional list of specific IDs to process
                          (default: all unassigned, or all if include_assigned=True)
        include_assigned: If True, also process already-assigned MacroInsights
        similarity_threshold: Minimum similarity for cluster assignment

    Returns:
        IncrementalClusteringResult with change tracking information
    """
    # Build query
    query = session.query(MacroInsight)

    if not include_assigned:
        query = query.filter(MacroInsight.cluster_id.is_(None))

    if macro_insight_ids is not None:
        query = query.filter(MacroInsight.id.in_(macro_insight_ids))

    macro_insights = query.all()

    if not macro_insights:
        log.info("[CLUSTER] No MacroInsights found to process")
        return IncrementalClusteringResult()

    # Perform incremental assignment
    result = assign_macro_insights_incremental(
        session, macro_insights, similarity_threshold
    )

    # Persist all assignments (including unchanged, for consistency)
    persist_incremental_cluster_assignments(session, result, only_changed=False)

    # Log summary
    log.info(
        "[CLUSTER] Summary: %d processed, %d new assignments, %d changed, %d unchanged",
        result.total_processed,
        result.total_new_assignments,
        result.total_changed - result.total_new_assignments,  # Changed excludes new
        result.total_unchanged,
    )

    return result


# ---------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------
def get_cluster_statistics(session: Session) -> Dict[str, int]:
    """
    Get count of macro insights per cluster.

    Args:
        session: SQLAlchemy session

    Returns:
        Dictionary mapping cluster names to macro insight counts
    """
    from sqlalchemy import func

    stats = (
        session.query(Cluster.name, func.count(MacroInsight.id))
        .outerjoin(MacroInsight, MacroInsight.cluster_id == Cluster.id)
        .group_by(Cluster.name)
        .all()
    )

    return {name: count for name, count in stats}


def reassign_other_cluster(
    session: Session,
    new_threshold: float,
) -> ClusteringResult:
    """
    Re-evaluate macro insights currently in "Other / Emerging" cluster.

    Useful when lowering the threshold to capture more assignments.

    Args:
        session: SQLAlchemy session
        new_threshold: New (lower) similarity threshold

    Returns:
        ClusteringResult with any new assignments
    """
    other_cluster = session.query(Cluster).filter(
        Cluster.name == OTHER_CLUSTER_NAME
    ).first()

    if other_cluster is None:
        log.info("[CLUSTER] No 'Other / Emerging' cluster found")
        return ClusteringResult()

    # Get macro insights in Other cluster
    macro_insights = session.query(MacroInsight).filter(
        MacroInsight.cluster_id == other_cluster.id
    ).all()

    if not macro_insights:
        log.info("[CLUSTER] No macro insights in 'Other / Emerging'")
        return ClusteringResult()

    # Clear current assignments to re-evaluate
    for macro in macro_insights:
        macro.cluster_id = None
    session.flush()

    # Re-assign with new threshold
    return assign_and_persist_macro_insights(
        session,
        macro_insight_ids=[m.id for m in macro_insights],
        similarity_threshold=new_threshold,
    )


# ---------------------------------------------------------------------
# Orphan UnitInsight clustering
# ---------------------------------------------------------------------
@dataclass
class UnitInsightAssignment:
    """Result of assigning a unit insight to a strategic cluster."""
    unit_insight_id: int
    unit_insight_name: str
    cluster_name: str
    similarity_score: float
    is_other: bool = False


@dataclass
class UnitInsightClusteringResult:
    """Aggregate result of a unit insight clustering operation."""
    assignments: List[UnitInsightAssignment] = field(default_factory=list)
    clusters_used: Dict[str, int] = field(default_factory=dict)
    total_processed: int = 0
    total_assigned_to_other: int = 0

    def add_assignment(self, assignment: UnitInsightAssignment) -> None:
        """Add an assignment and update counters."""
        self.assignments.append(assignment)
        self.total_processed += 1

        if assignment.is_other:
            self.total_assigned_to_other += 1

        if assignment.cluster_name not in self.clusters_used:
            self.clusters_used[assignment.cluster_name] = 0
        self.clusters_used[assignment.cluster_name] += 1


def assign_unit_insight(
    unit_insight: UnitInsight,
    cluster_cache: ClusterEmbeddingCache,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> UnitInsightAssignment:
    """
    Assign a single orphan unit insight to a strategic cluster.

    Args:
        unit_insight: UnitInsight database model
        cluster_cache: Cache containing cluster embeddings
        similarity_threshold: Minimum similarity for cluster assignment

    Returns:
        UnitInsightAssignment with assignment details

    Raises:
        ValueError: If unit insight has no embedding
    """
    if unit_insight.embedding is None:
        raise ValueError(f"UnitInsight {unit_insight.id} has no embedding")

    # Get and normalize the unit insight embedding
    unit_vec = np.array(unit_insight.embedding, dtype=np.float32)
    unit_vec = normalize_vector(unit_vec)

    # Find best cluster
    cluster_name, score, is_other = find_best_cluster(
        unit_vec,
        cluster_cache.embeddings,
        similarity_threshold,
    )

    return UnitInsightAssignment(
        unit_insight_id=unit_insight.id,
        unit_insight_name=unit_insight.name,
        cluster_name=cluster_name,
        similarity_score=score,
        is_other=is_other,
    )


def assign_orphan_unit_insights_to_clusters(
    unit_insights: Sequence[UnitInsight],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    cluster_cache: Optional[ClusterEmbeddingCache] = None,
) -> UnitInsightClusteringResult:
    """
    Assign orphan unit insights (without macro insight) to strategic clusters.

    This function assigns unit insights directly to strategic clusters when they
    couldn't be grouped into macro insights (isolated insights).

    Args:
        unit_insights: Sequence of UnitInsight database models
        similarity_threshold: Minimum cosine similarity for cluster assignment
        cluster_cache: Optional custom cache (uses singleton by default)

    Returns:
        UnitInsightClusteringResult with all assignments and statistics
    """
    if not unit_insights:
        log.info("[CLUSTER] No orphan unit insights to process")
        return UnitInsightClusteringResult()

    cache = cluster_cache or get_cluster_embedding_cache()
    result = UnitInsightClusteringResult()

    log.info(
        "[CLUSTER] Assigning %d orphan unit insights (threshold=%.2f)",
        len(unit_insights),
        similarity_threshold,
    )

    for unit_insight in unit_insights:
        try:
            assignment = assign_unit_insight(unit_insight, cache, similarity_threshold)
            result.add_assignment(assignment)

            log.info(
                "[CLUSTER] '%s' → %s (%.3f)%s",
                assignment.unit_insight_name[:50],
                assignment.cluster_name,
                assignment.similarity_score,
                " [OTHER]" if assignment.is_other else "",
            )
        except ValueError as e:
            log.warning("[CLUSTER] Skipping unit insight: %s", e)
            continue

    log.info(
        "[CLUSTER] Completed: %d orphan unit insights processed, %d to 'Other'",
        result.total_processed,
        result.total_assigned_to_other,
    )

    return result


def persist_unit_insight_cluster_assignments(
    session: Session,
    result: UnitInsightClusteringResult,
) -> Dict[str, Cluster]:
    """
    Persist unit insight cluster assignments to the database.

    Updates UnitInsight.cluster_id for all assignments.

    Args:
        session: SQLAlchemy session
        result: UnitInsightClusteringResult from assign_orphan_unit_insights_to_clusters

    Returns:
        Dictionary mapping cluster names to Cluster models
    """
    if not result.assignments:
        return {}

    # Get or create all required clusters
    cluster_map: Dict[str, Cluster] = {}
    for cluster_name in result.clusters_used.keys():
        cluster_map[cluster_name] = get_or_create_cluster(session, cluster_name)

    # Update unit insight cluster assignments
    for assignment in result.assignments:
        cluster = cluster_map[assignment.cluster_name]
        session.query(UnitInsight).filter(
            UnitInsight.id == assignment.unit_insight_id
        ).update(
            {UnitInsight.cluster_id: cluster.id},
            synchronize_session="fetch",
        )

    session.commit()

    log.info(
        "[CLUSTER] Persisted %d unit insight assignments to %d clusters",
        len(result.assignments),
        len(cluster_map),
    )

    return cluster_map


def assign_and_persist_orphan_unit_insights(
    session: Session,
    unit_insight_ids: Optional[List[int]] = None,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> UnitInsightClusteringResult:
    """
    End-to-end orphan unit insight clustering and persistence.

    This is the main entry point for clustering orphan unit insights
    (those without a macro insight) directly to strategic clusters.

    Steps:
    1. Fetch orphan UnitInsights (no macro_insight_id) from database
    2. Compute cluster assignments using embedding similarity
    3. Create/update Cluster records
    4. Update UnitInsight.cluster_id links

    Args:
        session: SQLAlchemy database session
        unit_insight_ids: Optional list of specific IDs to process
                         (default: all orphan unit insights)
        similarity_threshold: Minimum similarity for cluster assignment

    Returns:
        UnitInsightClusteringResult with assignments and statistics
    """
    # Build query for orphan unit insights (no macro insight assigned)
    query = session.query(UnitInsight).filter(
        UnitInsight.macro_insight_id.is_(None)
    )

    if unit_insight_ids is not None:
        query = query.filter(UnitInsight.id.in_(unit_insight_ids))

    unit_insights = query.all()

    if not unit_insights:
        log.info("[CLUSTER] No orphan UnitInsights found")
        return UnitInsightClusteringResult()

    # Perform assignment
    result = assign_orphan_unit_insights_to_clusters(unit_insights, similarity_threshold)

    # Persist to database
    persist_unit_insight_cluster_assignments(session, result)

    return result
