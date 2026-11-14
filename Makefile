# Common tasks. UV and NPM can be overridden (e.g. make UV=/path/to/uv test).
UV ?= uv
NPM ?= npm
PORT ?= 8780

.PHONY: setup dev api web test lint check build e2e

setup:            ## install Python and web dependencies
	$(UV) sync
	cd web && $(NPM) ci

api:              ## run the API with reload on port $(PORT)
	$(UV) run uvicorn service.app:app --reload --host 127.0.0.1 --port $(PORT)

web:              ## run the web dev server (proxies /api to port $(PORT))
	cd web && SKY_API=http://127.0.0.1:$(PORT) $(NPM) run dev

dev:              ## API and web dev server together
	$(MAKE) -j2 api web

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy
	cd web && $(NPM) run typecheck

test:             ## unit and contract tests
	$(UV) run pytest
	cd web && $(NPM) test

build:
	cd web && $(NPM) run build

e2e: build        ## end-to-end tests in a real browser against the real service
	cd web && UV=$(UV) npx playwright test

check: lint test e2e
