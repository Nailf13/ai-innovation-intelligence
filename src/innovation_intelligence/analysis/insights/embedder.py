# src/innovation_intelligence/analysis/embeddings/embedder.py
from __future__ import annotations

from functools import lru_cache
from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


# ======================================================================
# Model loader (singleton)
# ======================================================================
@lru_cache(maxsize=1)
def _load_model() -> SentenceTransformer:
    """
    Load BGE-M3 once.
    """
    model_name = settings.embeddings.model_name

    log.info("[EMBED] Loading embedding model: %s", model_name)
    model = SentenceTransformer(
        model_name,
        device=settings.embeddings.device,
    )

    return model


# ======================================================================
# Public API
# ======================================================================
def embed_text(text: str) -> List[float]:
    """
    Embed a single text using BGE-M3.

    Guarantees:
    - L2-normalized vector
    - cosine-similarity ready
    - deterministic output
    """
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")

    model = _load_model()

    vec = model.encode(
        text,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )

    return vec.astype(np.float32).tolist()


def embed_texts_batch(texts: List[str]) -> List[List[float]]:
    """
    Embed multiple texts in a single batch (much faster than individual calls).

    Args:
        texts: List of text strings to embed

    Returns:
        List of embedding vectors (L2-normalized)

    Raises:
        ValueError: If texts list is empty or contains empty strings
    """
    if not texts:
        return []

    # Validate inputs
    for i, text in enumerate(texts):
        if not text or not text.strip():
            raise ValueError(f"Cannot embed empty text at index {i}")

    model = _load_model()

    log.info("[EMBED] Batch embedding %d texts", len(texts))

    vecs = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=len(texts) > 10,  # Show progress for larger batches
    )

    return [vec.astype(np.float32).tolist() for vec in vecs]
