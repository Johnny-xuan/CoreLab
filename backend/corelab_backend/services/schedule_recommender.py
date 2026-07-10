"""Phase J — recommend the earliest matching reservation window.

Given a user's resource ask (``gpu_count`` GPUs on one server, plus a
``time_limit_seconds`` runtime cap, optionally not before ``after``),
walk every server's GPU time axes and return the first K candidate
``(server_id, gpu_ids, start_at, end_at)`` tuples.

Algorithm (single-server, single-pass sweep — small-lab scale is fine):

1. List all active GPUs in the lab, grouped by server.
2. Filter out servers that don't have at least ``gpu_count`` GPUs.
3. For each server:
   a. Pull every scheduled/active reservation on the server's GPUs
      that overlaps the search horizon ``[after, after + 7 days)``.
   b. For each GPU, derive a sorted list of (busy_start, busy_end)
      intervals from those reservations.
   c. Sweep the time axis with a min-heap of "next event per GPU".
      At every interval boundary, recompute ``free_now = set of GPU
      ids that are NOT in a busy interval``. Whenever ``len(free_now)
      >= gpu_count`` for a contiguous span >= ``time_limit_seconds``,
      record a candidate at the start of that span with any
      ``gpu_count`` of the free GPUs.
4. Aggregate candidates across servers, sort by start_at, return top K.

We deliberately do NOT enumerate combinations (C(8, 2) etc.) — for the
small-lab use case, "any gpu_count out of the currently-free set" is
indistinguishable from "the optimal pick" and far simpler to debug.

For Mode 3 (no GPU) the API layer doesn't call this — it builds a row
directly with ``gpu_id=None``. Recommendation only matters when the
user wants GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Gpu, Reservation, Server
from . import reservation_service

# Search at most 7 days ahead. Beyond that the suggestion is too
# speculative — better to say "no near-term slot, try lowering the
# request or check back later".
HORIZON_DAYS: int = 7

# Returned to the user — we send only the earliest few so the UI can
# show a tight "this/next/later" tri-fold without scroll fatigue.
DEFAULT_TOP_K: int = 3


@dataclass(frozen=True, slots=True)
class Candidate:
    server_id: int
    gpu_ids: list[int]
    start_at: datetime
    end_at: datetime


async def recommend(
    session: AsyncSession,
    *,
    lab_id: int,
    gpu_count: int,
    time_limit_seconds: int,
    after: datetime | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[Candidate]:
    """Find the earliest ``top_k`` candidate windows in the lab.

    ``after`` defaults to "now"; the caller can pass a later instant to
    surface "I want to start after dinner" candidates.

    Returns an empty list when no server has the GPU count + duration
    available within the 7-day horizon. The API layer maps an empty
    list to a 200 with ``candidates: []`` so the UI can show "nothing
    near-term, lower the ask".
    """
    if gpu_count < 1:
        raise ValueError("gpu_count must be >= 1")
    if time_limit_seconds < 60:
        raise ValueError("time_limit_seconds must be >= 60")

    # MySQL TIMESTAMP fields come back as naive datetimes, so we sweep
    # in naive-UTC space to keep all comparisons mixed-tz-safe.
    now = datetime.now(UTC).replace(tzinfo=None)
    after_naive = (
        after.replace(tzinfo=None) if after is not None and after.tzinfo is not None else after
    )
    search_start = max(after_naive or now, now)
    horizon_end = search_start + timedelta(days=HORIZON_DAYS)
    duration = timedelta(seconds=time_limit_seconds)

    # 1) GPUs grouped by server (active only — soft-deleted rows skipped).
    gpu_rows = (
        await session.execute(
            select(Gpu.id, Gpu.server_id)
            .join(Server, Gpu.server_id == Server.id)
            .where(
                Server.lab_id == lab_id,
                Server.is_active == 1,
                Server.status.in_(("online", "offline")),  # offline is fine — agent may reconnect
                Gpu.is_active == 1,
            )
            .order_by(Gpu.server_id, Gpu.gpu_index)
        )
    ).all()

    gpus_by_server: dict[int, list[int]] = {}
    for gpu_id, server_id in gpu_rows:
        gpus_by_server.setdefault(server_id, []).append(gpu_id)

    # 2) Discard servers without enough GPUs to satisfy the ask.
    eligible_servers = {
        server_id: gids for server_id, gids in gpus_by_server.items() if len(gids) >= gpu_count
    }
    if not eligible_servers:
        return []

    # 3) Pull every overlapping busy interval for every eligible GPU in
    #    one query — let the DB do the filter rather than per-server
    #    round-trips.
    all_gpu_ids = [gid for gids in eligible_servers.values() for gid in gids]
    busy_rows = (
        await session.execute(
            select(Reservation.gpu_id, Reservation.start_at, Reservation.end_at)
            .where(
                Reservation.gpu_id.in_(all_gpu_ids),
                Reservation.status.in_(reservation_service.ACTIVE_STATUSES),
                Reservation.start_at < horizon_end,
                Reservation.end_at > search_start,
            )
            .order_by(Reservation.gpu_id, Reservation.start_at)
        )
    ).all()

    busy_by_gpu: dict[int, list[tuple[datetime, datetime]]] = {}
    for gpu_id, b_start, b_end in busy_rows:
        # Clip to the search window to keep the sweep tight.
        busy_by_gpu.setdefault(gpu_id, []).append(
            (max(b_start, search_start), min(b_end, horizon_end))
        )

    # 4) Per-server sweep — find the earliest contiguous span where
    #    >= gpu_count GPUs are free for >= duration seconds.
    candidates: list[Candidate] = []
    for server_id, gids in eligible_servers.items():
        candidate = _earliest_window_for_server(
            gpu_ids=gids,
            busy_by_gpu=busy_by_gpu,
            window_start=search_start,
            window_end=horizon_end,
            gpu_count=gpu_count,
            duration=duration,
        )
        if candidate is not None:
            candidates.append(Candidate(server_id=server_id, **candidate))

    # 5) Sort by start_at, take top_k.
    candidates.sort(key=lambda c: c.start_at)
    return candidates[:top_k]


def _earliest_window_for_server(
    *,
    gpu_ids: list[int],
    busy_by_gpu: dict[int, list[tuple[datetime, datetime]]],
    window_start: datetime,
    window_end: datetime,
    gpu_count: int,
    duration: timedelta,
) -> dict | None:
    """Sweep one server's GPU axes; return the earliest ``duration``-long
    span where >= ``gpu_count`` GPUs are continuously free.

    Returns ``{gpu_ids, start_at, end_at}`` (without ``server_id`` —
    caller folds that in) or ``None`` if nothing fits in this server's
    horizon.

    The sweep collects every busy interval boundary as an event, sorts
    them, and walks the timeline. ``free_set`` is the set of GPU ids
    currently NOT in any busy interval at the sweep cursor; whenever it
    grows from "< gpu_count" to ">= gpu_count" we record the candidate
    start; whenever it shrinks back below the threshold we check if the
    span lasted >= duration.
    """
    # Build events: (time, +1 if entering free, -1 if leaving free, gpu_id)
    # Free transitions are derived from busy intervals — entering a busy
    # interval = leaving the free set, leaving a busy interval = entering.
    events: list[tuple[datetime, int, int]] = []
    for gpu_id in gpu_ids:
        for b_start, b_end in busy_by_gpu.get(gpu_id, []):
            events.append((b_start, -1, gpu_id))  # leave free at busy start
            events.append((b_end, +1, gpu_id))  # re-enter free at busy end
    # Sort: order by time; -1 before +1 at same instant so an ending
    # interval and a starting interval at the same time net out cleanly.
    events.sort(key=lambda e: (e[0], e[1]))

    # Initial state at window_start: all GPUs free that aren't in a
    # busy interval starting exactly at window_start.
    free_set = set(gpu_ids)
    # Pre-apply any busy interval that contains window_start.
    for gpu_id in gpu_ids:
        for b_start, b_end in busy_by_gpu.get(gpu_id, []):
            if b_start <= window_start < b_end:
                free_set.discard(gpu_id)

    candidate_start: datetime | None = None
    if len(free_set) >= gpu_count:
        candidate_start = window_start

    for event_time, delta, gpu_id in events:
        # Apply event.
        if delta == -1:
            free_set.discard(gpu_id)
        else:
            free_set.add(gpu_id)

        had_enough_before_event = candidate_start is not None
        has_enough_now = len(free_set) >= gpu_count

        if not had_enough_before_event and has_enough_now:
            candidate_start = event_time
        elif had_enough_before_event and not has_enough_now:
            # The span [candidate_start, event_time) was a stretch with
            # >= gpu_count free. Check if it was long enough.
            span_end = event_time
            assert candidate_start is not None
            if span_end - candidate_start >= duration:
                return {
                    "gpu_ids": _pick_first_n(gpu_ids, gpu_count, busy_by_gpu, candidate_start),
                    "start_at": candidate_start,
                    "end_at": candidate_start + duration,
                }
            candidate_start = None

        if event_time >= window_end:
            break

    # If we ended with an open candidate that runs all the way to the
    # horizon, accept it provided it lasted long enough.
    if candidate_start is not None and window_end - candidate_start >= duration:
        return {
            "gpu_ids": _pick_first_n(gpu_ids, gpu_count, busy_by_gpu, candidate_start),
            "start_at": candidate_start,
            "end_at": candidate_start + duration,
        }

    return None


def _pick_first_n(
    gpu_ids: list[int],
    n: int,
    busy_by_gpu: dict[int, list[tuple[datetime, datetime]]],
    at_time: datetime,
) -> list[int]:
    """Pick the first ``n`` GPUs (by gpu_index — caller pre-sorted) that
    are free at ``at_time``. The sweep guaranteed >= n are free at this
    instant; this is just a deterministic tie-break."""
    picked: list[int] = []
    for gpu_id in gpu_ids:
        if any(b_start <= at_time < b_end for b_start, b_end in busy_by_gpu.get(gpu_id, [])):
            continue
        picked.append(gpu_id)
        if len(picked) == n:
            break
    return picked
