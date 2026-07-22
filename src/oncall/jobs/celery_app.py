"""Celery application configured for the hybrid local runtime."""

import os

from celery import Celery


def _redis_url() -> str:
    # Keep module imports side-effect free; the complete Settings object is loaded
    # only when a job actually needs application services.
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:16379/0")


celery_app = Celery(
    "oncall",
    broker=_redis_url(),
    backend=_redis_url(),
    include=["oncall.jobs.tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    timezone="UTC",
    enable_utc=True,
)
