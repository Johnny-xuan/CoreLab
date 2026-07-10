"""Human-account discovery — the ``discovered_scan`` provenance source.

Once per WS connection the agent snapshots the host's human accounts
and pushes an ``agent.account_scan.report`` frame. The backend upserts
unknown usernames as ``physical_account.source='discovered_scan'`` so
lab admins (and the ClaimAccount wizard) can map pre-existing Linux
users to CoreLab identities without typing them in by hand.

Linux: walk ``pwd.getpwall()`` and keep human accounts only —
UID >= 1000 (Debian/Ubuntu/RHEL convention for people), skip the
``nobody`` sentinel (65534) and accounts whose shell is a
``nologin``/``false`` stub.

Mac in ``mock_mode``: returns two fixed fake accounts (``grace`` /
``heidi``) so end-to-end tests can watch the full pipeline land in the
physical_account table without leaking the dev machine's real users.
"""

from __future__ import annotations

import pwd
from typing import TYPE_CHECKING

from corelab_protocol import AccountScanEntry, AccountScanReport

from .logging_setup import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .config import MockAccountSpec

_log = get_logger("corelab.agent.account_scanner")

# People start at 1000 on every mainstream distro; 65534 is "nobody".
_MIN_HUMAN_UID = 1000
_NOBODY_UID = 65534
_STUB_SHELLS = ("nologin", "false")

_MOCK_ENTRIES = [
    AccountScanEntry(
        linux_username="grace",
        uid=1001,
        gid=1001,
        home_directory="/home/grace",
        default_shell="/bin/bash",
    ),
    AccountScanEntry(
        linux_username="heidi",
        uid=1002,
        gid=1002,
        home_directory="/home/heidi",
        default_shell="/bin/zsh",
    ),
]


def _is_human(entry: pwd.struct_passwd) -> bool:
    if entry.pw_uid < _MIN_HUMAN_UID or entry.pw_uid == _NOBODY_UID:
        return False
    # Protocol caps linux_username at 32 chars (Linux useradd's own
    # limit) — skip rather than crash the whole scan on an oddball.
    if not entry.pw_name or len(entry.pw_name) > 32:
        return False
    shell = entry.pw_shell.rsplit("/", maxsplit=1)[-1]
    return shell not in _STUB_SHELLS


def scan(
    *,
    mock_mode: bool,
    mock_accounts: Sequence[MockAccountSpec] | None = None,
) -> AccountScanReport:
    """Snapshot human accounts on this host.

    Never raises — a scan failure must not take down the connection;
    callers get an empty report and the warning lands in the log.

    In ``mock_mode`` with ``mock_accounts`` provided (e.g. from the demo
    launcher), report exactly those accounts so the scan *confirms* a
    seeded server's real Linux users — refreshing ``last_seen_at`` and
    backfilling uid — rather than inventing strangers. Without them it
    falls back to the fixed grace/heidi pair for plain e2e tests.
    """
    if mock_mode:
        if mock_accounts:
            entries = [
                AccountScanEntry(
                    linux_username=a.linux_username,
                    uid=a.uid,
                    gid=a.gid,
                    home_directory=a.home_directory or f"/home/{a.linux_username}",
                    default_shell=a.default_shell,
                )
                for a in mock_accounts
            ]
            return AccountScanReport(entries=entries, mock=True)
        return AccountScanReport(entries=list(_MOCK_ENTRIES), mock=True)
    try:
        entries = [
            AccountScanEntry(
                linux_username=e.pw_name,
                uid=e.pw_uid,
                gid=e.pw_gid,
                home_directory=e.pw_dir or None,
                default_shell=e.pw_shell or None,
            )
            for e in pwd.getpwall()
            if _is_human(e)
        ]
    except Exception as exc:
        _log.warning("account_scan.failed", error=str(exc))
        return AccountScanReport(entries=[], mock=False)
    _log.info("account_scan.collected", n_accounts=len(entries))
    return AccountScanReport(entries=entries, mock=False)
