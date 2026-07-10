"""SQLAlchemy ORM models for CoreLab.

All model classes inherit from a single ``Base`` so that
``Base.metadata`` aggregates the full schema for Alembic autogenerate.
"""

from __future__ import annotations

from .account_link import AccountLink
from .account_link_request import AccountLinkRequest
from .agent_capability import AgentCapability
from .agent_policy import AgentPolicy
from .alert_event import AlertEvent
from .audit_log import AuditLog
from .authorized_key_entry import AuthorizedKeyEntry
from .base import Base
from .enrollment_token import EnrollmentToken
from .gpu import Gpu
from .lab import Lab
from .notification import Notification
from .physical_account import PhysicalAccount
from .registration_invite import RegistrationInvite
from .reservation import Reservation
from .server import Server
from .server_admin_grant import ServerAdminGrant
from .setup_token import SetupToken
from .ssh_public_key import SshPublicKey
from .user import User

__all__ = [
    "AccountLink",
    "AccountLinkRequest",
    "AgentCapability",
    "AgentPolicy",
    "AlertEvent",
    "AuditLog",
    "AuthorizedKeyEntry",
    "Base",
    "EnrollmentToken",
    "Gpu",
    "Lab",
    "Notification",
    "PhysicalAccount",
    "RegistrationInvite",
    "Reservation",
    "Server",
    "ServerAdminGrant",
    "SetupToken",
    "SshPublicKey",
    "User",
]
