"""account_scanner unit tests — human-account filter + mock mode.

The real ``pwd.getpwall()`` walk is monkeypatched with synthetic
``struct_passwd`` rows so the filter rules are pinned regardless of
the machine running the suite.
"""

from __future__ import annotations

import pwd

import pytest
from corelab_agent import account_scanner
from corelab_agent.config import MockAccountSpec


def _pw(name: str, uid: int, shell: str = "/bin/bash") -> pwd.struct_passwd:
    return pwd.struct_passwd((name, "x", uid, uid, "", f"/home/{name}", shell))


class TestScanFilter:
    @pytest.fixture(autouse=True)
    def _patch_getpwall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            _pw("root", 0),
            _pw("daemon", 1, shell="/usr/sbin/nologin"),
            _pw("sshd", 105, shell="/usr/sbin/nologin"),
            _pw("alice", 1000),
            _pw("bob", 1001, shell="/bin/zsh"),
            _pw("svc-runner", 1002, shell="/usr/sbin/nologin"),
            _pw("ftp-stub", 1003, shell="/bin/false"),
            _pw("nobody", 65534, shell="/usr/sbin/nologin"),
            _pw("x" * 33, 1004),  # exceeds the 32-char protocol cap
        ]
        monkeypatch.setattr(pwd, "getpwall", lambda: rows)

    def test_keeps_human_accounts_only(self) -> None:
        report = account_scanner.scan(mock_mode=False)
        names = [e.linux_username for e in report.entries]
        assert names == ["alice", "bob"]
        assert report.mock is False

    def test_entry_fields_round_trip(self) -> None:
        report = account_scanner.scan(mock_mode=False)
        alice = report.entries[0]
        assert alice.uid == 1000
        assert alice.home_directory == "/home/alice"
        assert alice.default_shell == "/bin/bash"


class TestMockMode:
    def test_mock_returns_fixed_fake_accounts(self) -> None:
        report = account_scanner.scan(mock_mode=True)
        assert report.mock is True
        assert [e.linux_username for e in report.entries] == ["grace", "heidi"]

    def test_mock_never_touches_getpwall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom() -> list[pwd.struct_passwd]:
            raise AssertionError("mock mode must not read /etc/passwd")

        monkeypatch.setattr(pwd, "getpwall", _boom)
        report = account_scanner.scan(mock_mode=True)
        assert len(report.entries) == 2

    def test_mock_accounts_override_the_fixed_pair(self) -> None:
        accounts = [
            MockAccountSpec(linux_username="alice", uid=1001, gid=1001),
            MockAccountSpec(
                linux_username="bob",
                uid=1002,
                gid=1002,
                home_directory="/data/bob",
                default_shell="/bin/zsh",
            ),
        ]
        report = account_scanner.scan(mock_mode=True, mock_accounts=accounts)
        assert report.mock is True
        assert [e.linux_username for e in report.entries] == ["alice", "bob"]
        alice, bob = report.entries
        assert alice.uid == 1001
        assert alice.home_directory == "/home/alice"  # synthesized default
        assert bob.home_directory == "/data/bob"
        assert bob.default_shell == "/bin/zsh"

    def test_empty_mock_accounts_keeps_grace_heidi_default(self) -> None:
        report = account_scanner.scan(mock_mode=True, mock_accounts=[])
        assert [e.linux_username for e in report.entries] == ["grace", "heidi"]


class TestScanFailure:
    def test_scan_failure_returns_empty_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom() -> list[pwd.struct_passwd]:
            raise OSError("nss is on fire")

        monkeypatch.setattr(pwd, "getpwall", _boom)
        report = account_scanner.scan(mock_mode=False)
        assert report.entries == []
