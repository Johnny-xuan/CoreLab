"""Reservation — user 占用 server 上某 GPU 的某时段(可选 script + 可选共享显存).

Phase 5 ORM. Schema 严按 docs/02-data-model.md §5.13(v0.2 reframing
已并入):``account_link_id`` 必填(per-PA UI 路由 context 注入),
service 层 enforce ``source != 'admin_declared'``;``gpu_memory_mb``
NULL = 独占整卡 / 非空 = 共享模式(同 GPU 同时段累加 ≤ gpu.memory_total_mb);
``script_scheduled_start_at`` 允许 ``>= start_at && < end_at``(默认
等于 start_at);``status`` Phase 5 只用 scheduled / cancelled,active
/ completed / failed 由 Phase 6 scheduler 触发。

``server_id`` 是 ``gpu.server_id`` 的传递依赖(违反 3NF) — 保留是为
了热查询 "server X 当天预约" 不走 JOIN;DDL 注释 doc §5.13。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import CHAR, INTEGER, TEXT, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class Reservation(Base):
    __tablename__ = "reservation"
    __table_args__ = (
        Index("idx_res_gpu_time", "gpu_id", "start_at", "end_at"),
        Index("idx_res_server_time", "server_id", "start_at"),
        Index("idx_res_user_time", "user_id", "start_at"),
        Index("idx_res_link_time", "account_link_id", "start_at"),
        Index("idx_res_status_start", "status", "start_at"),
        Index("idx_res_status_script_start", "status", "script_scheduled_start_at"),
        Index("idx_res_group", "group_id"),
        CheckConstraint(
            "status IN ('scheduled','active','completed','cancelled','failed')",
            name="ck_res_status",
        ),
        CheckConstraint("end_at > start_at", name="ck_res_time_order"),
        CheckConstraint(
            "script_scheduled_start_at IS NULL OR "
            "(script_scheduled_start_at >= start_at AND script_scheduled_start_at < end_at)",
            name="ck_res_script_time",
        ),
        CheckConstraint(
            "gpu_compute_share_pct IS NULL OR "
            "(gpu_compute_share_pct >= 1 AND gpu_compute_share_pct <= 100)",
            name="ck_res_compute_pct",
        ),
        # Phase 6: script_status tracks the lifecycle of a script attached
        # to an active reservation. NULL = not yet fired (or no script);
        # running = agent has started subprocess; completed = exit 0;
        # failed = exit != 0; killed = agent terminated it (cancel /
        # max_runtime). Reservation.status transitions independently
        # (see services/reservation_service.py transition helpers).
        CheckConstraint(
            "script_status IS NULL OR script_status IN ('running','completed','failed','killed')",
            name="ck_res_script_status",
        ),
        # Phase J — reservation is now the unified "task" entity:
        #   gpu_id NOT NULL              → Modes 1/2 (occupies a GPU slot)
        #   gpu_id NULL + script NOT NULL → Mode 3 (pure cron task, no GPU)
        # The "at least one of gpu_id/script" invariant is enforced in
        # reservation_service.create instead of a DB CHECK — MySQL refuses
        # CHECK on FK referential-action columns (error 3823). See
        # migration 9a7b3c2d1e0f for the historical context.
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_res_user", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    server_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("server.id", name="fk_res_server", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    gpu_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("gpu.id", name="fk_res_gpu", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=True,  # Phase J — Mode 3 (pure cron task) leaves this NULL.
    )
    account_link_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("account_link.id", name="fk_res_link", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    start_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    end_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, server_default="scheduled")
    gpu_memory_mb: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True)
    gpu_compute_share_pct: Mapped[int | None] = mapped_column(TINYINT(unsigned=True), nullable=True)
    script: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    script_scheduled_start_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP_TYPE, nullable=True
    )
    script_max_runtime_seconds: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True), nullable=True
    )
    script_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    script_finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    script_exit_code: Mapped[int | None] = mapped_column(INTEGER(), nullable=True)
    script_output_size_bytes: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True), nullable=True
    )
    script_log_path: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    script_log_tail_text: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    script_log_tail_truncated: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, server_default="0"
    )
    # Phase 6 — see docs/02 §5.13 field table + ck_res_script_status above.
    script_status: Mapped[str | None] = mapped_column(VARCHAR(16), nullable=True)
    # Phase 7 (Worker Catch #1, B 方案) — outbox marker. Scheduler sets
    # it inside the SERIALIZABLE dispatch tick alongside
    # script_status='running'; the agent's script.started ack clears
    # it; the 4th tick action _retry_stuck_dispatches re-fires the
    # RPC or marks the row failed when the column has stayed set for
    # more than 60 s. Reservation.script_status never rolls back, so
    # the Phase 6 _ALLOWED_SCRIPT_TRANSITIONS table is unaffected.
    script_dispatch_started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP_TYPE, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    cancelled_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_res_cancelled_by", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
