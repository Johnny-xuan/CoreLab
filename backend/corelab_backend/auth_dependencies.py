"""FastAPI dependencies: bearer-token parsing, current-user lookup, RBAC.

The dependency boundary is where 401 / 403 first surface — anything
deeper (services) stays HTTP-unaware.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import User
from .security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


class AuthenticatedUser:
    """Snapshot of the authenticated user attached to the request."""

    __slots__ = ("user",)

    def __init__(self, user: User) -> None:
        self.user = user

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def lab_id(self) -> int:
        return self.user.lab_id

    @property
    def role(self) -> str:
        return self.user.role


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token: {exc.__class__.__name__}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token missing sub"
        ) from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found or disabled"
        )
    # Attach to request.state for audit middleware / loggers.
    request.state.user_id = user.id
    request.state.lab_id = user.lab_id
    return AuthenticatedUser(user)


def require_role(
    *allowed: str,
) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """Dependency factory that 403s unless the current user has one of ``allowed``."""

    allowed_set = frozenset(allowed)

    async def _checker(
        current: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if current.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{current.role}' is not allowed (need one of: {sorted(allowed_set)})",
            )
        return current

    return _checker


def extract_request_context(request: Request) -> tuple[str | None, str | None]:
    """Returns ``(client_ip, user_agent)`` for audit-log writes."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


async def assert_server_admin(
    session: AsyncSession,
    *,
    user: AuthenticatedUser,
    server_id: int,
) -> None:
    """Phase K K-fence — verify the caller can write to ``server_id``.

    lab_admin always passes (implicit grant of every server in their
    lab). Otherwise we require an active row in ``server_admin_grant``
    for this user and server. Raises 403 if the gate fails — call this
    at the top of any service path that mutates per-server state on
    behalf of a server admin.
    """
    if user.role == "lab_admin":
        return
    from sqlalchemy import select as _sel

    from .models import ServerAdminGrant

    result = await session.execute(
        _sel(ServerAdminGrant).where(
            ServerAdminGrant.user_id == user.id,
            ServerAdminGrant.server_id == server_id,
            ServerAdminGrant.is_active == 1,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not_server_admin",
        )


async def list_granted_server_ids(session: AsyncSession, *, user: AuthenticatedUser) -> list[int]:
    """Server IDs this caller can manage. lab_admin gets every server in
    their lab; otherwise returns active server_admin_grant rows."""
    from sqlalchemy import select as _sel

    from .models import Server, ServerAdminGrant

    if user.role == "lab_admin":
        result = await session.execute(_sel(Server.id).where(Server.lab_id == user.lab_id))
        return [int(x) for x in result.scalars().all()]
    result = await session.execute(
        _sel(ServerAdminGrant.server_id).where(
            ServerAdminGrant.user_id == user.id,
            ServerAdminGrant.is_active == 1,
        )
    )
    return [int(x) for x in result.scalars().all()]
