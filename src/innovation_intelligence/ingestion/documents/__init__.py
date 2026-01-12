# src/innovation_intelligence/ingestion/documents/__init__.py
"""
Document ingestion and processing module.

Provides:
- DocumentService: Uploading and processing documents with GCS integration
- Discovery: Finding documents in folders
- Text extraction: PDF to text conversion
- Pipelines: Batch document ingestion (local & GCS)
- Chunking: Structure-aware document chunking
"""

# ---------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.documents.document_service import (
    DocumentService,
    DocumentUploadResult,
    upload_document,
    upload_document_to_gcs,
)

# ---------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.documents.discovery import list_pdf_files

# ---------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.documents.text_extraction import (
    extract_text_from_pdf,
    pdf_to_text_file,
)

# ---------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.documents.pipeline import (
    ingest_documents,
    ingest_documents_from_gcs,
    get_documents_dirs,
)

# ---------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.documents.document_chunker import (
    DocumentChunker,
    DocumentChunk,
    chunk_document,
)

# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
__all__ = [
    # Services
    "DocumentService",
    "DocumentUploadResult",
    "upload_document",
    "upload_document_to_gcs",

    # Discovery
    "list_pdf_files",

    # Text extraction
    "extract_text_from_pdf",
    "pdf_to_text_file",

    # Pipelines
    "ingest_documents",
    "ingest_documents_from_gcs",
    "get_documents_dirs",

    # Chunking
    "DocumentChunker",
    "DocumentChunk",
    "chunk_document",
]