"""Trace-id propagation and structured (JSON) logging.

The active trace id lives in a ContextVar so every layer (tools, service, HTTP
client, logs) sees the same id for a single tool call without passing it around.
"""

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:12]}"


def current_trace_id() -> str:
    return _trace_id.get() or new_trace_id()


def bind_trace_id(trace_id: str | None) -> str:
    """Bind the given (or a freshly generated) trace id to the current context."""
    trace_id = trace_id or new_trace_id()
    _trace_id.set(trace_id)
    return trace_id


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "trace_id": _trace_id.get(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    # stderr only: stdout is reserved for the MCP stdio transport.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
