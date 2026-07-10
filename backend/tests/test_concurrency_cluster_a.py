"""Concurrency regression tests — audit cluster A (locking reads).

Each test drives the documented TOCTOU race with real service functions
on independent AsyncSessions and asserts the post-fix (locking-read)
behaviour. These reproduce, and now guard against regressing:

* #1  cancel_reservation must not clobber a scheduler-committed terminal
      state (failed/completed) when it wakes from the SP-5 long await with
      a stale ``active`` snapshot.
* #5  concurrent approve of one pending account_link_request must not run
      its side effects twice (double audit/notification, double key push).
* #20 the last-active-admin invariant must hold under two concurrent
      disables; the lab can never drop to zero active admins.

They depend on ``integration_client`` only to get a clean DB + a wired
engine; the race itself is run directly against ``get_session_factory``.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from corelab_backend.db import get_session_factory
from corelab_backend.models import (
    AccountLink,
    AccountLinkRequest,
    AuditLog,
    Gpu,
    Lab,
    PhysicalAccount,
    Reservation,
    Server,
    SshPublicKey,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import (
    account_link_request_service,
    agent_rpc,
    reservation_service,
    user_service,
)
from httpx import AsyncClient
from sqlalchemy import func, select


async def _seed_base(slug: str) -> dict[str, Any]:
    """lab + admin user + server + gpu + pa + active account_link."""
    factory = get_session_factory()
    async with factory() as s, s.begin():
        lab = Lab(name=slug, slug=slug)
        s.add(lab)
        await s.flush()
        u = User(
            lab_id=lab.id,
            username=f"{slug}_u",
            email=f"{slug}@r.test",
            display_name="U",
            password_hash=hash_password("Pw!2024xxxx"),  # pragma: allowlist secret
            role="lab_admin",
        )
        s.add(u)
        await s.flush()
        srv = Server(
            lab_id=lab.id,
            hostname=f"h.{slug}",
            display_name="H",
            status="online",
            created_by_user_id=u.id,
            max_reservation_hours=24,
        )
        s.add(srv)
        await s.flush()
        gpu = Gpu(server_id=srv.id, gpu_index=0, model="RTX 4090", memory_total_mb=24576)
        s.add(gpu)
        await s.flush()
        pa = PhysicalAccount(
            server_id=srv.id,
            linux_username=f"{slug}_lx",
            uid=1001,
            source="admin_manual_register",
            created_by_user_id=u.id,
        )
        s.add(pa)
        await s.flush()
        link = AccountLink(
            user_id=u.id,
            physical_account_id=pa.id,
            source="ssh_challenge",
            proof_evidence={"challenge_id": "x"},
            established_by_user_id=u.id,
        )
        s.add(link)
        await s.flush()
        return {
            "lab_id": lab.id,
            "user_id": u.id,
            "server_id": srv.id,
            "gpu_id": gpu.id,
            "pa_id": pa.id,
            "link_id": link.id,
        }


async def test_cancel_does_not_clobber_terminal_state(integration_client: AsyncClient) -> None:
    """#1 — cancel on a stale ``active`` snapshot must not overwrite a
    concurrently-committed ``failed``."""
    del integration_client
    w = await _seed_base("cc1")
    factory = get_session_factory()
    base = datetime.now(UTC).replace(microsecond=0)
    async with factory() as s, s.begin():
        res = Reservation(
            user_id=w["user_id"],
            server_id=w["server_id"],
            gpu_id=w["gpu_id"],
            account_link_id=w["link_id"],
            start_at=base - timedelta(minutes=30),
            end_at=base + timedelta(minutes=1),
            status="active",
            script="echo hi",
            script_status="running",
        )
        s.add(res)
        await s.flush()
        res_id = res.id

    async with factory() as s1:
        # Router-style read: s1's identity map now caches status='active'
        # (the stale snapshot SP-5 holds across its ~10s await).
        row1 = await s1.get(Reservation, res_id)
        assert row1.status == "active"

        # Concurrent scheduler tick: active -> failed, committed.
        async with factory() as s2, s2.begin():
            row2 = await s2.get(Reservation, res_id)
            await reservation_service.transition_to_failed(
                s2, reservation=row2, lab_id=w["lab_id"], reason="script_killed"
            )

        # SP-5 wakes and cancels on the stale snapshot — must be rejected.
        raised = False
        try:
            await reservation_service.cancel_reservation(
                s1,
                reservation_id=res_id,
                actor_user_id=w["user_id"],
                actor_can_admin=True,
                reason="user_cancel",
                lab_id=w["lab_id"],
            )
            await s1.commit()
        except reservation_service.ReservationError:
            await s1.rollback()
            raised = True

    assert raised, "cancel should be rejected once the row is terminal"
    async with factory() as s3:
        assert (await s3.get(Reservation, res_id)).status == "failed"


async def test_concurrent_approve_runs_side_effects_once(
    integration_client: AsyncClient, monkeypatch: Any
) -> None:
    """#5 — two concurrent approvals of one pending request: exactly one
    succeeds, exactly one approval audit row is written."""
    del integration_client
    w = await _seed_base("cc5")
    factory = get_session_factory()
    async with factory() as s, s.begin():
        req_user = User(
            lab_id=w["lab_id"],
            username="cc5_req",
            email="cc5req@r.test",
            display_name="Req",
            password_hash=hash_password("Pw!2024xxxx"),  # pragma: allowlist secret
            role="user",
        )
        s.add(req_user)
        await s.flush()
        s.add(
            SshPublicKey(
                user_id=req_user.id,
                public_key="ssh-ed25519 AAAA cc5",
                fingerprint_sha256="SHA256:" + "a" * 43,
                key_type="ssh-ed25519",
                is_active=1,
            )
        )
        reqrow = AccountLinkRequest(
            requester_user_id=req_user.id,
            physical_account_id=w["pa_id"],
            status="pending",
        )
        s.add(reqrow)
        await s.flush()
        req_id = reqrow.id

    # Force the agent-offline branch so neither approval inserts an
    # authorized_key_entry — without a unique key to collide on, only the
    # locking read can serialize the two approvals.
    async def _offline(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise agent_rpc.AgentOfflineError("test: agent offline")

    monkeypatch.setattr(account_link_request_service.agent_rpc, "request_response", _offline)

    s1 = factory()
    s2 = factory()

    async def approve(sess: Any) -> str:
        try:
            await account_link_request_service.approve_request(
                sess,
                request_id=req_id,
                decision_note="ok",
                admin_user_id=w["user_id"],
                lab_id=w["lab_id"],
            )
            await sess.commit()
            return "OK"
        except Exception as e:
            await sess.rollback()
            return type(e).__name__

    try:
        r1, r2 = await asyncio.gather(approve(s1), approve(s2))
    finally:
        await s1.close()
        await s2.close()

    successes = [r for r in (r1, r2) if r == "OK"]
    assert len(successes) == 1, f"exactly one approval should win, got {(r1, r2)}"

    async with factory() as s3:
        n_audit = (
            await s3.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "account_link_request.approved",
                    AuditLog.target_id == req_id,
                )
            )
        ).scalar_one()
    assert n_audit == 1, f"expected 1 approval audit row, got {n_audit}"


async def test_last_admin_invariant_holds_under_concurrency(
    integration_client: AsyncClient,
) -> None:
    """#20 — two concurrent disables of the last two admins can never drop
    the lab to zero active admins; at least one request is rejected."""
    del integration_client
    factory = get_session_factory()
    async with factory() as s, s.begin():
        lab = Lab(name="cc20", slug="cc20")
        s.add(lab)
        await s.flush()
        a = User(
            lab_id=lab.id,
            username="cc20_a",
            email="cc20a@r.test",
            display_name="A",
            password_hash=hash_password("Pw!2024xxxx"),  # pragma: allowlist secret
            role="lab_admin",
        )
        b = User(
            lab_id=lab.id,
            username="cc20_b",
            email="cc20b@r.test",
            display_name="B",
            password_hash=hash_password("Pw!2024xxxx"),  # pragma: allowlist secret
            role="lab_admin",
        )
        s.add_all([a, b])
        await s.flush()
        lab_id, a_id, b_id = lab.id, a.id, b.id

    s1 = factory()
    s2 = factory()

    async def disable(sess: Any, target: int, actor: int) -> str:
        try:
            await user_service.disable_user(sess, target, actor_user_id=actor, lab_id=lab_id)
            await sess.commit()
            return "OK"
        except Exception as e:
            with contextlib.suppress(Exception):
                await sess.rollback()
            return type(e).__name__

    try:
        r1, r2 = await asyncio.gather(disable(s1, a_id, b_id), disable(s2, b_id, a_id))
    finally:
        await s1.close()
        await s2.close()

    assert "OK" in (r1, r2), f"at least one disable should succeed, got {(r1, r2)}"
    assert not (r1 == "OK" and r2 == "OK"), f"both disables must not succeed, got {(r1, r2)}"

    async with factory() as s3:
        remaining = (
            await s3.execute(
                select(func.count())
                .select_from(User)
                .where(User.lab_id == lab_id, User.role == "lab_admin", User.is_active == 1)
            )
        ).scalar_one()
    assert remaining >= 1, "lab must always keep at least one active admin"
