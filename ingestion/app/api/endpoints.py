import os
import uuid
import json
import logging
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
import redis

from app.config import get_settings
from app.db.database import get_db
from app.db.models import Document, DocumentChunk
from app.services.rate_limiter import RateLimiter
from app.services.embedder import EmbedderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
settings = get_settings()

# Initialize services
try:
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
except Exception:
    redis_client = None

rate_limiter = RateLimiter(redis_client=redis_client)
embedder_service = EmbedderService(rate_limiter=rate_limiter)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Text query to search for")
    top_k: int = Field(5, ge=1, le=50, description="Number of results to return")
    document_id: Optional[str] = Field(None, description="Optional filter by document ID")


class SearchResultItem(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    page_number: Optional[int]
    content: str
    similarity: float


class SearchResponse(BaseModel):
    query: str
    results_count: int
    results: List[SearchResultItem]


@router.post("/upload")
async def upload_documents(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    """
    Accepts multiple PDF or DOCX file uploads, saves them, creates DB records,
    and enqueues them for asynchronous ingestion via Redis.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    allowed_extensions = {".pdf", ".docx", ".doc"}
    uploaded_docs = []

    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{ext}' for file '{file.filename}'. Allowed: PDF, DOCX."
            )

        # Generate unique storage filename to avoid collisions
        unique_id = uuid.uuid4().hex
        stored_filename = f"{unique_id}_{file.filename}"
        file_path = settings.upload_path / stored_filename

        # Stream file to disk
        content = await file.read()
        file_size = len(content)
        with open(file_path, "wb") as f:
            f.write(content)

        # Create Document record
        doc = Document(
            id=unique_id,
            filename=file.filename,
            file_type=ext.replace(".", ""),
            file_size=file_size,
            file_path=str(file_path),
            status="PENDING",
            total_chunks=0,
            processed_chunks=0
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        # Enqueue in Redis
        if redis_client:
            try:
                payload = json.dumps({"document_id": doc.id})
                redis_client.rpush(settings.REDIS_QUEUE_NAME, payload)
                logger.info(f"Enqueued document {doc.id} ({doc.filename}) into Redis.")
            except Exception as e:
                logger.error(f"Failed to enqueue document in Redis: {e}")
                # We keep the doc in DB; worker or manual retry can process it
        else:
            logger.warning("Redis client unavailable; document created in DB but not queued.")

        uploaded_docs.append(doc.to_dict())

    return {
        "message": f"Successfully queued {len(uploaded_docs)} document(s) for ingestion.",
        "documents": uploaded_docs
    }


@router.get("/documents")
def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Returns the list of uploaded documents and their ingestion statuses."""
    docs = db.query(Document).order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
    total = db.query(Document).count()
    return {
        "total": total,
        "documents": [d.to_dict() for d in docs]
    }


@router.get("/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db)):
    """Returns status details for a single document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc.to_dict()


@router.get("/documents/{document_id}/chunks")
def get_document_chunks(
    document_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Returns extracted chunks and vector embedding status for a document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    total_chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .count()
    )

    return {
        "document_id": document_id,
        "filename": doc.filename,
        "total_chunks": total_chunks,
        "chunks": [c.to_dict(include_embedding=False) for c in chunks]
    }


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    """Deletes a document, removing its chunks from pgvector and the file from disk."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Remove file on disk if exists
    if os.path.exists(doc.file_path):
        try:
            os.remove(doc.file_path)
        except Exception as e:
            logger.warning(f"Could not remove file on disk: {e}")

    db.delete(doc)
    db.commit()
    return {"message": f"Document '{doc.filename}' deleted successfully."}


@router.get("/quota")
def get_quota_status():
    """Returns live rate limit meters (RPM, TPM, RPD) for the web portal dashboard."""
    return rate_limiter.get_quota_stats()


@router.post("/search", response_model=SearchResponse)
def search_embeddings(
    req: SearchRequest,
    db: Session = Depends(get_db)
):
    """
    Performs semantic vector search against stored document chunks in pgvector
    using cosine similarity with Gemini Embedding 2.
    """
    try:
        # Embed user's search query
        query_vector = embedder_service.embed_query(req.query)
        vector_str = "[" + ",".join(f"{x:.6f}" for x in query_vector) + "]"

        # Cosine distance in pgvector: <=>
        # Cosine similarity: 1 - (embedding <=> query_vector)
        where_clause = "WHERE dc.document_id = :doc_id" if req.document_id else ""
        
        sql = text(f"""
            SELECT 
                dc.id AS chunk_id,
                dc.document_id,
                d.filename,
                dc.chunk_index,
                dc.page_number,
                dc.content,
                1 - (dc.embedding <=> cast(:vector_str as vector)) AS similarity
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            {where_clause}
            ORDER BY dc.embedding <=> cast(:vector_str as vector) ASC
            LIMIT :top_k;
        """)

        params = {"vector_str": vector_str, "top_k": req.top_k}
        if req.document_id:
            params["doc_id"] = req.document_id

        rows = db.execute(sql, params).fetchall()

        results = []
        for row in rows:
            results.append(SearchResultItem(
                chunk_id=str(row.chunk_id),
                document_id=str(row.document_id),
                filename=str(row.filename),
                chunk_index=int(row.chunk_index),
                page_number=int(row.page_number) if row.page_number is not None else None,
                content=str(row.content),
                similarity=round(float(row.similarity), 4)
            ))

        return SearchResponse(
            query=req.query,
            results_count=len(results),
            results=results
        )
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Health status check for DB, Redis, and Gemini API configuration."""
    db_ok = False
    try:
        db.execute(text("SELECT 1;"))
        db_ok = True
    except Exception:
        pass

    redis_ok = False
    if redis_client:
        try:
            redis_ok = bool(redis_client.ping())
        except Exception:
            pass

    gemini_configured = bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "your_gemini_api_key_here")

    return {
        "status": "healthy" if (db_ok and redis_ok) else "degraded",
        "database_connected": db_ok,
        "redis_connected": redis_ok,
        "gemini_api_key_configured": gemini_configured,
        "embedding_model": settings.GEMINI_EMBEDDING_MODEL,
        "embedding_dimension": settings.EMBEDDING_DIMENSION,
    }
