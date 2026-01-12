"""
Document service for handling document uploads and ingestion.

Provides a clean API for:
- Manual document upload with user-provided metadata (title, date)
- Upload to Google Cloud Storage (GCS) for cloud storage
- Text extraction from PDFs
- Vector indexing

Designed for API integration while maintaining compatibility
with batch folder discovery.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Union
import shutil

from sqlalchemy.orm import Session

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.db.session import SessionLocal
from innovation_intelligence.db.models import Document
from innovation_intelligence.db.repositories.document_repository import DocumentRepository

from innovation_intelligence.ingestion.documents.text_extraction import (
    extract_text_from_pdf,
    pdf_to_text_file,
)

log = get_logger(__name__)


# =============================================================================
# Constants & Enums
# =============================================================================

class FileFormat(str, Enum):
    """Supported file formats for document ingestion."""
    PDF = "pdf"
    TXT = "txt"
    MD = "md"
    DOCX = "docx"
    UNKNOWN = "unknown"

    @classmethod
    def from_extension(cls, ext: str) -> "FileFormat":
        """Get FileFormat from file extension (with or without dot)."""
        ext_clean = ext.lower().lstrip(".")
        try:
            return cls(ext_clean)
        except ValueError:
            return cls.UNKNOWN

    @property
    def extension(self) -> str:
        """Return the file extension with dot."""
        return f".{self.value}" if self != FileFormat.UNKNOWN else ".bin"


# Formats that support text extraction
EXTRACTABLE_FORMATS = {FileFormat.PDF}

# Default chunk size for file operations (8KB)
FILE_CHUNK_SIZE = 8192


# =============================================================================
# Result Classes
# =============================================================================

@dataclass
class DocumentUploadResult:
    """Result of a document upload operation (GCS-first mode)."""
    success: bool
    document_id: Optional[int] = None
    title: str = ""
    gcs_uri: Optional[str] = None
    gcs_transcript_uri: Optional[str] = None
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "document_id": self.document_id,
            "title": self.title,
            "gcs_uri": self.gcs_uri,
            "gcs_transcript_uri": self.gcs_transcript_uri,
            "error": self.error,
            "warnings": self.warnings,
        }


# =============================================================================
# Document Service
# =============================================================================

class DocumentService:
    """
    Service for document upload and ingestion with GCS integration.

    Provides a production-ready API for:
    - Uploading documents (file path or file-like object)
    - Upload to Google Cloud Storage (GCS)
    - Text extraction
    - Database persistence

    Key concepts:
    - `file_format`: The actual file format (pdf, txt, docx) - determines how to process
    - `source_type`: Business category (report, whitepaper, article) - metadata only

    Example usage (API integration):
        ```python
        service = DocumentService(upload_to_gcs=True)
        result = service.upload_document(
            file=uploaded_file,
            title="Q4 2024 Health Trends Report",
            document_date=datetime(2024, 12, 15),
            file_format="pdf",
            source_type="report",
        )
        if result.success:
            print(f"Document uploaded: {result.document_id}")
            print(f"GCS URI: {result.gcs_uri}")
        ```

    Example usage (CLI/batch):
        ```python
        service = DocumentService()
        result = service.upload_document(
            file_path=Path("/path/to/report.pdf"),
            title="Health Trends Report",
            document_date=datetime(2024, 12, 15),
        )
        ```
    """

    def __init__(
        self,
        session: Optional[Session] = None,
        upload_to_gcs: bool = True,
        store_transcript_in_gcs: bool = True,
    ):
        """
        Initialize document service.

        Args:
            session: SQLAlchemy session (creates one if not provided)
            upload_to_gcs: Whether to upload documents to GCS (default: True)
            store_transcript_in_gcs: Whether to store transcripts in GCS (default: True)
        """
        self._session = session
        self._owns_session = session is None
        self.upload_to_gcs = upload_to_gcs
        self.store_transcript_in_gcs = store_transcript_in_gcs
        self._gcs_service = None

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = SessionLocal()
        return self._session

    @property
    def gcs_service(self):
        """Lazily create GCS service."""
        if self._gcs_service is None:
            from innovation_intelligence.ingestion.gcs_service import GCSStorageService
            self._gcs_service = GCSStorageService()
        return self._gcs_service

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._owns_session and self._session is not None:
            self._session.close()

    def _get_storage_dirs(self) -> tuple[Path, Path]:
        """Get storage directories for raw files and transcripts."""
        data_root = settings.paths.data_root
        raw_dir = (data_root / "documents" / "raw").resolve()
        transcripts_dir = (data_root / "documents" / "transcripts").resolve()
        raw_dir.mkdir(parents=True, exist_ok=True)
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        return raw_dir, transcripts_dir

    def _resolve_file_format(
        self,
        file_format: Optional[str],
        source_type: Optional[str],
        file_path: Optional[Path],
        filename: Optional[str],
    ) -> FileFormat:
        """
        Resolve the actual file format from available information.

        Priority:
        1. Explicit file_format parameter
        2. Extension from file_path
        3. Extension from filename
        4. source_type if it's a known format (backward compatibility)
        5. Default to UNKNOWN
        """
        # 1. Explicit file_format
        if file_format:
            fmt = FileFormat.from_extension(file_format)
            if fmt != FileFormat.UNKNOWN:
                return fmt

        # 2. From file_path extension
        if file_path:
            fmt = FileFormat.from_extension(file_path.suffix)
            if fmt != FileFormat.UNKNOWN:
                return fmt

        # 3. From filename extension
        if filename:
            fmt = FileFormat.from_extension(Path(filename).suffix)
            if fmt != FileFormat.UNKNOWN:
                return fmt

        # 4. Backward compatibility: source_type might be a format
        if source_type:
            fmt = FileFormat.from_extension(source_type)
            if fmt != FileFormat.UNKNOWN:
                return fmt

        # 5. Default
        return FileFormat.UNKNOWN

    def _generate_document_id(self, title: str) -> str:
        """Generate a unique document ID from title."""
        # Clean title for ID
        clean_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        clean_title = clean_title.strip().replace(" ", "_")[:50]

        # Add timestamp for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        return f"{clean_title}_{timestamp}"

    def _generate_filename(self, title: str, extension: str) -> str:
        """Generate a unique filename from title."""
        # Clean title for filename
        clean_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        clean_title = clean_title.strip().replace(" ", "_")[:100]

        # Add timestamp for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Ensure extension starts with dot
        if not extension.startswith("."):
            extension = f".{extension}"

        return f"{clean_title}_{timestamp}{extension}"

    def _save_uploaded_file(
        self,
        file: BinaryIO,
        dest_path: Path,
    ) -> None:
        """Save uploaded file to destination path."""
        with open(dest_path, "wb") as f:
            while chunk := file.read(FILE_CHUNK_SIZE):
                f.write(chunk)

    def _extract_text(
        self,
        source_path: Path,
        file_format: FileFormat,
        transcripts_dir: Path,
    ) -> Optional[Path]:
        """
        Extract text from document if supported.

        Returns:
            Path to transcript file, or None if extraction not supported/failed
        """
        if file_format not in EXTRACTABLE_FORMATS:
            return None

        transcript_name = source_path.stem + ".txt"
        transcript_path = transcripts_dir / transcript_name

        try:
            if file_format == FileFormat.PDF:
                log.info(f"[DOC] Extracting text from PDF: {source_path}")
                pdf_to_text_file(source_path, transcript_path)
                log.info(f"[DOC] Text extracted to: {transcript_path}")
                return transcript_path
        except Exception as e:
            log.error(f"[DOC] Text extraction failed: {e}")
            # Don't fail the upload, just skip extraction
            return None

        return None

    def _upload_to_gcs(
        self,
        local_path: Path,
        document_id: str,
    ) -> Optional[str]:
        """Upload document to GCS and return the GCS URI."""
        try:
            gcs_uri = self.gcs_service.upload_document(local_path, document_id)
            log.info(f"[DOC] Uploaded to GCS: {gcs_uri}")
            return gcs_uri
        except Exception as e:
            log.error(f"[DOC] GCS upload failed: {e}")
            return None

    def _store_transcript_in_gcs(
        self,
        transcript_path: Path,
        document_id: str,
    ) -> Optional[str]:
        """Store transcript text in GCS and return the GCS URI."""
        try:
            # Read the transcript text
            text = transcript_path.read_text(encoding="utf-8")

            # Create transcript data structure
            transcript_data = {
                "text": text,
                "segments": [{"text": text, "start": 0, "end": 0}],
                "_metadata": {
                    "document_id": document_id,
                    "source_file": transcript_path.name,
                    "extraction_method": "pdf_to_text",
                },
            }

            # Store in GCS
            gcs_uri = self.gcs_service.store_transcript(
                data=transcript_data,
                content_id=document_id,
                content_type="document",
            )
            log.info(f"[DOC] Transcript stored in GCS: {gcs_uri}")
            return gcs_uri
        except Exception as e:
            log.error(f"[DOC] GCS transcript storage failed: {e}")
            return None

    def _extract_text_to_gcs(
        self,
        source_path: Path,
        file_format: FileFormat,
        document_id: str,
    ) -> Optional[str]:
        """
        Extract text from document and upload directly to GCS (GCS-first mode).

        Extracts text with page information preserved for accurate chunking.

        Args:
            source_path: Temporary path to document file
            file_format: Document format
            document_id: Document ID for GCS naming

        Returns:
            GCS URI of transcript, or None if extraction failed
        """
        if file_format not in EXTRACTABLE_FORMATS:
            return None

        try:
            # Extract text with page information
            if file_format == FileFormat.PDF:
                log.info(f"[DOC] Extracting text from PDF with page info: {source_path}")
                from innovation_intelligence.ingestion.documents.text_extraction import extract_text_from_pdf_with_pages

                transcript_data = extract_text_from_pdf_with_pages(source_path)
                transcript_data["_metadata"]["document_id"] = document_id
                log.info(f"[DOC] Extracted {transcript_data['_metadata']['total_pages']} pages")
            else:
                log.warning(f"[DOC] Unsupported format for extraction: {file_format}")
                return None

            # Store transcript data in GCS
            gcs_transcript_uri = self.gcs_service.store_transcript(
                data=transcript_data,
                content_id=document_id,
                content_type="document",
            )
            log.info(f"[DOC] Transcript stored in GCS: {gcs_transcript_uri}")
            return gcs_transcript_uri

        except Exception as e:
            log.error(f"[DOC] Text extraction to GCS failed: {e}")
            return None

    def upload_document(
        self,
        *,
        file: Optional[BinaryIO] = None,
        file_path: Optional[Union[Path, str]] = None,
        filename: Optional[str] = None,
        title: str,
        document_date: Optional[datetime] = None,
        source_type: Optional[str] = None,
        file_format: Optional[str] = None,
        extract_text: bool = True,
    ) -> DocumentUploadResult:
        """
        Upload a document for ingestion (GCS-first mode).

        Accepts either a file-like object (for API uploads) or a file path
        (for CLI/batch processing). The document is:
        1. Uploaded directly to GCS (uses temp files only)
        2. Text extracted (if PDF and extract_text=True)
        3. Transcript stored in GCS
        4. Saved to database with GCS URIs only

        Args:
            file: File-like object (e.g., from FastAPI UploadFile.file)
            file_path: Path to existing file on disk
            filename: Original filename (used for format detection when file is BinaryIO)
            title: Document title (required)
            document_date: Document date (for proper indexing)
            source_type: Business category (report, whitepaper, article, etc.)
            file_format: Explicit file format (pdf, txt, docx). Auto-detected if not provided.
            extract_text: Whether to extract text from supported formats (default: True)

        Returns:
            DocumentUploadResult with success status and GCS URIs
        """
        warnings: list[str] = []
        gcs_uri: Optional[str] = None
        gcs_transcript_uri: Optional[str] = None
        tmp_path: Optional[Path] = None

        # Validate input
        if file is None and file_path is None:
            return DocumentUploadResult(
                success=False,
                title=title,
                error="Either 'file' or 'file_path' must be provided",
            )

        try:
            repo = DocumentRepository(self.session)

            # Convert file_path to Path if string
            resolved_file_path: Optional[Path] = None
            if file_path is not None:
                resolved_file_path = Path(file_path).resolve()

            # Resolve file format
            fmt = self._resolve_file_format(
                file_format=file_format,
                source_type=source_type,
                file_path=resolved_file_path,
                filename=filename,
            )

            if fmt == FileFormat.UNKNOWN:
                warnings.append("Could not detect file format, using .bin extension")
                log.warning(f"[DOC] Unknown file format for: {title}")

            # Generate document ID for GCS
            document_id = self._generate_document_id(title)

            # Create temp file for processing
            import tempfile
            tmp_file = tempfile.NamedTemporaryFile(suffix=fmt.extension, delete=False)
            tmp_path = Path(tmp_file.name)
            tmp_file.close()

            try:
                # Save file to temp location
                if resolved_file_path is not None:
                    # Copy from existing file
                    if not resolved_file_path.exists():
                        return DocumentUploadResult(
                            success=False,
                            title=title,
                            error=f"File not found: {resolved_file_path}",
                        )
                    shutil.copy2(resolved_file_path, tmp_path)
                    log.info(f"[DOC] Copied file to temp: {tmp_path}")
                else:
                    # Save from file-like object
                    self._save_uploaded_file(file, tmp_path)
                    log.info(f"[DOC] Saved uploaded file to temp: {tmp_path}")

                # Upload to GCS
                gcs_uri = self._upload_to_gcs(tmp_path, document_id)
                if gcs_uri is None:
                    return DocumentUploadResult(
                        success=False,
                        title=title,
                        error="GCS upload failed",
                        warnings=warnings,
                    )
                log.info(f"[DOC] Uploaded to GCS: {gcs_uri}")

                # Check if document already exists (by GCS URI)
                existing = repo.get_by_gcs_uri(gcs_uri)
                if existing:
                    log.warning(f"[DOC] Document already exists: {existing.id}")
                    return DocumentUploadResult(
                        success=True,
                        document_id=existing.id,
                        title=existing.title,
                        gcs_uri=gcs_uri,
                        gcs_transcript_uri=existing.gcs_transcript_uri,
                        error="Document already exists (returning existing)",
                        warnings=warnings,
                    )

                # Extract text if enabled and format supports it
                if extract_text and fmt in EXTRACTABLE_FORMATS:
                    gcs_transcript_uri = self._extract_text_to_gcs(tmp_path, fmt, document_id)
                    if gcs_transcript_uri is None:
                        warnings.append("Text extraction failed, document saved without transcript")
                    else:
                        log.info(f"[DOC] Transcript uploaded to GCS: {gcs_transcript_uri}")

                # Determine source_type for DB (use provided or default to format)
                db_source_type = source_type if source_type else fmt.value

                # Create database record with GCS URIs only
                doc = repo.create(
                    title=title,
                    gcs_document_uri=gcs_uri,
                    source_type=db_source_type,
                    gcs_transcript_uri=gcs_transcript_uri,
                    document_date=document_date,
                )

                log.info(f"[DOC] Created document: id={doc.id}, title={title}, format={fmt.value}")

                return DocumentUploadResult(
                    success=True,
                    document_id=doc.id,
                    title=doc.title,
                    gcs_uri=gcs_uri,
                    gcs_transcript_uri=gcs_transcript_uri,
                    warnings=warnings,
                )

            finally:
                # Clean up temp file
                if tmp_path and tmp_path.exists():
                    tmp_path.unlink()
                    log.debug(f"[DOC] Cleaned up temp file: {tmp_path}")

        except Exception as e:
            log.exception(f"[DOC] Upload failed: {e}")
            return DocumentUploadResult(
                success=False,
                title=title,
                error=str(e),
            )

    def upload_document_to_gcs_only(
        self,
        file_path: Union[Path, str],
        document_id: Optional[str] = None,
        extract_text: bool = True,
    ) -> DocumentUploadResult:
        """
        Upload a document directly to GCS without database persistence.

        Useful for quick uploads where database tracking is not needed.

        Args:
            file_path: Path to the document file
            document_id: Optional custom document ID (auto-generated if not provided)
            extract_text: Whether to extract and upload transcript

        Returns:
            DocumentUploadResult with GCS URIs
        """
        file_path = Path(file_path)

        if not file_path.exists():
            return DocumentUploadResult(
                success=False,
                title=file_path.stem,
                error=f"File not found: {file_path}",
            )

        # Generate document ID if not provided
        if document_id is None:
            document_id = self._generate_document_id(file_path.stem)

        warnings: list[str] = []
        gcs_transcript_uri: Optional[str] = None

        try:
            # Upload document to GCS
            gcs_uri = self._upload_to_gcs(file_path, document_id)
            if gcs_uri is None:
                return DocumentUploadResult(
                    success=False,
                    title=file_path.stem,
                    error="GCS upload failed",
                )

            # Extract and upload transcript if enabled
            if extract_text:
                fmt = FileFormat.from_extension(file_path.suffix)
                if fmt in EXTRACTABLE_FORMATS:
                    # Extract to temp location
                    _, transcripts_dir = self._get_storage_dirs()
                    transcript_path = self._extract_text(file_path, fmt, transcripts_dir)

                    if transcript_path:
                        gcs_transcript_uri = self._store_transcript_in_gcs(
                            transcript_path, document_id
                        )
                        if gcs_transcript_uri is None:
                            warnings.append("Transcript GCS upload failed")
                    else:
                        warnings.append("Text extraction failed")

            return DocumentUploadResult(
                success=True,
                title=file_path.stem,
                file_path=str(file_path),
                gcs_uri=gcs_uri,
                gcs_transcript_uri=gcs_transcript_uri,
                warnings=warnings,
            )

        except Exception as e:
            log.exception(f"[DOC] GCS-only upload failed: {e}")
            return DocumentUploadResult(
                success=False,
                title=file_path.stem,
                error=str(e),
            )

    def get_document(self, document_id: int) -> Optional[Document]:
        """Get document by ID."""
        repo = DocumentRepository(self.session)
        return repo.get(document_id)

    def list_documents(
        self,
        source_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Document]:
        """
        List documents with optional filtering.

        Args:
            source_type: Filter by source type
            limit: Maximum number of documents to return
        """
        repo = DocumentRepository(self.session)
        return repo.list_all(source_type=source_type, limit=limit)

    def list_documents_in_gcs(self, max_results: int = 100) -> list[str]:
        """List all documents stored in GCS."""
        return self.gcs_service.list_documents(max_results=max_results)

    def list_document_transcripts_in_gcs(self, max_results: int = 100) -> list[str]:
        """List all document transcripts stored in GCS."""
        return self.gcs_service.list_document_transcripts(max_results=max_results)

    def get_transcript_from_gcs(self, document_id: str) -> Optional[Dict[str, Any]]:
        """
        Get transcript data from GCS.

        Args:
            document_id: Document ID used when storing the transcript

        Returns:
            Transcript data dictionary or None if not found
        """
        try:
            gcs_uri = self.gcs_service.get_transcript_gcs_uri(
                document_id, content_type="document"
            )
            return self.gcs_service.get_transcript(gcs_uri)
        except Exception as e:
            log.error(f"[DOC] Failed to get transcript from GCS: {e}")
            return None


# =============================================================================
# Convenience Functions
# =============================================================================

def upload_document(
    *,
    file: Optional[BinaryIO] = None,
    file_path: Optional[Union[Path, str]] = None,
    filename: Optional[str] = None,
    title: str,
    document_date: Optional[datetime] = None,
    source_type: Optional[str] = None,
    file_format: Optional[str] = None,
    upload_to_gcs: bool = True,
) -> DocumentUploadResult:
    """
    Upload a document (convenience function).

    See DocumentService.upload_document for full documentation.
    """
    with DocumentService(upload_to_gcs=upload_to_gcs) as service:
        return service.upload_document(
            file=file,
            file_path=file_path,
            filename=filename,
            title=title,
            document_date=document_date,
            source_type=source_type,
            file_format=file_format,
        )


def upload_document_to_gcs(
    file_path: Union[Path, str],
    document_id: Optional[str] = None,
    extract_text: bool = True,
) -> DocumentUploadResult:
    """
    Upload a document directly to GCS (convenience function).

    Args:
        file_path: Path to the document file
        document_id: Optional custom document ID
        extract_text: Whether to extract and upload transcript

    Returns:
        DocumentUploadResult with GCS URIs
    """
    with DocumentService() as service:
        return service.upload_document_to_gcs_only(
            file_path=file_path,
            document_id=document_id,
            extract_text=extract_text,
        )
