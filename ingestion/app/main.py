import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import router as api_router
from app.db.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database extension and schema...")
    try:
        init_db()
        logger.info("Database startup initialization completed.")
    except Exception as e:
        logger.warning(f"Database initialization deferred (database may still be starting): {e}")
    yield


app = FastAPI(
    title="Document Ingestion Pipeline",
    description="Multi-file ingestion pipeline using Redis Queue, Gemini Embedding 2, and pgvector",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API router
app.include_router(api_router)

# Mount static directory for the Web Portal
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
def serve_portal():
    """Serves the main Web Portal UI."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Document Ingestion Pipeline API is running. Web Portal UI not found."}
