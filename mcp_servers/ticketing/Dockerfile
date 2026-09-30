# ==============================================================================
# Stage 1: Resolve & install locked dependencies with uv
# ==============================================================================
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself (installed non-editable so src/ isn't needed at runtime)
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# ==============================================================================
# Stage 2: Minimal runtime image
# ==============================================================================
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

RUN useradd -m -u 1000 appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8100

USER appuser

EXPOSE 8100

# MCP streamable-http has no plain GET health route; verify the port accepts connections
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os, socket; socket.create_connection(('127.0.0.1', int(os.environ.get('MCP_PORT', '8100'))), timeout=3).close()" || exit 1

CMD ["ticketing-mcp"]
