from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from innovation_intelligence.config import settings
from innovation_intelligence.db.base import Base


# SQLAlchemy engine
engine = create_engine(
    settings.db.url,
    echo=False,   # set True for SQL debug
    future=True,
)

# Session factory
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)


def init_db() -> None:
    """
    Create all tables if they don't exist yet.
    Import models here so that they are registered on Base.metadata.
    """
    # Import models to register them with Base.metadata
    from innovation_intelligence.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
