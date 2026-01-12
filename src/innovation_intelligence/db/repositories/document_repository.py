from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from innovation_intelligence.db.models import Document


class DocumentRepository:
    """
    Repository for Document entities.
    Encapsulates all DB operations related to documents.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # --------------------- CRUD --------------------- #
    def create(
        self,
        *,
        title: str,
        gcs_document_uri: str,
        source_type: str = "pdf",
        gcs_transcript_uri: str | None = None,
        document_date: datetime | None = None,
    ) -> Document:
        """Create a new document record (GCS-first mode)."""
        doc = Document(
            title=title,
            source_type=source_type,
            document_date=document_date,
            gcs_document_uri=gcs_document_uri,
            gcs_transcript_uri=gcs_transcript_uri,
        )
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    def get(self, doc_id: int) -> Optional[Document]:
        return self.session.get(Document, doc_id)

    def get_by_gcs_uri(self, gcs_uri: str) -> Optional[Document]:
        """Get document by GCS URI."""
        return (
            self.session.query(Document)
            .filter(Document.gcs_document_uri == gcs_uri)
            .first()
        )

    def list_all(
        self,
        source_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Document]:
        """List documents with optional filtering."""
        query = self.session.query(Document)

        if source_type:
            query = query.filter(Document.source_type == source_type)

        query = query.order_by(Document.created_at.desc())

        if limit:
            query = query.limit(limit)

        return query.all()

    # ------------------- Updates -------------------- #
    def update_transcript_uri(
        self,
        document: Document,
        gcs_transcript_uri: str,
    ) -> Document:
        """Update transcript GCS URI."""
        document.gcs_transcript_uri = gcs_transcript_uri
        self.session.commit()
        self.session.refresh(document)
        return document

    def delete(self, doc_id: int) -> bool:
        """Delete a document by ID. Returns True if deleted."""
        doc = self.get(doc_id)
        if doc is None:
            return False
        self.session.delete(doc)
        self.session.commit()
        return True

    # ------------------- Updates -------------------- #
    def update_document_date(
        self,
        document: Document,
        document_date: datetime,
    ) -> Document:
        """Update document date."""
        document.document_date = document_date
        self.session.commit()
        self.session.refresh(document)
        return document

    def update_gcs_uris(
        self,
        document: Document,
        gcs_document_uri: str | None = None,
        gcs_transcript_uri: str | None = None,
    ) -> Document:
        """Update GCS URIs for a document."""
        if gcs_document_uri is not None:
            document.gcs_document_uri = gcs_document_uri
        if gcs_transcript_uri is not None:
            document.gcs_transcript_uri = gcs_transcript_uri
        self.session.commit()
        self.session.refresh(document)
        return document

    # ------------------- Helpers -------------------- #
    def index_by_gcs_uri(self) -> Dict[str, Document]:
        """Return mapping gcs_document_uri -> Document for all docs."""
        docs = self.list_all()
        return {d.gcs_document_uri: d for d in docs}

    # ------------------- Counts -------------------- #
    def count(self) -> int:
        """Count total documents."""
        return self.session.query(Document).count()

    def count_with_transcripts(self) -> int:
        """Count documents with transcripts (in GCS)."""
        return (
            self.session.query(Document)
            .filter(Document.gcs_transcript_uri.isnot(None))
            .count()
        )

    def count_by_source_type(self) -> Dict[str, int]:
        """Count documents grouped by source type."""
        from sqlalchemy import func
        results = (
            self.session.query(
                Document.source_type,
                func.count(Document.id)
            )
            .group_by(Document.source_type)
            .all()
        )
        return {source_type: count for source_type, count in results}
