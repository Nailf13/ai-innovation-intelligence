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
        file_path: Path | str,
        source_type: str = "pdf",
        transcript_path: Path | str | None = None,
        document_date: datetime | None = None,
    ) -> Document:
        doc = Document(
            title=title,
            source_type=source_type,
            file_path=str(file_path),
            transcript_path=str(transcript_path) if transcript_path else None,
            document_date=document_date,
        )
        self.session.add(doc)
        self.session.commit()
        self.session.refresh(doc)
        return doc

    def get(self, doc_id: int) -> Optional[Document]:
        return self.session.get(Document, doc_id)

    def get_by_file_path(self, file_path: Path | str) -> Optional[Document]:
        return (
            self.session.query(Document)
            .filter(Document.file_path == str(file_path))
            .first()
        )

    def list_all(self) -> List[Document]:
        return (
            self.session.query(Document)
            .order_by(Document.created_at.desc())
            .all()
        )

    # ------------------- Updates -------------------- #
    def update_transcript_path(
        self,
        document: Document,
        transcript_path: Path | str,
    ) -> Document:
        document.transcript_path = str(transcript_path)
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

    # ------------------- Helpers -------------------- #
    def index_by_path(self) -> Dict[str, Document]:
        """Return mapping file_path -> Document for all docs."""
        docs = self.list_all()
        return {d.file_path: d for d in docs}
