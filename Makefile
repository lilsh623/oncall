PYTHON ?= python
API_HOST ?= 0.0.0.0
API_PORT ?= 8000

.PHONY: install api infra-up infra-ps infra-down demo-healthy demo-fail

install:
	$(PYTHON) -m pip install -e ".[dev]"

api:
	$(PYTHON) -m uvicorn oncall.api.main:create_app --factory --reload --host $(API_HOST) --port $(API_PORT)

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
