from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db():
    """Initializes the database, installs the pgvector extension, and creates all tables."""
    try:
        with engine.connect() as conn:
            logger.info("Enabling pgvector extension if not present...")
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            conn.commit()

        logger.info("Creating tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully with pgvector support.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise


def get_db():
    """FastAPI dependency for yielding database sessions."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
