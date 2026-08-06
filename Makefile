PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
API_HOST ?= 0.0.0.0
API_PORT ?= 8000

.PHONY: install api worker recovery-mcp migrate

install:
	$(PYTHON) -m pip install -e .

api:
	$(PYTHON) -m uvicorn oncall.api.main:create_app --factory --reload --host $(API_HOST) --port $(API_PORT)

worker:
	$(PYTHON) -m celery -A oncall.jobs.celery_app:celery_app worker --loglevel=INFO

recovery-mcp:
	$(PYTHON) -m uvicorn mcp_servers.recovery.server:app --host 127.0.0.1 --port 8080

migrate:
	$(PYTHON) -m alembic upgrade head
