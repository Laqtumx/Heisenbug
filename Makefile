SHELL := /bin/bash
PY    := backend/.venv/bin/python
PIP   := backend/.venv/bin/pip

.PHONY: help setup db-up db-down migrate demo-data api test test-fast lint clean nuke

help:
	@echo "CleanSlate - move the data, not the mistakes."
	@echo ""
	@echo "  make setup      create the venv, install deps, start databases, migrate"
	@echo "  make db-up      start Postgres (5433) and Mongo (27018) containers"
	@echo "  make db-down    stop the database containers"
	@echo "  make migrate    apply SQL migrations"
	@echo "  make demo-data  generate the synthetic legacy dataset into data/legacy"
	@echo "  make seed       load demo users and run a full end-to-end migration"
	@echo "  make api        run the FastAPI server on :8000"
	@echo "  make web        run the Next.js frontend on :3000"
	@echo "  make test       run the whole pytest suite"
	@echo "  make test-fast  run everything except the slow pipeline tests"

setup: db-up
	python3 -m venv backend/.venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	$(PY) -m app.db.migrate
	@echo ""
	@echo "Setup complete. Next: make demo-data && make seed && make api"

db-up:
	@bash scripts/db_up.sh

db-down:
	@docker rm -f cleanslate-pg cleanslate-mongo >/dev/null 2>&1 || true
	@echo "databases stopped"

migrate:
	cd backend && .venv/bin/python -m app.db.migrate

demo-data:
	$(PY) scripts/generate_demo_data.py

seed:
	$(PY) scripts/seed.py

api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

web:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/python -m pytest -q

test-fast:
	cd backend && .venv/bin/python -m pytest -q -m "not pipeline"

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/.mypy_cache

nuke: db-down clean
	rm -rf backend/.venv frontend/node_modules frontend/.next data
