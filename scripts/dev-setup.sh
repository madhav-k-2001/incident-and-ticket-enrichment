#!/usr/bin/env bash
# One-shot local dev setup. Run from anywhere:  ./scripts/dev-setup.sh
#
#   - creates .env from .env.sample (if missing)
#   - creates the repo-root .venv with the backend, simulator and ingestion dependencies
#   - creates a .venv inside each MCP server with its dependencies
#
# Afterwards: `python scripts/run_tests.py` runs every test suite.
# To run the stack itself, use `docker compose up --build` (see README).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\n==> %s\n' "$1"; }
die()  { printf 'error: %s\n' "$1" >&2; exit 1; }

step "Checking prerequisites"
command -v uv >/dev/null 2>&1 || die "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
command -v docker >/dev/null 2>&1 || echo "warning: docker not found; you need it to run the full stack"
echo "uv: $(uv --version)"

step "Environment file"
if [ -f .env ]; then
  echo ".env already exists, leaving it alone"
else
  cp .env.sample .env
  echo "created .env from .env.sample, now set OPENAI_API_KEY and the database settings"
fi

step "Root virtualenv (.venv): backend, simulator, ingestion"
uv venv --python ">=3.12" .venv
uv pip install --python .venv \
  -r apps/backend/pyproject.toml \
  -r apps/alarms_and_ticket_simulation_api/requirements.txt \
  -r ingestion/requirements.txt \
  pytest pytest-asyncio aiosqlite

for server in alarm-management ticketing knowledge-base; do
  step "MCP server: $server"
  (cd "mcp_servers/$server" && uv sync)
done

step "Done"
echo "Next: edit .env, then run 'docker compose up --build' (or 'python scripts/run_tests.py')"
