"""Phase M v5 — 60-second URL reachability probe.

Kept separate from ``reservation_scheduler`` because the two have very
different cadence (reservation tick = 30s, URL probe = 60s) and very
different failure modes (URL probe is harmless I/O, reservation tick
mutates user-visible state). Both share the same AsyncIOScheduler
pattern so failures are easy to debug.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from ..db import get_session_factory
from ..logging_setup import get_logger
from ..models import Lab
from . import lab_url_service

_log = get_logger("corelab.lab_url_scheduler")
_JOB_ID = "lab_url_probe_tick"


async def url_probe_tick() -> None:
    """Walk every lab, probe every URL, update reachability state.

    Single-tenant CoreLab → one lab → one set of probes per tick. The
    code keeps the lab loop in case multi-tenant arrives later; it adds
    nothing to per-tick cost when there's only one row.
    """
    factory = get_session_factory()
    started = datetime.now(UTC)
    async with factory() as session:
        result = await session.execute(select(Lab))
        labs = result.scalars().all()
        if not labs:
            return
        for lab in labs:
            summary = await lab_url_service.probe_lab(session, lab=lab)
            if summary:
                _log.info(
                    "lab_url_probe.tick",
                    lab_id=lab.id,
                    reachable=sum(1 for ok in summary.values() if ok),
                    total=len(summary),
                )
        await session.commit()
    duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    _log.debug("lab_url_probe.tick.done", duration_ms=duration_ms)


def build_scheduler(*, tick_seconds: int = 60) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(event_loop=asyncio.get_event_loop())
    scheduler.add_job(
        url_probe_tick,
        trigger="interval",
        seconds=tick_seconds,
        id=_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def start_scheduler(*, tick_seconds: int = 60) -> AsyncIOScheduler:
    scheduler = build_scheduler(tick_seconds=tick_seconds)
    scheduler.start()
    _log.info("lab_url_scheduler.started", tick_seconds=tick_seconds, job_id=_JOB_ID)
    return scheduler


async def shutdown_scheduler(scheduler: AsyncIOScheduler) -> None:
    scheduler.shutdown(wait=False)
    _log.info("lab_url_scheduler.stopped", job_id=_JOB_ID)
