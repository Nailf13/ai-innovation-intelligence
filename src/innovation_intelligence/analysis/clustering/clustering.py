"""
Strategic cluster assignment for macro insights.

This module handles:
- Embedding-based assignment of macro insights to strategic clusters
- Lazy loading and caching of cluster embeddings
- Database persistence of cluster assignments
- Support for dynamic "Other / Emerging" cluster

The clustering hierarchy:
    UnitInsight → MacroInsight → Cluster (strategic)

Strategic clusters are predefined high-level categories. MacroInsights that
don't fit any predefined cluster (below similarity threshold) are assigned
to "Other / Emerging".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

import numpy as np
from sqlalchemy.orm import Session

from innovation_intelligence.analysis.insights.embedder import embed_text
from innovation_intelligence.db.models import Cluster, MacroInsight
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
DEFAULT_SIMILARITY_THRESHOLD = 0.75
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
        "diabetes obesity nutrition metabolic insulin blood sugar weight management "
        "cardiovascular lipid cholesterol dietary interventions"
    ),
    "Mental, Emotional & Cognitive Well-Being": (
        "mental health cognition stress focus mood psychology depression anxiety "
        "brain health cognitive function emotional resilience mindfulness"
    ),
    "Women's Health & Hormonal Balance": (
        "female health hormones menstrual menopause fertility pregnancy reproductive "
        "estrogen progesterone perimenopause endocrine"
    ),
    "Healthy Aging, Longevity & Vitality": (
        "longevity aging lifespan vitality healthspan anti-aging cellular health "
        "senescence regeneration telomeres NAD sirtuins"
    ),
    "Immunity & Gut Health": (
        "immunity gut microbiome inflammation immune system probiotics prebiotics "
        "intestinal barrier autoimmune infection resistance"
    ),
    "Physical Resilience & Performance": (
        "exercise fitness strength recovery performance endurance muscle training "
        "sports rehabilitation physical activity mobility"
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
