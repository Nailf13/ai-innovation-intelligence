"""
Document service for handling document uploads and ingestion.

Provides a clean API for:
- Manual document upload with user-provided metadata (title, date)
- Text extraction from PDFs
- Vector indexing

Designed for API integration while maintaining compatibility
with batch folder discovery.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Optional, Union
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
    """Result of a document upload operation."""
    success: bool
    document_id: Optional[int] = None
    title: str = ""
    file_path: Optional[str] = None
    transcript_path: Optional[str] = None
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "document_id": self.document_id,
            "title": self.title,
            "file_path": self.file_path,
            "transcript_path": self.transcript_path,
            "error": self.error,
            "warnings": self.warnings,
        }


# =============================================================================
# Document Service
# =============================================================================

class DocumentService:
    """
    Service for document upload and ingestion.

    Provides a production-ready API for:
    - Uploading documents (file path or file-like object)
    - Text extraction
    - Database persistence

    Key concepts:
    - `file_format`: The actual file format (pdf, txt, docx) - determines how to process
    - `source_type`: Business category (report, whitepaper, article) - metadata only

    Example usage (API integration):
        ```python
        service = DocumentService()
        result = service.upload_document(
            file=uploaded_file,
            title="Q4 2024 Health Trends Report",
            document_date=datetime(2024, 12, 15),
            file_format="pdf",
            source_type="report",
        )
        if result.success:
            print(f"Document uploaded: {result.document_id}")
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

    def __init__(self, session: Optional[Session] = None):
        """
        Initialize document service.

        Args:
            session: SQLAlchemy session (creates one if not provided)
        """
        self._session = session
        self._owns_session = session is None

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = SessionLocal()
        return self._session

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
        Upload a document for ingestion.

        Accepts either a file-like object (for API uploads) or a file path
        (for CLI/batch processing). The document is:
        1. Stored in the raw documents directory
        2. Text extracted (if PDF and extract_text=True)
        3. Saved to database with metadata

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
            DocumentUploadResult with success status and document info
        """
        warnings: list[str] = []

        # Validate input
        if file is None and file_path is None:
            return DocumentUploadResult(
                success=False,
                title=title,
                error="Either 'file' or 'file_path' must be provided",
            )

        try:
            raw_dir, transcripts_dir = self._get_storage_dirs()
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
                warnings.append("Could not detect file format, saved with .bin extension")
                log.warning(f"[DOC] Unknown file format for: {title}")

            # Determine stored file path based on input type
            if resolved_file_path is not None:
                # File already on disk - copy to raw directory
                if not resolved_file_path.exists():
                    return DocumentUploadResult(
                        success=False,
                        title=title,
                        error=f"File not found: {resolved_file_path}",
                    )

                # Generate unique filename preserving original extension
                stored_name = self._generate_filename(title, fmt.extension)
                stored_path = raw_dir / stored_name

                # Copy file to storage
                shutil.copy2(resolved_file_path, stored_path)
                log.info(f"[DOC] Copied file to: {stored_path}")

            else:
                # File-like object - save to raw directory
                stored_name = self._generate_filename(title, fmt.extension)
                stored_path = raw_dir / stored_name

                # Write file content
                self._save_uploaded_file(file, stored_path)
                log.info(f"[DOC] Saved uploaded file to: {stored_path}")

            # Check if document already exists (by path)
            existing = repo.get_by_file_path(stored_path)
            if existing:
                log.warning(f"[DOC] Document already exists: {existing.id}")
                return DocumentUploadResult(
                    success=True,
                    document_id=existing.id,
                    title=existing.title,
                    file_path=existing.file_path,
                    transcript_path=existing.transcript_path,
                    error="Document already exists (returning existing)",
                    warnings=warnings,
                )

            # Extract text if enabled and format supports it
            transcript_path: Optional[Path] = None
            if extract_text:
                transcript_path = self._extract_text(stored_path, fmt, transcripts_dir)
                if fmt in EXTRACTABLE_FORMATS and transcript_path is None:
                    warnings.append("Text extraction failed, document saved without transcript")

            # Determine source_type for DB (use provided or default to format)
            db_source_type = source_type if source_type else fmt.value

            # Create database record
            doc = repo.create(
                title=title,
                file_path=stored_path,
                source_type=db_source_type,
                transcript_path=transcript_path,
                document_date=document_date,
            )

            log.info(f"[DOC] Created document: id={doc.id}, title={title}, format={fmt.value}")

            return DocumentUploadResult(
                success=True,
                document_id=doc.id,
                title=doc.title,
                file_path=doc.file_path,
                transcript_path=doc.transcript_path,
                warnings=warnings,
            )

        except Exception as e:
            log.exception(f"[DOC] Upload failed: {e}")
            return DocumentUploadResult(
                success=False,
                title=title,
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

    def delete_document(self, document_id: int, delete_files: bool = False) -> bool:
        """
        Delete a document.

        Args:
            document_id: Document ID to delete
            delete_files: Whether to delete associated files from disk

        Returns:
            True if deleted successfully, False if document not found
        """
        repo = DocumentRepository(self.session)
        doc = repo.get(document_id)

        if doc is None:
            log.warning(f"[DOC] Document not found for deletion: {document_id}")
            return False

        # Delete files if requested
        if delete_files:
            if doc.file_path:
                file_path = Path(doc.file_path)
                if file_path.exists():
                    file_path.unlink()
                    log.info(f"[DOC] Deleted file: {doc.file_path}")

            if doc.transcript_path:
                transcript_path = Path(doc.transcript_path)
                if transcript_path.exists():
                    transcript_path.unlink()
                    log.info(f"[DOC] Deleted transcript: {doc.transcript_path}")

        # Delete from database
        repo.delete(document_id)
        log.info(f"[DOC] Deleted document: {document_id}")

        return True

    def reprocess_document(
        self,
        document_id: int,
        force: bool = False,
    ) -> DocumentUploadResult:
        """
        Reprocess an existing document (re-extract text).

        Args:
            document_id: Document ID to reprocess
            force: If True, re-extract even if transcript exists

        Returns:
            DocumentUploadResult with updated info
        """
        repo = DocumentRepository(self.session)
        doc = repo.get(document_id)

        if doc is None:
            return DocumentUploadResult(
                success=False,
                error=f"Document {document_id} not found",
            )

        if doc.transcript_path and not force:
            return DocumentUploadResult(
                success=True,
                document_id=doc.id,
                title=doc.title,
                file_path=doc.file_path,
                transcript_path=doc.transcript_path,
                warnings=["Transcript already exists, use force=True to re-extract"],
            )

        if not doc.file_path:
            return DocumentUploadResult(
                success=False,
                document_id=doc.id,
                title=doc.title,
                error="Document has no file path",
            )

        file_path = Path(doc.file_path)
        if not file_path.exists():
            return DocumentUploadResult(
                success=False,
                document_id=doc.id,
                title=doc.title,
                error=f"File not found: {file_path}",
            )

        # Detect format and extract
        fmt = FileFormat.from_extension(file_path.suffix)
        _, transcripts_dir = self._get_storage_dirs()

        transcript_path = self._extract_text(file_path, fmt, transcripts_dir)

        if transcript_path:
            repo.update_transcript_path(doc, transcript_path)
            log.info(f"[DOC] Reprocessed document {document_id}: {transcript_path}")

        return DocumentUploadResult(
            success=True,
            document_id=doc.id,
            title=doc.title,
            file_path=doc.file_path,
            transcript_path=str(transcript_path) if transcript_path else None,
        )


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
) -> DocumentUploadResult:
    """
    Upload a document (convenience function).

    See DocumentService.upload_document for full documentation.
    """
    with DocumentService() as service:
        return service.upload_document(
            file=file,
            file_path=file_path,
            filename=filename,
            title=title,
            document_date=document_date,
            source_type=source_type,
            file_format=file_format,
        )