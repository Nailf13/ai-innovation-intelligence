# src/innovation_intelligence/ingestion/__init__.py
"""
Ingestion module for Innovation Intelligence.

Provides services for:
- Google Cloud Storage (GCS) integration
- Podcast ingestion pipeline
- Document processing
- Vector indexing
"""

from innovation_intelligence.ingestion.gcs_service import GCSStorageService

__all__ = [
    "GCSStorageService",
]
