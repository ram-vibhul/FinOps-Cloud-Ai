# Developer shortcuts. Backend venv lives in backend/.venv.
PY := backend/.venv/bin

.PHONY: setup dev-api dev-ui test test-pg pg-stop lint build check notices image

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

# Backend tests against a throwaway PostgreSQL 17 container (needs Docker).
PG_TEST_URL := postgresql://costlens:costlens@localhost:55432/costlens_test
test-pg:
	@docker inspect costlens-pgtest >/dev/null 2>&1 || docker run -d --name costlens-pgtest \
	  -e POSTGRES_USER=costlens -e POSTGRES_PASSWORD=costlens -e POSTGRES_DB=costlens_test \
	  -p 55432:5432 postgres:17.11-trixie >/dev/null
	@docker start costlens-pgtest >/dev/null
	@until docker exec costlens-pgtest pg_isready -U costlens -d costlens_test >/dev/null 2>&1; do sleep 1; done
	cd backend && COSTLENS_TEST_DATABASE_URL=$(PG_TEST_URL) .venv/bin/pytest

pg-stop:
	-docker rm -f costlens-pgtest

build:
	cd frontend && npm run build

check: lint test build
	$(PY)/python tools/third_party_notices.py --check

notices:
	$(PY)/python tools/third_party_notices.py

image:
	docker build -t costlens:dev .
