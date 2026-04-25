"""Logging configuration for NightOwl API.

Ghi log ra file theo ngày với rotation, đồng thời giữ output ra console.

Environment variables:
  LOG_LEVEL         DEBUG | INFO | WARNING | ERROR   (default: INFO)
  LOG_DIR           Thư mục chứa file log             (default: logs/)
  LOG_BACKUP_DAYS   Số ngày log giữ lại               (default: 10)
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logging() -> None:
    """Gọi một lần khi khởi động app. Cấu hình handler cho logger gốc 'nightowl'."""

    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    backup_days = int(os.getenv("LOG_BACKUP_DAYS", "10"))

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── File handler (rotation theo ngày, giữ 10 ngày) ────────────────────────
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_dir / "app.log",
        when="midnight",
        interval=1,
        backupCount=backup_days,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(fmt)
    file_handler.setLevel(log_level)

    # ── Console handler ────────────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(log_level)

    # ── Root logger "nightowl" — bắt tất cả sub-logger ────────────────────────
    root_logger = logging.getLogger("nightowl")
    root_logger.setLevel(log_level)

    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

    root_logger.propagate = False

    # ── Uvicorn loggers → cũng ghi vào file ───────────────────────────────────
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvi = logging.getLogger(name)
        if not any(isinstance(h, logging.handlers.TimedRotatingFileHandler) for h in uvi.handlers):
            uvi.addHandler(file_handler)
