# Document Ingestion Pipeline

An end-to-end, production-ready document ingestion pipeline supporting multi-file uploads (PDF and DOCX), asynchronous task processing via a Redis queue, strict rate-limit protection (**100 RPM, 30K TPM, 1K RPD**), embeddings using Google's **Gemini Embedding 2** (`gemini-embedding-2-preview`), vector storage in **PostgreSQL with pgvector**, and an interactive web portal with semantic vector search.

---

## 🌟 Key Features

1. **Multi-File Drag-and-Drop Web Portal**:
   - Modern, responsive dark UI to drop multiple `.pdf` and `.docx` files simultaneously.
   - Live ingestion progress monitoring (`PARSING` &rarr; `PROCESSING` &rarr; `COMPLETED`).
   - Chunk Inspector to inspect extracted text chunks and page attribution.
   - Interactive **Semantic Search Playground** to query embeddings against pgvector via cosine similarity.

2. **Strict Gemini Rate-Limit Safeguards**:
   - **1,000 Requests/Day (RPD)**: High-density adaptive batching (20–40 chunks per request) cuts API calls by up to 40x.
   - **30,000 Tokens/Minute (TPM)**: Redis sliding-window token bucket throttles at a safe 24,000 TPM ceiling.
   - **100 Requests/Minute (RPM)**: Enforced via rolling request window (capped at 80 RPM).
   - **Live Quota Meters**: Dashboard gauges displaying real-time TPM, RPM, and RPD consumption.

3. **Resilient Ingestion Worker**:
   - Asynchronous queue consumer using Redis.
   - Document extraction: `pypdf` for PDFs (preserving page numbers) and `python-docx` for Word documents (paragraphs and tables).
   - Recursive semantic chunking with configurable overlap.
   - **Incremental Checkpointing**: Embeddings are committed to Postgres immediately per batch. If paused or interrupted, processing resumes without duplicate API calls or wasted tokens.

4. **PostgreSQL + pgvector Vector Storage**:
   - 768-dimensional vector embeddings with Google's Matryoshka Representation Learning.
   - Fast cosine similarity retrieval using **HNSW** indexing (`vector_cosine_ops`).

---

## 🏗️ Architecture

```
[Web Portal UI] ──(Multi-file Drop)──> [FastAPI Backend] ──(Save & Enqueue)──> [Redis Queue]
       ▲                                                                               │
       │ (Live Status & Quota)                                                         │ (BLPOP)
       │                                                                               ▼
[Rate Limit Dashboard] <── [Redis Rate Limiter] <─────── [Ingestion Worker]
                                 (RPM, TPM, RPD)                 │
                                                                 ├──> [PDF / DOCX Parser]
                                                                 ├──> [Semantic Chunker]
                                                                 ├──> [Gemini Embedding 2]
                                                                 │       (dim: 768)
                                                                 ▼
                                                    [PostgreSQL (pgvector)]
                                                 (HNSW Cosine Similarity Index)
```

---

## 🚀 Quick Start (Docker Compose)

### 1. Configure Environment
Copy the example environment file and insert your Gemini API key:
```bash
cp .env.example .env
```
Edit `.env` and set:
```env
GEMINI_API_KEY=your_actual_gemini_api_key
```

### 2. Start Services
To avoid collisions with other projects on your machine, ports are pre-configured:
- Web Portal / API: `http://localhost:8000`
- PostgreSQL pgvector: `localhost:5434`
- Redis: `localhost:6380`

Start the entire stack with Docker Compose:
```bash
docker compose up -d
```

### 3. Open Web Portal
Navigate to:
```
http://localhost:8000
```
- Drag and drop PDF or DOCX files into the upload area.
- Watch live ingestion progress and quota meters.
- Search your ingested documents in the Semantic Search Playground!

---

## 💻 Local Development Setup (Without Docker Compose)

If you already have PostgreSQL (with pgvector) and Redis running:

### 1. Setup Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start PostgreSQL with pgvector & Redis
```bash
# Starts pgvector on port 5434 and Redis on port 6380
docker compose up -d db redis
```

### 3. Start Ingestion Worker
```bash
python -m worker.worker
```

### 4. Start FastAPI Application
In another terminal:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Running Automated Tests

Run the full test suite (21 unit and end-to-end integration tests):
```bash
pytest tests/ -v
```

The test suite validates:
- PDF extraction with page attribution (`tests/test_parser.py`)
- DOCX extraction with paragraphs and tables (`tests/test_parser.py`)
- Recursive semantic chunking, overlap, and token estimation (`tests/test_chunker.py`)
- Adaptive batch builder preserving rate limits (`tests/test_chunker.py`)
- Redis sliding-window rate limiter & safety caps (`tests/test_rate_limiter.py`)
- Gemini Embedding 2 mock vector generation & dimension verification (`tests/test_embedder.py`)
- API endpoints: upload, health, quota, documents (`tests/test_api.py`)
- Complete end-to-end ingestion and pgvector cosine search (`tests/test_e2e.py`)

---

## 📡 REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/upload` | Multipart multi-file upload (`.pdf`, `.docx`) |
| `GET` | `/api/documents` | List uploaded documents with status and progress |
| `GET` | `/api/documents/{id}` | Get status details for a document |
| `GET` | `/api/documents/{id}/chunks` | Inspect extracted chunks for a document |
| `DELETE` | `/api/documents/{id}` | Delete document, file, and pgvector embeddings |
| `GET` | `/api/quota` | Get live rate-limit meter metrics (RPM, TPM, RPD) |
| `POST` | `/api/search` | Execute semantic vector search (`query`, `top_k`, `document_id`) |
| `GET` | `/api/health` | Health check for DB, Redis, and Gemini API |

---

## ⚙️ Configuration Reference

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | `""` | Google Gemini API Key |
| `GEMINI_EMBEDDING_MODEL`| `gemini-embedding-2-preview` | Model name |
| `EMBEDDING_DIMENSION` | `768` | Matryoshka output dimension |
| `RATE_LIMIT_MAX_RPM` | `80` | Safety cap for Requests/Minute |
| `RATE_LIMIT_MAX_TPM` | `24000` | Safety cap for Tokens/Minute |
| `RATE_LIMIT_MAX_RPD` | `950` | Safety cap for Requests/Day |
| `POSTGRES_PORT` | `5434` | Host port for pgvector PostgreSQL |
| `REDIS_PORT` | `6380` | Host port for Redis |
| `CHUNK_SIZE` | `1000` | Target characters per chunk |
| `CHUNK_OVERLAP` | `150` | Character overlap between chunks |
| `MAX_BATCH_SIZE` | `30` | Max chunks per API request |
| `TARGET_BATCH_TOKENS` | `6000` | Target tokens per batch |
