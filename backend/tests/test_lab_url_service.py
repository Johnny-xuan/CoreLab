from __future__ import annotations

from datetime import UTC, datetime, timedelta

from corelab_backend.models import Lab
from corelab_backend.services import lab_url_service


def _lab(public_urls: list[dict]) -> Lab:
    return Lab(name="Lab", slug="lab", public_urls=public_urls)


def test_agent_urls_skip_stale_cloudflare_quick_url() -> None:
    now = datetime(2026, 7, 5, 5, 0, tzinfo=UTC)
    lab = _lab(
        [
            {
                "url": "https://old.trycloudflare.com",
                "kind": "cloudflare_quick",
                "source": "manual_admin",
                "verified_at": (now - timedelta(days=1)).isoformat(),
                "last_reachable_at": (now - timedelta(days=1)).isoformat(),
                "primary": True,
            },
            {
                "url": "http://localhost:8080",
                "kind": "lan",
                "source": "manual_admin",
                "primary": False,
            },
        ]
    )

    assert lab_url_service.agent_urls_only(lab, now=now) == ["http://localhost:8080"]


def test_agent_urls_keep_recent_cloudflare_quick_url() -> None:
    now = datetime(2026, 7, 5, 5, 0, tzinfo=UTC)
    lab = _lab(
        [
            {
                "url": "https://fresh.trycloudflare.com",
                "kind": "cloudflare_quick",
                "source": "manual_admin",
                "last_reachable_at": (now - timedelta(minutes=2)).isoformat(),
                "primary": True,
            },
            {
                "url": "http://localhost:8080",
                "kind": "lan",
                "source": "manual_admin",
                "primary": False,
            },
        ]
    )

    assert lab_url_service.agent_urls_only(lab, now=now) == [
        "https://fresh.trycloudflare.com",
        "http://localhost:8080",
    ]


def test_agent_urls_keep_runtime_discovered_quick_tunnel_before_probe() -> None:
    now = datetime(2026, 7, 5, 5, 0, tzinfo=UTC)
    lab = _lab(
        [
            {
                "url": "https://new.trycloudflare.com",
                "kind": "cloudflare_quick",
                "source": "tunnel_runtime",
                "primary": True,
            }
        ]
    )

    assert lab_url_service.agent_urls_only(lab, now=now) == ["https://new.trycloudflare.com"]


def test_agent_urls_fall_back_when_filtering_would_remove_everything() -> None:
    now = datetime(2026, 7, 5, 5, 0, tzinfo=UTC)
    lab = _lab(
        [
            {
                "url": "https://old.trycloudflare.com",
                "kind": "cloudflare_quick",
                "source": "manual_admin",
                "verified_at": (now - timedelta(days=1)).isoformat(),
                "primary": True,
            }
        ]
    )

    assert lab_url_service.agent_urls_only(lab, now=now) == ["https://old.trycloudflare.com"]
