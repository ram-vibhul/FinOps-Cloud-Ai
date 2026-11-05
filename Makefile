# Developer shortcuts. Backend venv lives in backend/.venv.
PY := backend/.venv/bin

.PHONY: setup dev-api dev-ui test lint build check notices image

setup:
	python3 -m venv backend/.venv
	$(PY)/pip install -e 'backend[dev]'
	cd frontend && npm ci

dev-api:
	cd backend && .venv/bin/costlens migrate && .venv/bin/costlens serve --reload

dev-ui:
	cd frontend && npm run dev

lint:
	cd backend && .venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/pyright
	cd frontend && npm run lint && npm run typecheck

test:
	cd backend && .venv/bin/pytest
	cd frontend && npm test

build:
	cd frontend && npm run build

check: lint test build
	$(PY)/python tools/third_party_notices.py --check

notices:
	$(PY)/python tools/third_party_notices.py

image:
	docker build -t costlens:dev .
