# Developer shortcuts. Backend venv lives in backend/.venv.
PY := backend/.venv/bin

.PHONY: setup dev-api dev-ui test test-pg pg-stop lint build check notices image

setup:
	python3 -m venv backend/.venv
	$(PY)/pip install -e 'backend[dev]'
	cd frontend && npm ci

dev-api:
	cd backend && .venv/bin/ccradar migrate && CCRADAR_LOCAL_LOGIN=true .venv/bin/ccradar serve --reload

dev-ui:
	cd frontend && npm run dev

lint:
	cd backend && .venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/pyright
	cd agent && ../$(PY)/ruff format --check . && ../$(PY)/ruff check . && ../$(PY)/pyright
	cd sdk/python && ../../$(PY)/ruff format --check . && ../../$(PY)/ruff check . && ../../$(PY)/pyright
	cd sdk/typescript && ../../frontend/node_modules/.bin/tsc -p tsconfig.json
	@command -v helm >/dev/null && helm lint deploy/helm/cloudcostradar-agent --quiet \
	  --set ccradar.url=https://ccradar.example --set ccradar.token=ckt_lint_token || true
	cd frontend && npm run lint && npm run typecheck

test:
	cd backend && .venv/bin/pytest
	cd agent && ../$(PY)/pytest
	cd sdk/python && ../../$(PY)/pytest -q -p no:cacheprovider
	cd sdk/typescript && node --test test/*.test.ts
	cd frontend && npm test

# Backend tests against a throwaway PostgreSQL 17 container (needs Docker).
PG_TEST_URL := postgresql://ccradar:ccradar@localhost:55432/ccradar_test
test-pg:
	@docker inspect ccradar-pgtest >/dev/null 2>&1 || docker run -d --name ccradar-pgtest \
	  -e POSTGRES_USER=ccradar -e POSTGRES_PASSWORD=ccradar -e POSTGRES_DB=ccradar_test \
	  -p 55432:5432 postgres:17.11-trixie >/dev/null
	@docker start ccradar-pgtest >/dev/null
	@until docker exec ccradar-pgtest pg_isready -U ccradar -d ccradar_test >/dev/null 2>&1; do sleep 1; done
	cd backend && CCRADAR_TEST_DATABASE_URL=$(PG_TEST_URL) .venv/bin/pytest

pg-stop:
	-docker rm -f ccradar-pgtest

build:
	cd frontend && npm run build

check: lint test build
	$(PY)/python tools/third_party_notices.py --check
	$(PY)/python tools/website_plans.py --check
	$(PY)/python tools/config_reference.py --check
	$(PY)/python tools/changelog.py --check

notices:
	$(PY)/python tools/third_party_notices.py

image:
	docker build -t ccradar:dev .
	docker build -f agent/Dockerfile -t cloudcostradar-agent:dev .
