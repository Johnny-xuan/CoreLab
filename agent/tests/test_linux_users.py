"""Linux user lifecycle handler tests."""

from __future__ import annotations

import pytest
from corelab_agent import capabilities
from corelab_agent.linux_users import (
    USERADD_CAPABILITY_KEY,
    USERDEL_CAPABILITY_KEY,
    CommandResult,
    InvalidLinuxUserPayloadError,
    UserInfo,
    useradd,
    userdel,
)


@pytest.fixture(autouse=True)
def _reset_capabilities() -> None:
    capabilities.clear()


async def test_useradd_mock_returns_account_shape() -> None:
    result = await useradd(linux_username="alice_lab", mock_mode=True)
    assert result.ok is True
    assert result.uid == 20000
    assert result.gid == 20000
    assert result.home_directory == "/home/alice_lab"
    assert result.default_shell == "/bin/bash"
    assert result.mock_warning is not None


async def test_useradd_capability_gate() -> None:
    capabilities.set_enabled(USERADD_CAPABILITY_KEY, False)
    with pytest.raises(capabilities.CapabilityDisabledError):
        await useradd(linux_username="alice_lab", mock_mode=True)


async def test_useradd_rejects_bad_payload() -> None:
    with pytest.raises(InvalidLinuxUserPayloadError):
        await useradd(linux_username="bad;name", mock_mode=True)
    with pytest.raises(InvalidLinuxUserPayloadError):
        await useradd(linux_username="alice_lab", uid=999, mock_mode=True)
    with pytest.raises(InvalidLinuxUserPayloadError):
        await useradd(linux_username="alice_lab", default_shell="bash -c nope", mock_mode=True)


async def test_useradd_real_path_builds_sudo_command_and_returns_lookup() -> None:
    commands: list[list[str]] = []
    created = False

    async def runner(cmd: list[str]) -> CommandResult:
        nonlocal created
        commands.append(cmd)
        created = True
        return CommandResult(returncode=0)

    def lookup(name: str) -> UserInfo | None:
        if created:
            return UserInfo(
                uid=20001,
                gid=20001,
                home_directory=f"/home/{name}",
                default_shell="/bin/bash",
            )
        return None

    result = await useradd(
        linux_username="alice_lab",
        uid=20001,
        gid=20001,
        mock_mode=False,
        runner=runner,
        lookup_user=lookup,
    )

    assert result.ok is True
    assert result.uid == 20001
    assert commands == [
        [
            "sudo",
            "-n",
            "/usr/sbin/useradd",
            "-m",
            "-s",
            "/bin/bash",
            "-d",
            "/home/alice_lab",
            "-u",
            "20001",
            "-g",
            "20001",
            "alice_lab",
        ]
    ]


async def test_useradd_existing_user_is_idempotent() -> None:
    async def runner(_cmd: list[str]) -> CommandResult:
        raise AssertionError("runner should not be called for existing user")

    result = await useradd(
        linux_username="alice_lab",
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: UserInfo(
            uid=20002,
            gid=20002,
            home_directory="/home/alice_lab",
            default_shell="/bin/zsh",
        ),
    )

    assert result.ok is True
    assert result.default_shell == "/bin/zsh"


async def test_useradd_failure_returns_error() -> None:
    async def runner(_cmd: list[str]) -> CommandResult:
        return CommandResult(returncode=9, stderr="useradd failed")

    result = await useradd(
        linux_username="alice_lab",
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: None,
    )

    assert result.ok is False
    assert result.error == "useradd failed"


async def test_userdel_mock_and_capability_gate() -> None:
    result = await userdel(linux_username="alice_lab", remove_home=False, mock_mode=True)
    assert result.ok is True
    assert result.mock_warning is not None

    capabilities.set_enabled(USERDEL_CAPABILITY_KEY, False)
    with pytest.raises(capabilities.CapabilityDisabledError):
        await userdel(linux_username="alice_lab", remove_home=False, mock_mode=True)


async def test_userdel_real_path_builds_sudo_command() -> None:
    commands: list[list[str]] = []

    async def runner(cmd: list[str]) -> CommandResult:
        commands.append(cmd)
        return CommandResult(returncode=0)

    result = await userdel(
        linux_username="alice_lab",
        remove_home=True,
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: UserInfo(
            uid=20002,
            gid=20002,
            home_directory="/home/alice_lab",
            default_shell="/bin/bash",
        ),
    )

    assert result.ok is True
    assert commands == [["sudo", "-n", "/usr/sbin/userdel", "-r", "alice_lab"]]


async def test_userdel_missing_user_is_idempotent() -> None:
    async def runner(_cmd: list[str]) -> CommandResult:
        raise AssertionError("runner should not be called for missing user")

    result = await userdel(
        linux_username="alice_lab",
        remove_home=True,
        mock_mode=False,
        runner=runner,
        lookup_user=lambda _name: None,
    )

    assert result.ok is True
