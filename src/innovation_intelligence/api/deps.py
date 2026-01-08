# src/innovation_intelligence/api/deps.py
"""
FastAPI dependencies for dependency injection.
"""
from typing import Generator

from sqlalchemy.orm import Session

from innovation_intelligence.db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """
    Database session dependency.
    
    Yields a SQLAlchemy session and ensures cleanup after request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
