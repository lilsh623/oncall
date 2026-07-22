"""Celery application configured for the hybrid local runtime."""

from celery import Celery

from oncall.config import get_broker_settings


redis_url = get_broker_settings().redis_url
celery_app = Celery(
    "oncall",
    broker=redis_url,
    backend=redis_url,
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
