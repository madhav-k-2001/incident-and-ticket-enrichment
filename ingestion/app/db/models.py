import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.config import get_settings
from app.db.database import Base

settings = get_settings()


def utc_now():
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String(255), nullable=False)
    file_type = Column(String(10), nullable=False)  # 'pdf' or 'docx'
    file_size = Column(Integer, nullable=False)  # in bytes
    file_path = Column(String(500), nullable=False)
    status = Column(String(30), default="PENDING", nullable=False, index=True)
    # Statuses: PENDING, PARSING, PROCESSING, RATE_LIMITED_PAUSED, COMPLETED, FAILED
    total_chunks = Column(Integer, default=0, nullable=False)
    processed_chunks = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    chunks = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan", order_by="DocumentChunk.chunk_index"
    )

    def to_dict(self):
        progress = 0.0
        if self.total_chunks > 0:
            progress = round((self.processed_chunks / self.total_chunks) * 100, 1)
        elif self.status == "COMPLETED":
            progress = 100.0

        return {
            "id": self.id,
            "filename": self.filename,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "status": self.status,
            "total_chunks": self.total_chunks,
            "processed_chunks": self.processed_chunks,
            "progress_percent": progress,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    page_number = Column(Integer, nullable=True)
    content = Column(Text, nullable=False)
    char_count = Column(Integer, nullable=False)
    estimated_tokens = Column(Integer, nullable=False)

    # pgvector embedding column with configurable dimension (default 768)
    embedding = Column(Vector(settings.EMBEDDING_DIMENSION), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    document = relationship("Document", back_populates="chunks")

    __table_args__ = (
        Index("ix_doc_chunk_doc_index", "document_id", "chunk_index"),
        Index(
            "ix_doc_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def to_dict(self, include_embedding: bool = False):
        data = {
            "id": self.id,
            "document_id": self.document_id,
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "content": self.content,
            "char_count": self.char_count,
            "estimated_tokens": self.estimated_tokens,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_embedding and self.embedding is not None:
            data["embedding"] = list(self.embedding)
        return data
