.PHONY: venv install api

venv:
	python3 -m venv .venv

install:
	.venv/bin/pip install -e .

api:
	.venv/bin/uvicorn oncall.api.main:create_app --factory --reload --port 8000
