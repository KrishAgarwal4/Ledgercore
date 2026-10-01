.PHONY: help setup dev test bench migrate clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
UVICORN := $(VENV)/bin/uvicorn

help:
	@echo "LedgerCore Developer Commands:"
	@echo "  make setup    - Create virtualenv and install all dependencies"
	@echo "  make dev      - Start FastAPI development server with reload"
	@echo "  make test     - Run full automated test suite with pytest"
	@echo "  make bench    - Run high-concurrency race condition benchmark"
	@echo "  make migrate  - Apply db/schema.sql to ledgercore database"
	@echo "  make clean    - Remove build artifacts and caches"

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo "Setup complete. Virtual environment ready in $(VENV)"

dev:
	$(UVICORN) ledgercore.api.app:app --host 0.0.0.0 --port 8000 --reload

test:
	$(PYTEST) -v

bench:
	$(PYTHON) concurrency_test.py --workers 100

migrate:
	psql -d ledgercore -f db/schema.sql
	psql -d ledgercore_test -f db/schema.sql

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	rm -rf build dist *.egg-info
