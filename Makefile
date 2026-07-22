PYTHON ?= python

.PHONY: venv install api infra-up infra-ps infra-down

venv:
	python3 -m venv .venv

install:
	$(PYTHON) -m pip install -e ".[dev]"

api:
	$(PYTHON) -m uvicorn oncall.api.main:create_app --factory --reload --port 8000

infra-up:
	$(PYTHON) -m podman_compose up -d postgres redis etcd minio milvus demo-service prometheus alertmanager traffic-generator

infra-ps:
	$(PYTHON) -m podman_compose ps

infra-down:
	$(PYTHON) -m podman_compose down
