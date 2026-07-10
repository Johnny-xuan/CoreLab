"""``authorized_keys`` mutation handlers (push + revoke).

Linux: compute the target ``authorized_keys`` content in-process, then
use a narrow sudoers whitelist for ``install`` and ``cat``. The pushed
line is ``<public-key> <label>``: we keep the key payload exactly as the
backend sent it and append the platform-supplied ``label`` so operators
can grep the file to see which CoreLab user owns which line.

Revoke removes any line whose computed fingerprint matches the
caller-supplied one. The fingerprint is computed in-process (no
keypair material leaves agent memory) and compared bytewise — no regex
shenanigans on the authorized_keys file.

Mac in ``mock_mode``: doesn't touch any filesystem; returns a sentinel
``installed_path='/dev/null/mock-mac'`` + ``fingerprint='SHA256:MOCK-MAC-NO-WRITE'``
so prod-mode regressions hitting the mock path stand out.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import grp
import hashlib
import os
import pwd
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from . import capabilities
from .linux_users import CommandResult, UserInfo
from .logging_setup import get_logger

_log = get_logger("corelab.agent.authorized_keys")

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_HOME_RE = re.compile(r"^/home/[A-Za-z0-9._-]+$")
PUSH_CAPABILITY_KEY = "ssh.push_authorized_key"
REVOKE_CAPABILITY_KEY = "ssh.remove_authorized_key"
MOCK_PATH = "/dev/null/mock-mac"
MOCK_FINGERPRINT = "SHA256:MOCK-MAC-NO-WRITE"
MOCK_WARNING = "platform=Darwin, no real authorized_keys mutation"
AUTHKEYS_TMP_PREFIX = "corelab-authkeys-"  # pragma: allowlist secret

CommandRunner = Callable[[list[str], bytes | None], Awaitable[CommandResult]]


class AuthorizedKeysError(Exception):
    pass


class InvalidUsernameError(AuthorizedKeysError):
    pass


class InvalidPublicKeyError(AuthorizedKeysError):
    pass


class UserNotFoundError(AuthorizedKeysError):
    pass


class UnsupportedHomeDirectoryError(AuthorizedKeysError):
    pass


@dataclass(frozen=True, slots=True)
class PushResult:
    installed_path: str
    fingerprint: str
    mock_warning: str | None = None


@dataclass(frozen=True, slots=True)
class RevokeResult:
    revoked: bool
    mock_warning: str | None = None


@dataclass(frozen=True, slots=True)
class ReadKeyEntry:
    line_number: int
    fingerprint_sha256: str
    key_type: str | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class ReadResult:
    authorized_keys_path: str
    line_count: int
    invalid_line_count: int
    keys: list[ReadKeyEntry]
    mock_warning: str | None = None


def _validate_username(linux_username: str) -> None:
    if not _USERNAME_RE.fullmatch(linux_username):
        raise InvalidUsernameError(f"invalid linux_username {linux_username!r}")


def _validate_single_line(value: str, *, field: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvalidPublicKeyError(f"{field} is empty")
    if any(char in stripped for char in ("\n", "\r", "\x00")):
        raise InvalidPublicKeyError(f"{field} must be a single line")
    return stripped


def _is_key_type(part: str) -> bool:
    return (
        part.startswith("ssh-")
        or part.startswith("ecdsa-")
        or part.startswith("sk-")
        or part.startswith("rsa-sha2-")
    )


def _fingerprint_for_pubkey(public_key: str) -> str:
    """Compute the OpenSSH SHA256 fingerprint of a public key string.

    Accepts ``ssh-ed25519 BASE64 [comment]`` form and ignores anything
    after the base64 payload (label / comment). Lines with leading
    authorized_keys options are also handled when the key type appears as
    a whitespace-delimited token. Returns ``SHA256:<base64-unpadded>``.
    """
    parts = public_key.strip().split()
    body_index: int | None = None
    for index, part in enumerate(parts[:-1]):
        if _is_key_type(part):
            body_index = index + 1
            break
    if body_index is None:
        raise InvalidPublicKeyError("public_key missing base64 body")
    try:
        raw = base64.b64decode(parts[body_index], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidPublicKeyError(f"public_key base64 decode failed: {exc}") from exc
    digest = hashlib.sha256(raw).digest()
    encoded = base64.b64encode(digest).rstrip(b"=").decode("ascii")
    return f"SHA256:{encoded}"


def _summarize_authorized_key_line(line: str, *, line_number: int) -> ReadKeyEntry:
    parts = line.strip().split()
    key_type_index: int | None = None
    for index, part in enumerate(parts[:-1]):
        if _is_key_type(part):
            key_type_index = index
            break
    if key_type_index is None:
        raise InvalidPublicKeyError("authorized_keys line missing key type")
    comment_parts = parts[key_type_index + 2 :]
    return ReadKeyEntry(
        line_number=line_number,
        fingerprint_sha256=_fingerprint_for_pubkey(line),
        key_type=parts[key_type_index],
        comment=" ".join(comment_parts) if comment_parts else None,
    )


def _summarize_authorized_keys(text: str) -> tuple[list[ReadKeyEntry], int, int]:
    keys: list[ReadKeyEntry] = []
    key_line_count = 0
    invalid_line_count = 0
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key_line_count += 1
        try:
            keys.append(_summarize_authorized_key_line(stripped, line_number=line_number))
        except InvalidPublicKeyError:
            invalid_line_count += 1
    return keys, key_line_count, invalid_line_count


def _authorized_key_line(public_key: str, label: str) -> str:
    key = _validate_single_line(public_key, field="public_key")
    comment = _validate_single_line(label, field="label")
    _fingerprint_for_pubkey(key)
    return f"{key} {comment}"


def _join_authorized_key_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _rewrite_authorized_keys_for_push(existing_text: str, line: str) -> str:
    lines = existing_text.splitlines()
    target_fingerprint = _fingerprint_for_pubkey(line)
    for existing_line in lines:
        stripped = existing_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            existing_fingerprint = _fingerprint_for_pubkey(stripped)
        except InvalidPublicKeyError:
            continue
        if existing_fingerprint == target_fingerprint:
            return _join_authorized_key_lines(lines)
    return _join_authorized_key_lines([*lines, line])


def _rewrite_authorized_keys_for_revoke(existing_text: str, fingerprint: str) -> tuple[str, bool]:
    lines = existing_text.splitlines()
    kept: list[str] = []
    removed = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            try:
                line_fingerprint = _fingerprint_for_pubkey(stripped)
            except InvalidPublicKeyError:
                line_fingerprint = None
            if line_fingerprint == fingerprint:
                removed = True
                if kept and kept[-1].lstrip().startswith("# corelab"):
                    kept.pop()
                continue
        kept.append(line)
    return _join_authorized_key_lines(kept), removed


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


def _group_name_for_gid(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def _authorized_key_paths(info: UserInfo) -> tuple[str, str]:
    home = Path(info.home_directory)
    home_text = str(home)
    if not home.is_absolute() or ".." in home.parts or _HOME_RE.fullmatch(home_text) is None:
        raise UnsupportedHomeDirectoryError(
            f"authorized_keys mutation only supports /home/<user> homes, got {home_text!r}"
        )
    ssh_dir = home / ".ssh"
    return str(ssh_dir), str(ssh_dir / "authorized_keys")


def _missing_file(result: CommandResult) -> bool:
    text = f"{result.stderr}\n{result.stdout}".lower()
    return "no such file" in text or "no such file or directory" in text


def _command_error(operation: str, result: CommandResult) -> AuthorizedKeysError:
    detail = result.stderr.strip() or result.stdout.strip() or f"{operation}_failed"
    return AuthorizedKeysError(detail)


async def _run(
    cmd: list[str],
    *,
    runner: CommandRunner | None,
    input_bytes: bytes | None = None,
) -> CommandResult:
    if runner is not None:
        return await runner(cmd, input_bytes)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=input_bytes)
    return CommandResult(
        returncode=proc.returncode or 0,
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
    )


async def _ensure_ssh_dir(
    *,
    ssh_dir: str,
    linux_username: str,
    group_name: str,
    install_bin: str,
    runner: CommandRunner | None,
) -> None:
    result = await _run(
        [
            "sudo",
            "-n",
            install_bin,
            "-m",
            "700",
            "-o",
            linux_username,
            "-g",
            group_name,
            "-d",
            ssh_dir,
        ],
        runner=runner,
    )
    if result.returncode != 0:
        raise _command_error("authorized_keys_ensure_dir", result)


async def _read_authorized_keys(
    *,
    authorized_keys_path: str,
    cat_bin: str,
    runner: CommandRunner | None,
) -> str:
    result = await _run(["sudo", "-n", cat_bin, authorized_keys_path], runner=runner)
    if result.returncode == 0:
        return result.stdout
    if _missing_file(result):
        return ""
    raise _command_error("authorized_keys_read", result)


async def _install_authorized_keys(
    *,
    content: str,
    authorized_keys_path: str,
    linux_username: str,
    group_name: str,
    install_bin: str,
    runner: CommandRunner | None,
) -> None:
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=AUTHKEYS_TMP_PREFIX,
            dir="/tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content.encode("utf-8"))
            temp_name = temp_file.name
        result = await _run(
            [
                "sudo",
                "-n",
                install_bin,
                "-m",
                "600",
                "-o",
                linux_username,
                "-g",
                group_name,
                temp_name,
                authorized_keys_path,
            ],
            runner=runner,
        )
        if result.returncode != 0:
            raise _command_error("authorized_keys_install", result)
    finally:
        if temp_name:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)


async def push(
    *,
    linux_username: str,
    public_key: str,
    label: str,
    mock_mode: bool,
    runner: CommandRunner | None = None,
    lookup_user: Callable[[str], UserInfo | None] = _lookup_user,
    group_name_lookup: Callable[[int], str] = _group_name_for_gid,
    install_bin: str | None = None,
    cat_bin: str | None = None,
) -> PushResult:
    capabilities.require_enabled(PUSH_CAPABILITY_KEY)
    _validate_username(linux_username)
    fp = _fingerprint_for_pubkey(public_key)
    line = _authorized_key_line(public_key, label)

    if mock_mode:
        _log.warning(
            "authorized_keys.mock_push",
            linux_username=linux_username,
            label=label,
            fingerprint=fp,
            note="mock_mode — no filesystem write",
        )
        return PushResult(installed_path=MOCK_PATH, fingerprint=fp, mock_warning=MOCK_WARNING)

    user_info = lookup_user(linux_username)
    if user_info is None:
        raise UserNotFoundError(f"linux user {linux_username!r} not found")
    group_name = group_name_lookup(user_info.gid)
    ssh_dir, authorized_keys_path = _authorized_key_paths(user_info)
    install = install_bin or shutil.which("install") or "/usr/bin/install"
    cat = cat_bin or shutil.which("cat") or "/usr/bin/cat"

    await _ensure_ssh_dir(
        ssh_dir=ssh_dir,
        linux_username=linux_username,
        group_name=group_name,
        install_bin=install,
        runner=runner,
    )
    existing = await _read_authorized_keys(
        authorized_keys_path=authorized_keys_path,
        cat_bin=cat,
        runner=runner,
    )
    desired = _rewrite_authorized_keys_for_push(existing, line)
    if desired != existing:
        await _install_authorized_keys(
            content=desired,
            authorized_keys_path=authorized_keys_path,
            linux_username=linux_username,
            group_name=group_name,
            install_bin=install,
            runner=runner,
        )
    return PushResult(installed_path=authorized_keys_path, fingerprint=fp)


async def revoke(
    *,
    linux_username: str,
    fingerprint: str,
    mock_mode: bool,
    runner: CommandRunner | None = None,
    lookup_user: Callable[[str], UserInfo | None] = _lookup_user,
    group_name_lookup: Callable[[int], str] = _group_name_for_gid,
    install_bin: str | None = None,
    cat_bin: str | None = None,
) -> RevokeResult:
    capabilities.require_enabled(REVOKE_CAPABILITY_KEY)
    _validate_username(linux_username)

    if mock_mode:
        _log.warning(
            "authorized_keys.mock_revoke",
            linux_username=linux_username,
            fingerprint=fingerprint,
            note="mock_mode — no filesystem mutation",
        )
        return RevokeResult(revoked=True, mock_warning=MOCK_WARNING)

    user_info = lookup_user(linux_username)
    if user_info is None:
        return RevokeResult(revoked=False)
    group_name = group_name_lookup(user_info.gid)
    _, authorized_keys_path = _authorized_key_paths(user_info)
    install = install_bin or shutil.which("install") or "/usr/bin/install"
    cat = cat_bin or shutil.which("cat") or "/usr/bin/cat"

    existing = await _read_authorized_keys(
        authorized_keys_path=authorized_keys_path,
        cat_bin=cat,
        runner=runner,
    )
    desired, removed = _rewrite_authorized_keys_for_revoke(existing, fingerprint)
    if removed and desired != existing:
        await _install_authorized_keys(
            content=desired,
            authorized_keys_path=authorized_keys_path,
            linux_username=linux_username,
            group_name=group_name,
            install_bin=install,
            runner=runner,
        )
    return RevokeResult(revoked=removed)


async def read(
    *,
    linux_username: str,
    mock_mode: bool,
    runner: CommandRunner | None = None,
    lookup_user: Callable[[str], UserInfo | None] = _lookup_user,
    cat_bin: str | None = None,
) -> ReadResult:
    _validate_username(linux_username)

    if mock_mode:
        _log.warning(
            "authorized_keys.mock_read",
            linux_username=linux_username,
            note="mock_mode — no filesystem read",
        )
        return ReadResult(
            authorized_keys_path=MOCK_PATH,
            line_count=0,
            invalid_line_count=0,
            keys=[],
            mock_warning=MOCK_WARNING,
        )

    user_info = lookup_user(linux_username)
    if user_info is None:
        raise UserNotFoundError(f"linux user {linux_username!r} not found")
    _, authorized_keys_path = _authorized_key_paths(user_info)
    cat = cat_bin or shutil.which("cat") or "/usr/bin/cat"

    existing = await _read_authorized_keys(
        authorized_keys_path=authorized_keys_path,
        cat_bin=cat,
        runner=runner,
    )
    keys, line_count, invalid_line_count = _summarize_authorized_keys(existing)
    return ReadResult(
        authorized_keys_path=authorized_keys_path,
        line_count=line_count,
        invalid_line_count=invalid_line_count,
        keys=keys,
    )
