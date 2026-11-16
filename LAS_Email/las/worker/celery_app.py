"""Celery application + Beat schedule. The queue only triggers work; Postgres holds the truth.

This service only reads and extracts; Core LAS does everything else. So the schedule is:
re-drive stuck inbound mail, deliver events to Core, and the housekeeping that keeps metering and
the audit chain honest. Nothing here sends mail.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.schedules import crontab

from las.common.settings import get_settings

_s = get_settings()

SCHEDULE: dict[str, dict[str, Any]] = {
    "sweep-inbound": {"task": "las.sweep.inbound", "schedule": 120.0},
    "core-intake-handoff": {"task": "las.jobs.core_intake_handoff", "schedule": 15.0},
    "usage-rollup": {"task": "las.jobs.usage_rollup", "schedule": crontab(hour=2, minute=0)},
    "audit-verify": {"task": "las.jobs.audit_verify", "schedule": crontab(hour=2, minute=30)},
}

app = Celery("las", broker=_s.redis_url, include=["las.worker.tasks"])
app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="las",
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    timezone="Asia/Kolkata",
    beat_schedule=SCHEDULE,
)
