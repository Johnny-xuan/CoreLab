from __future__ import annotations

from typing import Any, cast

import pytest
from corelab_backend.api.v1 import reservations as reservations_api
from corelab_backend.auth_dependencies import AuthenticatedUser
from corelab_backend.models import Reservation, User
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


def _user(user_id: int, *, role: str = "user") -> AuthenticatedUser:
    row = User(
        id=user_id,
        lab_id=1,
        username=f"user{user_id}",
        email=f"user{user_id}@example.test",
        display_name=f"User {user_id}",
        role=role,
    )
    return AuthenticatedUser(row)


def _reservation(*, owner_id: int = 10, server_id: int = 20) -> Reservation:
    return Reservation(id=99, user_id=owner_id, server_id=server_id)


async def test_script_log_owner_can_read() -> None:
    await reservations_api._ensure_can_read_script_log(
        cast(Any, object()),
        row=_reservation(owner_id=10),
        user=_user(10),
    )


async def test_script_log_server_admin_can_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def can_admin(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(reservations_api, "_user_can_admin_server", can_admin)
    await reservations_api._ensure_can_read_script_log(
        cast(Any, object()),
        row=_reservation(owner_id=10),
        user=_user(11),
    )


async def test_script_log_other_user_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cannot_admin(*_: Any, **__: Any) -> bool:
        return False

    monkeypatch.setattr(reservations_api, "_user_can_admin_server", cannot_admin)
    with pytest.raises(HTTPException) as exc:
        await reservations_api._ensure_can_read_script_log(
            cast(Any, object()),
            row=_reservation(owner_id=10),
            user=_user(11),
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "SCRIPT_LOG_FORBIDDEN"}
