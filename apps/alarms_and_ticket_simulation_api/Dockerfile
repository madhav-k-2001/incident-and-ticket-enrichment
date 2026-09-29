# ==============================================================================
# Stage 1: Build & Dependency Installation (using uv & Python 3.12)
# ==============================================================================
FROM python:3.12-slim-bookworm AS builder

# Copy uv binary from the official Astral image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Enable bytecode compilation and standalone virtualenv creation
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy dependency specifications first for Docker layer caching
COPY pyproject.toml requirements.txt ./

# Create virtual environment and install dependencies
RUN uv venv /app/.venv && \
    uv pip install -r requirements.txt

# ==============================================================================
# Stage 2: Minimal Runtime Image
# ==============================================================================
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

# Copy the pre-built virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

# Place the virtual environment's bin directory on the PATH
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as a non-privileged user for container security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Copy application code and mock data
COPY --chown=appuser:appuser simulator_app.py mock_data.json ./

# Expose API port
EXPOSE 8000

# Container health check using built-in Python standard library
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Start FastAPI application with Uvicorn
CMD ["uvicorn", "simulator_app:app", "--host", "0.0.0.0", "--port", "8000"]
