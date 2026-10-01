#!/usr/bin/env bash
# One-shot local dev setup. Run from anywhere:  ./scripts/dev-setup.sh
#
#   - checks the required software (git, uv, make, docker + compose v2, Python 3.12+)
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
missing=0
ok()   { printf '  [ok]      %-16s %s\n' "$1" "$2"; }
fail() { printf '  [missing] %-16s %s\n' "$1" "$2"; missing=1; }
warn() { printf '  [warn]    %-16s %s\n' "$1" "$2"; }

if command -v git >/dev/null 2>&1; then
  ok git "$(git --version)"
else
  fail git "install from https://git-scm.com/downloads"
fi

if command -v uv >/dev/null 2>&1; then
  ok uv "$(uv --version)"
else
  fail uv "install from https://docs.astral.sh/uv/getting-started/installation/"
fi

if command -v make >/dev/null 2>&1; then
  ok make "$(make --version | head -n1)"
else
  fail make "install it (Linux: apt install make, macOS: xcode-select --install, Windows: choco install make)"
fi

# uv can download Python 3.12 itself, so an older or missing system Python is only a warning.
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
  ok "python (3.12+)" "$(python3 --version)"
else
  warn "python (3.12+)" "not found on PATH; uv will download it, but scripts/run_tests.py needs a 3.12+ python or the .venv"
fi

if command -v docker >/dev/null 2>&1; then
  ok docker "$(docker --version)"
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose" "$(docker compose version --short) (v2 required)"
  else
    fail "docker compose" "Compose v2 plugin not found: https://docs.docker.com/compose/install/"
  fi
  docker info >/dev/null 2>&1 || warn "docker daemon" "not running or not reachable; start Docker before 'docker compose up'"
else
  fail docker "install from https://docs.docker.com/get-docker/"
  fail "docker compose" "comes with Docker Desktop / the Compose v2 plugin"
fi

[ "$missing" -eq 0 ] || die "install the missing prerequisites above and re-run this script"

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
echo "Next: edit .env, then run 'make up' (or 'make test'). 'make help' lists all targets"
