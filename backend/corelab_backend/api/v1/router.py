"""Aggregate v1 router: mounted at ``/api/v1`` in main.py."""

from __future__ import annotations

from fastapi import APIRouter

from .account_link_requests import router as account_link_requests_router
from .account_links import router as account_links_router
from .admin_tokens import router as admin_tokens_router
from .agent_endpoints import router as agent_router
from .alerts import router as alerts_router
from .audit_logs import router as audit_logs_router
from .auth import router as auth_router
from .gpus import router as gpus_router
from .installer import router as installer_router
from .lab_overview import router as lab_overview_router
from .notifications import router as notifications_router
from .physical_accounts import router as physical_accounts_router
from .policies import router as policies_router
from .public_urls import router as public_urls_router
from .reservations import router as reservations_router
from .servers import router as servers_router
from .setup import router as setup_router
from .ssh_keys import router as ssh_keys_router
from .tunnel import router as tunnel_router
from .usage import router as usage_router
from .users import router as users_router

router = APIRouter(prefix="/api/v1")
router.include_router(setup_router)
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(ssh_keys_router)
router.include_router(servers_router)
router.include_router(gpus_router)
router.include_router(physical_accounts_router)
router.include_router(account_links_router)
router.include_router(account_link_requests_router)
router.include_router(reservations_router)
router.include_router(admin_tokens_router)
router.include_router(agent_router)
router.include_router(usage_router)
router.include_router(notifications_router)
router.include_router(policies_router)
router.include_router(alerts_router)
router.include_router(audit_logs_router)
router.include_router(lab_overview_router)
router.include_router(installer_router)
router.include_router(public_urls_router)
router.include_router(tunnel_router)
