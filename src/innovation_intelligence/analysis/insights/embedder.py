# src/innovation_intelligence/analysis/embeddings/embedder.py
from __future__ import annotations

from functools import lru_cache
from typing import List
import os

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
    Load BGE-M3 once, preferring local cache to avoid slow HF downloads.

    Strategy:
    1. Try local_files_only=True (zero network calls, instant from disk)
    2. Fall back to network-enabled load (first-ever run or missing cache)
    """
    model_name = settings.embeddings.model_name
    hf_token = os.getenv("HF_TOKEN")

    log.info("[EMBED] Loading embedding model: %s", model_name)

    # Fast path: load from local HF cache without any network calls
    try:
        model = SentenceTransformer(
            model_name,
            device=settings.embeddings.device,
            token=hf_token,
            local_files_only=True,
        )
        log.info("[EMBED] Model loaded from local cache (offline mode)")
        return model
    except Exception as offline_err:
        log.warning(
            "[EMBED] Offline load failed (%s). Falling back to network download. "
            "This may take a long time for large models.",
            offline_err,
        )

    # Slow path: download from HuggingFace (first run or cache missing)
    try:
        model = SentenceTransformer(
            model_name,
            device=settings.embeddings.device,
            token=hf_token,
        )
        log.info("[EMBED] Model loaded via network download")
        return model
    except Exception as e:
        log.error("[EMBED] Failed to load model %s: %s", model_name, e)
        raise


def clear_model_cache():
    """Clear the cached embedding model (useful for reloading with new token)."""
    _load_model.cache_clear()
    log.info("[EMBED] Model cache cleared")


def preload_model() -> None:
    """
    Eagerly load the embedding model into memory.

    Call from FastAPI lifespan startup so the first API request
    doesn't block on model loading.
    """
    log.info("[EMBED] Preloading embedding model at startup...")
    _load_model()
    log.info("[EMBED] Embedding model preload complete")


def download_model(force: bool = False) -> None:
    """
    Explicitly download or update the embedding model from HuggingFace.

    Use this when you intentionally want to pull the latest model version.
    Resumable and atomic (partial downloads won't corrupt the cache).

    Args:
        force: If True, re-download even if already cached.
    """
    from huggingface_hub import snapshot_download

    model_name = settings.embeddings.model_name
    hf_token = os.getenv("HF_TOKEN")

    log.info("[EMBED] Downloading model: %s (force=%s)", model_name, force)
    local_dir = snapshot_download(
        repo_id=model_name,
        token=hf_token,
        force_download=force,
    )
    log.info("[EMBED] Model downloaded to: %s", local_dir)

    _load_model.cache_clear()
    log.info("[EMBED] In-process model cache cleared — next call will load fresh weights")


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
