"""Agent entrypoint — invoked as ``python -m corelab_agent``.

Wires together: argv parsing → TOML config → logging → mock-mode warn
→ WS client run loop → SIGTERM/SIGINT graceful shutdown.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

from corelab_protocol import PROTOCOL_VERSION

from . import __version__
from .compliance_monitor import ComplianceMonitor
from .config import DEFAULT_CONFIG_PATH, load_config
from .gpu_collector import build_collector
from .logging_setup import configure_logging, get_logger
from .mock_mode import warn_if_linux
from .policy_handlers import PolicyDispatcher
from .ws_client import WsClient


def _parse_argv(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="corelab-agent")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to TOML config (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"corelab-agent {__version__} (protocol {PROTOCOL_VERSION})",
    )
    return parser.parse_args(argv)


async def _async_main(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    configure_logging(level=config.log_level, json_output=config.log_json)
    log = get_logger("corelab.agent.main")
    log.info(
        "agent.startup",
        version=__version__,
        protocol_version=PROTOCOL_VERSION,
        backend_url_count=len(config.backend_urls),
        first_backend_url=config.backend_urls[0] if config.backend_urls else None,
        mock_mode=config.mock_mode,
        server_id=config.server_id,
    )

    if config.mock_mode:
        warn_if_linux()

    # Wire signal handlers for graceful shutdown.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    gpu_collector = build_collector(config.mock_mode, config.mock_gpus, server_id=config.server_id)
    # Mock mode pushes telemetry every 5 s so a developer can see the
    # sine wave move in the UI without waiting; prod uses the documented
    # 60 s cadence (docs/06-agent-protocol.md §2).
    telemetry_interval = 5.0 if config.mock_mode else 60.0
    client = WsClient(
        config,
        gpu_collector=gpu_collector,
        telemetry_interval_seconds=telemetry_interval,
        stop_event=stop_event,
        config_path=args.config,
    )
    monitor: ComplianceMonitor | None = None
    if config.compliance_monitor_enabled and config.server_id is not None:
        dispatcher = PolicyDispatcher(
            server_id=config.server_id,
            kill_callback=client.kill_process_for_policy,
            violation_event_callback=client.push_compliance_violation,
        )
        monitor = ComplianceMonitor(
            server_id=config.server_id,
            gpu_collector=gpu_collector,
            dispatcher=dispatcher,
            tick_interval_seconds=config.compliance_monitor_interval_seconds,
            stop_event=stop_event,
        )
        monitor.start()
    elif config.compliance_monitor_enabled:
        log.warning("compliance.monitor.not_started", reason="server_id_missing")
    try:
        await client.run()
    finally:
        if monitor is not None:
            monitor.request_stop()
            await monitor.join()
        log.info("agent.shutdown")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":  # pragma: no cover — module-as-script
    raise SystemExit(main())
