# src/innovation_intelligence/chunking/__init__.py
"""
Chunking module for semantic text segmentation.

Provides specialized chunkers for:
- Podcast transcripts (speaker-turn based)
- Documents (structure-aware)
"""
from innovation_intelligence.chunking.podcast_chunker import (
    PodcastChunker,
    PodcastChunk,
    chunk_podcast_transcript,
)
from innovation_intelligence.chunking.document_chunker import (
    DocumentChunker,
    DocumentChunk,
    chunk_document,
)

__all__ = [
    "PodcastChunker",
    "PodcastChunk",
    "chunk_podcast_transcript",
    "DocumentChunker",
    "DocumentChunk",
    "chunk_document",
]
