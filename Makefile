PYTHON ?= python
API_HOST ?= 0.0.0.0
API_PORT ?= 8000

.PHONY: install api worker observability-mcp release-mcp recovery-mcp demo-runtime-init infra-up app-up infra-ps infra-down down demo-healthy demo-fail demo-reset migrate

install:
	$(PYTHON) -m pip install -e ".[dev]"

api:
	$(PYTHON) -m uvicorn oncall.api.main:create_app --factory --reload --host $(API_HOST) --port $(API_PORT)

worker:
	$(PYTHON) -m celery -A oncall.jobs.celery_app:celery_app worker --loglevel=INFO

observability-mcp:
	$(PYTHON) -m uvicorn mcp_servers.observability.server:app --host 127.0.0.1 --port 18081

release-mcp: demo-runtime-init
	$(PYTHON) -m uvicorn mcp_servers.release.server:app --host 127.0.0.1 --port 18082

recovery-mcp: demo-runtime-init
	$(PYTHON) -m uvicorn mcp_servers.recovery.server:app --host 127.0.0.1 --port 8080

migrate:
	$(PYTHON) -m alembic upgrade head

demo-runtime-init:
	mkdir -p .runtime
	mkdir -p .runtime/demo-logs
	chmod 700 .runtime/demo-logs
	test -f .runtime/demo-release.json || cp demo/release/demo-release.seed.json .runtime/demo-release.json
	chmod 600 .runtime/demo-release.json

infra-up: demo-runtime-init
	$(PYTHON) -m podman_compose up -d postgres redis etcd minio milvus demo-service prometheus alertmanager traffic-generator

app-up: demo-runtime-init migrate
	$(PYTHON) -m podman_compose up -d api worker observability-mcp release-mcp frontend

infra-ps:
	$(PYTHON) -m podman_compose ps

infra-down:
	$(PYTHON) -m podman_compose down

down:
	$(PYTHON) -m podman_compose down

demo-healthy: demo-runtime-init
	DEMO_VERSION=v1 $(PYTHON) -m podman_compose up -d --build --force-recreate demo-service traffic-generator
	$(PYTHON) -m oncall.cli demo-release sync --version v1

demo-fail: demo-runtime-init
	DEMO_VERSION=v2 $(PYTHON) -m podman_compose up -d --build --force-recreate demo-service traffic-generator
	$(PYTHON) -m oncall.cli demo-release sync --version v2

demo-reset: demo-healthy
