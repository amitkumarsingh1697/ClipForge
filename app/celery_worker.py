"""Celery app initialization.

Long-running live recordings go to their own `live` queue so they can't starve
the clip renderers (run a separate worker with `-Q live`).
"""
from celery import Celery

from app.config import get_settings

_s = get_settings()
celery_app = Celery("clip_agent", broker=_s.redis_url, backend=_s.redis_url)
celery_app.conf.update(
    task_always_eager=_s.celery_eager,
    task_acks_late=True,              # a crashed worker's job is redelivered
    worker_prefetch_multiplier=1,     # one heavy video job at a time per process
    task_routes={"app.celery_worker.record_live_task": {"queue": "live"}},
    broker_transport_options={"visibility_timeout": 8 * 3600},
    result_expires=86400,
)


@celery_app.task
def ingest_source_task(job_id: str):
    from app.pipeline import ingest_source
    return ingest_source(job_id)


@celery_app.task
def render_clip_task(job_id: str):
    from app.pipeline import render_clip
    out = render_clip(job_id)
    return str(out) if out else None


@celery_app.task
def publish_clip_task(job_id: str):
    from app.pipeline import publish_clip
    return publish_clip(job_id)


@celery_app.task
def record_live_task(job_id: str):
    from app.pipeline import record_live
    return record_live(job_id)
