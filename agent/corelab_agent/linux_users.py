"""Linux account lifecycle RPC handlers.

The real path shells out through sudoers using absolute command paths.
The agent still validates usernames and optional paths before subprocess
creation so a backend bug cannot smuggle extra flags or shell syntax into
host-level commands.
"""

from __future__ import annotations

import asyncio
import pwd
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from . import capabilities
from .logging_setup import get_logger

log = get_logger("corelab.agent.linux_users")

USERADD_CAPABILITY_KEY = "linux.useradd"
USERDEL_CAPABILITY_KEY = "linux.userdel"
MOCK_WARNING = "platform=Darwin, no real Linux user mutation"

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_ABS_PATH_RE = re.compile(r"^/[A-Za-z0-9._/+:-]+$")
_MIN_UID = 1000


class LinuxUserError(Exception):
    pass


class InvalidLinuxUserPayloadError(LinuxUserError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class UserInfo:
    uid: int
    gid: int
    home_directory: str
    default_shell: str


@dataclass(frozen=True, slots=True)
class UseraddResult:
    ok: bool
    uid: int | None = None
    gid: int | None = None
    home_directory: str | None = None
    default_shell: str | None = None
    error: str | None = None
    mock_warning: str | None = None


@dataclass(frozen=True, slots=True)
class UserdelResult:
    ok: bool
    error: str | None = None
    mock_warning: str | None = None


def _validate_username(linux_username: str) -> None:
    if not _USERNAME_RE.fullmatch(linux_username):
        raise InvalidLinuxUserPayloadError(f"invalid linux_username {linux_username!r}")


def _validate_optional_abs_path(value: str | None, *, field: str) -> None:
    if value is not None and not _ABS_PATH_RE.fullmatch(value):
        raise InvalidLinuxUserPayloadError(f"invalid {field} {value!r}")


def _validate_id(value: int | None, *, field: str) -> None:
    if value is not None and value < _MIN_UID:
        raise InvalidLinuxUserPayloadError(f"{field} must be >= {_MIN_UID}")


def _lookup_user(linux_username: str) -> UserInfo | None:
    try:
        info = pwd.getpwnam(linux_username)
    except KeyError:
        return None
    return UserInfo(
        uid=info.pw_uid,
        gid=info.pw_gid,
        home_directory=info.pw_dir,
        default_shell=info.pw_shell,
    )


def _result_from_info(info: UserInfo) -> UseraddResult:
    return UseraddResult(
        ok=True,
        uid=info.uid,
        gid=info.gid,
        home_directory=info.home_directory,
        default_shell=info.default_shell,
    )


async def useradd(
    *,
    linux_username: str,
    uid: int | None = None,
    gid: int | None = None,
    home_directory: str | None = None,
    default_shell: str | None = None,
    mock_mode: bool,
    runner: Callable[[list[str]], Awaitable[CommandResult]] | None = None,
    lookup_user: Callable[[str], UserInfo | None] = _lookup_user,
) -> UseraddResult:
    capabilities.require_enabled(USERADD_CAPABILITY_KEY)
    _validate_username(linux_username)
    _validate_id(uid, field="uid")
    _validate_id(gid, field="gid")
    _validate_optional_abs_path(home_directory, field="home_directory")
    _validate_optional_abs_path(default_shell, field="default_shell")

    shell = default_shell or "/bin/bash"
    home = home_directory or f"/home/{linux_username}"

    if mock_mode:
        return UseraddResult(
            ok=True,
            uid=uid or 20000,
            gid=gid or uid or 20000,
            home_directory=home,
            default_shell=shell,
            mock_warning=MOCK_WARNING,
        )

    existing = lookup_user(linux_username)
    if existing is not None:
        return _result_from_info(existing)

    useradd_bin = shutil.which("useradd") or "/usr/sbin/useradd"
    cmd = ["sudo", "-n", useradd_bin, "-m", "-s", shell, "-d", home]
    if uid is not None:
        cmd.extend(["-u", str(uid)])
    if gid is not None:
        cmd.extend(["-g", str(gid)])
    cmd.append(linux_username)

    result = await _run(cmd, runner=runner)
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "useradd_failed"
        log.warning("linux_user.useradd_failed", linux_username=linux_username, error=error)
        return UseraddResult(ok=False, error=error)

    created = lookup_user(linux_username)
    if created is None:
        return UseraddResult(ok=False, error="useradd_succeeded_but_user_not_found")
    return _result_from_info(created)


async def userdel(
    *,
    linux_username: str,
    remove_home: bool,
    mock_mode: bool,
    runner: Callable[[list[str]], Awaitable[CommandResult]] | None = None,
    lookup_user: Callable[[str], UserInfo | None] = _lookup_user,
) -> UserdelResult:
    capabilities.require_enabled(USERDEL_CAPABILITY_KEY)
    _validate_username(linux_username)

    if mock_mode:
        return UserdelResult(ok=True, mock_warning=MOCK_WARNING)

    if lookup_user(linux_username) is None:
        return UserdelResult(ok=True)

    userdel_bin = shutil.which("userdel") or "/usr/sbin/userdel"
    cmd = ["sudo", "-n", userdel_bin]
    if remove_home:
        cmd.append("-r")
    cmd.append(linux_username)
    result = await _run(cmd, runner=runner)
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "userdel_failed"
        log.warning("linux_user.userdel_failed", linux_username=linux_username, error=error)
        return UserdelResult(ok=False, error=error)
    return UserdelResult(ok=True)


async def _run(
    cmd: list[str],
    *,
    runner: Callable[[list[str]], Awaitable[CommandResult]] | None,
) -> CommandResult:
    if runner is not None:
        return await runner(cmd)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return CommandResult(
        returncode=proc.returncode or 0,
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
    )
