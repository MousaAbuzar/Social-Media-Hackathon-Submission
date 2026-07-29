import logging

from celery import Celery

from app.config import get_settings
from app.db import sync_session
from app.pipeline.runner import advance

log = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery("scriptcast", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)


@celery_app.task(
    bind=True,
    name="scriptcast.advance_run",
    autoretry_for=(Exception,),
    retry_backoff=5,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=3,
)
def advance_run(self, run_id: str) -> str:
    """Run one stage, then re-enqueue for the next.

    One stage per task keeps each unit of work short, so a redeploy or worker
    crash costs at most one stage rather than an entire run.
    """
    with sync_session() as session:
        more = advance(session, run_id)

    if more:
        advance_run.delay(run_id)
        return "continued"
    return "finished"
