.DEFAULT_GOAL := help
.PHONY: help setup up down logs test format format-check

PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Prepare a local checkout (.env, virtualenvs)
	./scripts/dev-setup.sh

up: ## Build and start the stack
	docker compose up --build

down: ## Stop the stack
	docker compose down

logs: ## Follow the stack logs
	docker compose logs -f

test: ## Run every test suite
	$(PY) scripts/run_tests.py

format: ## Format the code with ruff
	uvx ruff format .

format-check: ## Check formatting without changing files
	uvx ruff format --check .
