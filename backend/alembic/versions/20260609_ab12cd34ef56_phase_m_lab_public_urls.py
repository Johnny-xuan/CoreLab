"""phase M v5 lab.public_urls + tunnel_mode + tunnel_token

Revision ID: ab12cd34ef56
Revises: 9a7b3c2d1e0f
Create Date: 2026-06-09 14:00:00.000000+00:00

Phase M v5 — multi-URL native public access. The lab is no longer
identified by a single ``BACKEND_PUBLIC_URL`` env value; instead it
carries a list of all the addresses by which the backend is reachable
(LAN, public IP, custom domain, Cloudflare Quick / Named Tunnel).

``public_urls`` shape (each entry):

    {
      "url":               "http://192.168.1.17:80",
      "kind":              "lan" | "public_ip" | "custom_domain"
                          | "cloudflare_quick" | "cloudflare_named",
      "source":            "install_sh_probe" | "manual_admin"
                          | "tunnel_runtime",
      "verified_at":       ISO-8601 timestamp | null,
      "last_reachable_at": ISO-8601 timestamp | null,
      "primary":           true | false                          # UI-only hint
    }

``tunnel_mode`` is the lab's current tunnel posture:

    'none'              — LAN mode (vLLM-style direct port binding).
                          install.sh did not start cloudflared.
    'cloudflare_quick'  — cloudflared sidecar running with --url, random
                          trycloudflare.com hostname (changes on restart).
    'cloudflare_named'  — cloudflared sidecar running with a Named Tunnel
                          token (stable URL, requires CF account).

``tunnel_token`` holds the Named Tunnel token at rest (only populated
when tunnel_mode = 'cloudflare_named'). Encrypted-at-rest is a follow-up
(this column stores plaintext for now; treat as low-grade secret).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "ab12cd34ef56"  # pragma: allowlist secret
down_revision: str | None = "9a7b3c2d1e0f"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # MySQL JSON column needs a server-default that is itself JSON.
    # The literal '[]' must be wrapped in a sa.text() so alembic doesn't
    # quote it as a string default.
    op.add_column(
        "lab",
        sa.Column(
            "public_urls",
            mysql.JSON(),
            nullable=False,
            server_default=sa.text("(JSON_ARRAY())"),
            comment="List of URLs by which this CoreLab backend is reachable. "
            "Filled by install.sh probe + admin in Public Access card.",
        ),
    )
    op.add_column(
        "lab",
        sa.Column(
            "tunnel_mode",
            mysql.VARCHAR(32),
            nullable=False,
            server_default=sa.text("'none'"),
            comment="none | cloudflare_quick | cloudflare_named. Default 'none' = LAN mode.",
        ),
    )
    op.add_column(
        "lab",
        sa.Column(
            "tunnel_token",
            mysql.VARCHAR(255),
            nullable=True,
            comment="Cloudflare Named Tunnel token (plaintext for now; treat as secret).",
        ),
    )


def downgrade() -> None:
    op.drop_column("lab", "tunnel_token")
    op.drop_column("lab", "tunnel_mode")
    op.drop_column("lab", "public_urls")
