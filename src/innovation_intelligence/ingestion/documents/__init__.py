# src/innovation_intelligence/ingestion/documents/__init__.py
"""
Document ingestion module.

Provides:
- DocumentService: Service for uploading and processing documents
- Discovery: Find documents in folders
- Text extraction: PDF to text conversion
- Pipeline: Batch document ingestion
"""
from innovation_intelligence.ingestion.documents.document_service import (
    DocumentService,
    DocumentUploadResult,
    upload_document,
)
from innovation_intelligence.ingestion.documents.discovery import list_pdf_files
from innovation_intelligence.ingestion.documents.text_extraction import (
    extract_text_from_pdf,
    pdf_to_text_file,
)
from innovation_intelligence.ingestion.documents.pipeline import (
    ingest_documents,
    get_documents_dirs,
)

__all__ = [
    # Service (API-ready)
    "DocumentService",
    "DocumentUploadResult",
    "upload_document",
    # Discovery
    "list_pdf_files",
    # Text extraction
    "extract_text_from_pdf",
    "pdf_to_text_file",
    # Pipeline
    "ingest_documents",
    "get_documents_dirs",
]
