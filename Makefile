PYTHON ?= python
API_HOST ?= 0.0.0.0
API_PORT ?= 8000

.PHONY: install api worker observability-mcp release-mcp demo-runtime-init infra-up infra-ps infra-down demo-healthy demo-fail

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

demo-runtime-init:
	mkdir -p .runtime
	test -f .runtime/demo-release.json || cp demo/release/demo-release.seed.json .runtime/demo-release.json

infra-up:
	$(PYTHON) -m podman_compose up -d postgres redis etcd minio milvus demo-service prometheus alertmanager traffic-generator

infra-ps:
	$(PYTHON) -m podman_compose ps

infra-down:
	$(PYTHON) -m podman_compose down

demo-healthy:
	DEMO_VERSION=v1 $(PYTHON) -m podman_compose up -d --force-recreate demo-service traffic-generator

demo-fail:
	DEMO_VERSION=v2 $(PYTHON) -m podman_compose up -d --force-recreate demo-service traffic-generator
