# src/innovation_intelligence/api/routers/documents.py
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.api.schemas import (
    DocumentDB,
    DocumentListResponse,
    DocumentUploadResponse,
    SuccessResponse,
)
from innovation_intelligence.config import settings
from innovation_intelligence.db.models import Document
from innovation_intelligence.logger import get_logger
from innovation_intelligence.ingestion.documents.document_service import DocumentService  # NEW

log = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    source_type: Optional[str] = Form(None),
    document_date: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")

    # Parse document date
    doc_date = None
    if document_date:
        try:
            doc_date = datetime.fromisoformat(document_date)
        except ValueError:
            pass

    # ✅ FIX: Pass file_format explicitly from extension
    file_format = ext.lstrip(".")  # ".pdf" → "pdf"
    
    doc_title = title or Path(file.filename).stem.replace("_", " ")

    service = DocumentService(session=db)
    result = service.upload_document(
        file=file.file,
        filename=file.filename,      # ✅ Pass filename for format detection
        title=doc_title,
        document_date=doc_date,
        source_type=source_type,     # Business category (report, article...)
        file_format=file_format,     # ✅ Explicit format (pdf, txt...)
        extract_text=True,
    )

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "Upload failed")

    document = db.query(Document).filter(Document.id == result.document_id).first()
    
    return DocumentUploadResponse(
        id=document.id,
        title=document.title,
        file_path=document.file_path,
        source_type=document.source_type,
        created_at=document.created_at,
    )


@router.get("/", response_model=DocumentListResponse)
def list_documents(skip: int = 0, limit: int = 50, source_type: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Document)
    if source_type:
        query = query.filter(Document.source_type == source_type)
    total = query.count()
    documents = query.offset(skip).limit(limit).all()
    return DocumentListResponse(documents=[DocumentDB.model_validate(doc) for doc in documents], count=total)


@router.get("/{document_id}", response_model=DocumentDB)
def get_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDB.model_validate(document)


@router.delete("/{document_id}", response_model=SuccessResponse)
def delete_document(document_id: int, delete_file: bool = False, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if delete_file and document.file_path:
        file_path = Path(document.file_path)
        if file_path.exists():
            file_path.unlink()
    db.delete(document)
    db.commit()
    return SuccessResponse(message=f"Document {document_id} deleted")
