import time
import json
import logging
import signal
import sys
import redis
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import SessionLocal, init_db
from app.db.models import Document, DocumentChunk
from app.services.rate_limiter import RateLimiter
from app.services.embedder import EmbedderService
from worker.parser import parse_document
from worker.chunker import chunk_document_sections, create_adaptive_batches

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Worker] %(message)s"
)
logger = logging.getLogger(__name__)

running = True


def signal_handler(signum, frame):
    global running
    logger.info(f"Signal {signum} received. Gracefully shutting down worker...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class IngestionWorker:
    def __init__(self):
        self.settings = get_settings()
        self.redis_client = redis.Redis.from_url(self.settings.redis_url, decode_responses=True)
        self.rate_limiter = RateLimiter(redis_client=self.redis_client)
        self.embedder = EmbedderService(rate_limiter=self.rate_limiter)
        self.queue_name = self.settings.REDIS_QUEUE_NAME

    def process_document(self, document_id: str):
        db: Session = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                logger.error(f"Document {document_id} not found in database.")
                return

            if doc.status == "COMPLETED":
                logger.info(f"Document {document_id} ({doc.filename}) is already completed.")
                return

            logger.info(f"Processing document: {doc.filename} (ID: {doc.id}, Type: {doc.file_type})")
            
            # Step 1: Parsing
            doc.status = "PARSING"
            db.commit()

            try:
                sections = parse_document(doc.file_path)
            except Exception as pe:
                logger.error(f"Failed to parse document {doc.filename}: {pe}", exc_info=True)
                doc.status = "FAILED"
                doc.error_message = f"Parse Error: {str(pe)}"
                db.commit()
                return

            if not sections:
                doc.status = "FAILED"
                doc.error_message = "No readable text content extracted from document."
                db.commit()
                return

            # Step 2: Chunking
            chunks = chunk_document_sections(sections)
            doc.total_chunks = len(chunks)
            doc.status = "PROCESSING"
            db.commit()
            logger.info(f"Generated {len(chunks)} chunks for {doc.filename}.")

            # Step 3: Checkpoint & Resume capability
            # Check already processed chunks in DB to avoid re-embedding
            processed_indices = set(
                row[0] for row in db.query(DocumentChunk.chunk_index)
                .filter(DocumentChunk.document_id == doc.id)
                .all()
            )
            doc.processed_chunks = len(processed_indices)
            db.commit()

            pending_chunks = [c for c in chunks if c["chunk_index"] not in processed_indices]
            if not pending_chunks:
                logger.info(f"All chunks already embedded for {doc.filename}.")
                doc.status = "COMPLETED"
                db.commit()
                return

            # Step 4: Form adaptive batches bounded by size and tokens
            batches = create_adaptive_batches(
                pending_chunks,
                max_batch_size=self.settings.MAX_BATCH_SIZE,
                target_batch_tokens=self.settings.TARGET_BATCH_TOKENS
            )
            logger.info(f"Batched {len(pending_chunks)} pending chunks into {len(batches)} batches.")

            # Step 5: Process batches with rate limiting & incremental checkpointing
            for batch_idx, batch in enumerate(batches):
                if not running:
                    logger.warning("Shutdown requested during batch processing. Saving progress and exiting.")
                    break

                batch_texts = [c["content"] for c in batch]
                batch_tokens = sum(c["estimated_tokens"] for c in batch)

                # Check if rate limiter requires pause
                allowed, wait_sec, reason = self.rate_limiter.can_consume(tokens=batch_tokens, requests=1)
                if not allowed:
                    logger.info(f"Rate limit throttle for {doc.filename}: {reason}. Pausing {wait_sec:.1f}s...")
                    doc.status = "RATE_LIMITED_PAUSED"
                    db.commit()
                    time.sleep(wait_sec + 0.5)
                    doc.status = "PROCESSING"
                    db.commit()

                # Embed batch
                embeddings = self.embedder.embed_texts(batch_texts)

                # Persist batch immediately to pgvector
                chunk_objects = []
                for chunk_data, emb in zip(batch, embeddings):
                    chunk_obj = DocumentChunk(
                        document_id=doc.id,
                        chunk_index=chunk_data["chunk_index"],
                        page_number=chunk_data.get("page_number"),
                        content=chunk_data["content"],
                        char_count=chunk_data["char_count"],
                        estimated_tokens=chunk_data["estimated_tokens"],
                        embedding=emb
                    )
                    chunk_objects.append(chunk_obj)

                db.bulk_save_objects(chunk_objects)
                doc.processed_chunks += len(batch)
                db.commit()

                logger.info(
                    f"[{doc.filename}] Progress: {doc.processed_chunks}/{doc.total_chunks} chunks "
                    f"({(doc.processed_chunks / doc.total_chunks) * 100:.1f}%)"
                )

            if doc.processed_chunks >= doc.total_chunks:
                doc.status = "COMPLETED"
                db.commit()
                logger.info(f"Successfully completed ingestion for {doc.filename} ({doc.total_chunks} chunks).")

        except Exception as e:
            logger.error(f"Unexpected error processing document {document_id}: {e}", exc_info=True)
            db.rollback()
            try:
                doc = db.query(Document).filter(Document.id == document_id).first()
                if doc:
                    doc.status = "FAILED"
                    doc.error_message = f"Worker Error: {str(e)}"
                    db.commit()
            except Exception:
                pass
        finally:
            db.close()

    def recover_orphaned_documents(self):
        """
        Scans Postgres on startup for any documents left in PARSING, PROCESSING,
        or RATE_LIMITED_PAUSED due to an ungraceful process crash (SIGKILL, OOM, power loss).
        Re-enqueues them into Redis so they resume from their last saved checkpoint.
        """
        db: Session = SessionLocal()
        try:
            interrupted_docs = db.query(Document).filter(
                Document.status.in_(["PARSING", "PROCESSING", "RATE_LIMITED_PAUSED"])
            ).all()

            if not interrupted_docs:
                return

            logger.warning(f"Found {len(interrupted_docs)} orphaned document(s) in Postgres from a prior crash.")

            # Read existing queue items to avoid duplicate enqueueing
            queued_raw = self.redis_client.lrange(self.queue_name, 0, -1)
            queued_ids = set()
            for item in queued_raw:
                try:
                    d = json.loads(item)
                    queued_ids.add(d.get("document_id") if isinstance(d, dict) else item)
                except Exception:
                    queued_ids.add(item)

            for doc in interrupted_docs:
                if doc.id not in queued_ids:
                    logger.info(
                        f"Auto-recovering interrupted document: {doc.filename} "
                        f"(ID: {doc.id}, Processed: {doc.processed_chunks}/{doc.total_chunks}). Re-queueing in Redis."
                    )
                    doc.status = "PENDING"
                    db.commit()
                    payload = json.dumps({"document_id": doc.id})
                    self.redis_client.rpush(self.queue_name, payload)
        except Exception as e:
            logger.error(f"Error during startup orphan recovery: {e}")
        finally:
            db.close()

    def start(self):
        logger.info(f"Worker started. Listening on Redis queue '{self.queue_name}'...")
        # Auto-recover any jobs interrupted by a crash before entering poll loop
        self.recover_orphaned_documents()
        while running:
            try:
                # BLPOP waits up to 2 seconds for a message
                result = self.redis_client.blpop(self.queue_name, timeout=2)
                if result:
                    _, payload = result
                    try:
                        data = json.loads(payload)
                        doc_id = data.get("document_id") if isinstance(data, dict) else payload
                    except Exception:
                        doc_id = payload

                    if doc_id:
                        self.process_document(doc_id)
            except redis.ConnectionError:
                logger.warning(f"Redis connection lost. Retrying in 5 seconds...")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Worker queue polling error: {e}", exc_info=True)
                time.sleep(1)


def main():
    logger.info("Initializing database extension and schema...")
    try:
        init_db()
    except Exception as e:
        logger.warning(f"Could not connect to database on startup: {e}. Will retry during task execution.")

    worker = IngestionWorker()
    worker.start()


if __name__ == "__main__":
    main()
