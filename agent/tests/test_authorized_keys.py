"""``authorized_keys`` unit tests — fingerprint + capability + real Linux path."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from corelab_agent import capabilities
from corelab_agent.authorized_keys import (
    MOCK_FINGERPRINT,
    MOCK_PATH,
    PUSH_CAPABILITY_KEY,
    REVOKE_CAPABILITY_KEY,
    InvalidPublicKeyError,
    InvalidUsernameError,
    _fingerprint_for_pubkey,
    _rewrite_authorized_keys_for_push,
    _rewrite_authorized_keys_for_revoke,
    push,
    read,
    revoke,
)
from corelab_agent.linux_users import CommandResult, UserInfo

ED25519_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGugZyKstSXAh67UVPb6IjCpPj8YlGflO/Jv0aBOMOJ0"
OTHER_ED25519_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB8IuLZ9UybbIYoJOfdZBq1Q46QvR/9e2kr+7og7l7it"
)


@pytest.fixture(autouse=True)
def _reset_capabilities() -> None:
    capabilities.clear()


def _read_text(path: str) -> str:
    return Path(path).read_text()


def test_fingerprint_for_ed25519_key() -> None:
    """Computed fingerprint matches the canonical openssh `SHA256:...` form."""
    fp = _fingerprint_for_pubkey(ED25519_KEY)
    assert fp.startswith("SHA256:")
    # Reproducible across calls.
    assert _fingerprint_for_pubkey(ED25519_KEY) == fp


def test_fingerprint_for_authorized_keys_line_with_options() -> None:
    assert _fingerprint_for_pubkey(f'from="10.0.0.*" {ED25519_KEY}') == _fingerprint_for_pubkey(
        ED25519_KEY
    )


def test_fingerprint_rejects_missing_body() -> None:
    with pytest.raises(InvalidPublicKeyError):
        _fingerprint_for_pubkey("ssh-ed25519")


def test_fingerprint_rejects_invalid_base64() -> None:
    with pytest.raises(InvalidPublicKeyError):
        _fingerprint_for_pubkey("ssh-ed25519 !!!not-base64!!! comment")


def test_rewrite_push_appends_once() -> None:
    line = f"{ED25519_KEY} alice@corelab"
    existing = "# manual key\n"

    pushed = _rewrite_authorized_keys_for_push(existing, line)
    assert pushed == f"# manual key\n{line}\n"
    assert _rewrite_authorized_keys_for_push(pushed, line) == pushed
    assert (
        _rewrite_authorized_keys_for_push(
            f"{ED25519_KEY} old-comment\n",
            line,
        )
        == f"{ED25519_KEY} old-comment\n"
    )


def test_rewrite_revoke_removes_matching_key_and_corelab_comment() -> None:
    target = f"{ED25519_KEY} alice@corelab"
    other = f"{OTHER_ED25519_KEY} bob@corelab"
    fp = _fingerprint_for_pubkey(ED25519_KEY)
    existing = f"# corelab: managed key\n{target}\n# keep manual note\n{other}\n"

    rewritten, removed = _rewrite_authorized_keys_for_revoke(existing, fp)

    assert removed is True
    assert target not in rewritten
    assert "# corelab: managed key" not in rewritten
    assert "# keep manual note" in rewritten
    assert other in rewritten


async def test_push_mock_returns_sentinel_path() -> None:
    result = await push(
        linux_username="yang_lab",
        public_key=ED25519_KEY,
        label="alice@platform",
        mock_mode=True,
    )
    assert result.installed_path == MOCK_PATH
    assert result.fingerprint.startswith("SHA256:")
    # Mac mock returns the *real* fingerprint of the supplied key —
    # only the path is sentinel-replaced.
    assert result.fingerprint != MOCK_FINGERPRINT  # MOCK_FINGERPRINT is for verify only


async def test_push_real_path_installs_rewritten_authorized_keys() -> None:
    commands: list[list[str]] = []
    installed_content: list[str] = []

    async def runner(cmd: list[str], _input: bytes | None) -> CommandResult:
        commands.append(cmd)
        if cmd[2] == "/bin/cat":
            return CommandResult(returncode=0, stdout="# existing\n")
        if cmd[2] == "/usr/bin/install" and "-m" in cmd and "600" in cmd:
            installed_content.append(await asyncio.to_thread(_read_text, cmd[-2]))
        return CommandResult(returncode=0)

    result = await push(
        linux_username="yang_lab",
        public_key=ED25519_KEY,
        label="alice@corelab",
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: UserInfo(
            uid=20001,
            gid=20002,
            home_directory="/home/yang_lab",
            default_shell="/bin/bash",
        ),
        group_name_lookup=lambda _gid: "labusers",
        install_bin="/usr/bin/install",
        cat_bin="/bin/cat",
    )

    assert result.installed_path == "/home/yang_lab/.ssh/authorized_keys"
    assert result.fingerprint == _fingerprint_for_pubkey(ED25519_KEY)
    assert installed_content == [f"# existing\n{ED25519_KEY} alice@corelab\n"]
    assert commands == [
        [
            "sudo",
            "-n",
            "/usr/bin/install",
            "-m",
            "700",
            "-o",
            "yang_lab",
            "-g",
            "labusers",
            "-d",
            "/home/yang_lab/.ssh",
        ],
        ["sudo", "-n", "/bin/cat", "/home/yang_lab/.ssh/authorized_keys"],
        [
            "sudo",
            "-n",
            "/usr/bin/install",
            "-m",
            "600",
            "-o",
            "yang_lab",
            "-g",
            "labusers",
            commands[2][-2],
            "/home/yang_lab/.ssh/authorized_keys",
        ],
    ]


async def test_push_real_path_treats_missing_authorized_keys_as_empty() -> None:
    installed_content: list[str] = []

    async def runner(cmd: list[str], _input: bytes | None) -> CommandResult:
        if cmd[2] == "/bin/cat":
            return CommandResult(
                returncode=1,
                stderr="cat: /home/yang_lab/.ssh/authorized_keys: No such file or directory",
            )
        if cmd[2] == "/usr/bin/install" and "-m" in cmd and "600" in cmd:
            installed_content.append(await asyncio.to_thread(_read_text, cmd[-2]))
        return CommandResult(returncode=0)

    await push(
        linux_username="yang_lab",
        public_key=ED25519_KEY,
        label="alice@corelab",
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: UserInfo(
            uid=20001,
            gid=20002,
            home_directory="/home/yang_lab",
            default_shell="/bin/bash",
        ),
        group_name_lookup=lambda _gid: "labusers",
        install_bin="/usr/bin/install",
        cat_bin="/bin/cat",
    )

    assert installed_content == [f"{ED25519_KEY} alice@corelab\n"]


async def test_push_real_path_skips_duplicate_write() -> None:
    commands: list[list[str]] = []
    line = f"{ED25519_KEY} alice@corelab\n"

    async def runner(cmd: list[str], _input: bytes | None) -> CommandResult:
        commands.append(cmd)
        if cmd[2] == "/bin/cat":
            return CommandResult(returncode=0, stdout=line)
        return CommandResult(returncode=0)

    await push(
        linux_username="yang_lab",
        public_key=ED25519_KEY,
        label="alice@corelab",
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: UserInfo(
            uid=20001,
            gid=20002,
            home_directory="/home/yang_lab",
            default_shell="/bin/bash",
        ),
        group_name_lookup=lambda _gid: "labusers",
        install_bin="/usr/bin/install",
        cat_bin="/bin/cat",
    )

    assert len(commands) == 2


async def test_push_capability_gate() -> None:
    capabilities.set_enabled(PUSH_CAPABILITY_KEY, False)
    with pytest.raises(capabilities.CapabilityDisabledError):
        await push(
            linux_username="yang_lab",
            public_key=ED25519_KEY,
            label="alice",
            mock_mode=True,
        )


async def test_push_rejects_invalid_username() -> None:
    with pytest.raises(InvalidUsernameError):
        await push(
            linux_username="bad;rm -rf",
            public_key=ED25519_KEY,
            label="alice",
            mock_mode=True,
        )


async def test_push_rejects_multiline_label() -> None:
    with pytest.raises(InvalidPublicKeyError):
        await push(
            linux_username="yang_lab",
            public_key=ED25519_KEY,
            label="alice\nssh-rsa BAD",
            mock_mode=True,
        )


async def test_revoke_mock_returns_revoked_true() -> None:
    result = await revoke(
        linux_username="yang_lab",
        fingerprint="SHA256:abc",
        mock_mode=True,
    )
    assert result.revoked is True
    assert result.mock_warning is not None


async def test_revoke_real_path_removes_matching_key() -> None:
    target = f"{ED25519_KEY} alice@corelab"
    other = f"{OTHER_ED25519_KEY} bob@corelab"
    commands: list[list[str]] = []
    installed_content: list[str] = []

    async def runner(cmd: list[str], _input: bytes | None) -> CommandResult:
        commands.append(cmd)
        if cmd[2] == "/bin/cat":
            return CommandResult(returncode=0, stdout=f"# corelab: managed\n{target}\n{other}\n")
        if cmd[2] == "/usr/bin/install":
            installed_content.append(await asyncio.to_thread(_read_text, cmd[-2]))
        return CommandResult(returncode=0)

    result = await revoke(
        linux_username="yang_lab",
        fingerprint=_fingerprint_for_pubkey(ED25519_KEY),
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: UserInfo(
            uid=20001,
            gid=20002,
            home_directory="/home/yang_lab",
            default_shell="/bin/bash",
        ),
        group_name_lookup=lambda _gid: "labusers",
        install_bin="/usr/bin/install",
        cat_bin="/bin/cat",
    )

    assert result.revoked is True
    assert installed_content == [f"{other}\n"]
    assert commands[0] == ["sudo", "-n", "/bin/cat", "/home/yang_lab/.ssh/authorized_keys"]
    assert commands[1][-1] == "/home/yang_lab/.ssh/authorized_keys"


async def test_revoke_real_path_returns_false_for_missing_key() -> None:
    commands: list[list[str]] = []

    async def runner(cmd: list[str], _input: bytes | None) -> CommandResult:
        commands.append(cmd)
        return CommandResult(returncode=0, stdout=f"{OTHER_ED25519_KEY} bob@corelab\n")

    result = await revoke(
        linux_username="yang_lab",
        fingerprint=_fingerprint_for_pubkey(ED25519_KEY),
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: UserInfo(
            uid=20001,
            gid=20002,
            home_directory="/home/yang_lab",
            default_shell="/bin/bash",
        ),
        group_name_lookup=lambda _gid: "labusers",
        install_bin="/usr/bin/install",
        cat_bin="/bin/cat",
    )

    assert result.revoked is False
    assert len(commands) == 1


async def test_revoke_capability_gate() -> None:
    capabilities.set_enabled(REVOKE_CAPABILITY_KEY, False)
    with pytest.raises(capabilities.CapabilityDisabledError):
        await revoke(
            linux_username="yang_lab",
            fingerprint="SHA256:abc",
            mock_mode=True,
        )


async def test_read_mock_returns_no_raw_keys() -> None:
    result = await read(linux_username="yang_lab", mock_mode=True)

    assert result.authorized_keys_path == MOCK_PATH
    assert result.keys == []
    assert result.line_count == 0
    assert result.mock_warning is not None


async def test_read_real_path_summarizes_fingerprints_only() -> None:
    commands: list[list[str]] = []
    host_text = (
        "# managed by hand\n"
        f"{ED25519_KEY} corelab:user=44\n"
        f'from="10.0.0.*" {OTHER_ED25519_KEY} workstation key\n'
        "not-a-valid-key\n"
    )

    async def runner(cmd: list[str], _input: bytes | None) -> CommandResult:
        commands.append(cmd)
        return CommandResult(returncode=0, stdout=host_text)

    result = await read(
        linux_username="yang_lab",
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: UserInfo(
            uid=20001,
            gid=20002,
            home_directory="/home/yang_lab",
            default_shell="/bin/bash",
        ),
        cat_bin="/bin/cat",
    )

    assert result.authorized_keys_path == "/home/yang_lab/.ssh/authorized_keys"
    assert result.line_count == 3
    assert result.invalid_line_count == 1
    assert [key.fingerprint_sha256 for key in result.keys] == [
        _fingerprint_for_pubkey(ED25519_KEY),
        _fingerprint_for_pubkey(OTHER_ED25519_KEY),
    ]
    assert result.keys[0].comment == "corelab:user=44"
    assert result.keys[1].comment == "workstation key"
    assert commands == [["sudo", "-n", "/bin/cat", "/home/yang_lab/.ssh/authorized_keys"]]
