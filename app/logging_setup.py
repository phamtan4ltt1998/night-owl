"""Logging configuration for NightOwl API.

Ghi log ra file với rotation, đồng thời giữ output ra console.

Environment variables:
  LOG_LEVEL      DEBUG | INFO | WARNING | ERROR   (default: INFO)
  LOG_DIR        Thư mục chứa file log             (default: logs/)
  LOG_MAX_BYTES  Kích thước tối đa mỗi file        (default: 10485760 = 10 MB)
  LOG_BACKUP_COUNT  Số file backup giữ lại         (default: 5)
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

    max_bytes = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
    backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── File handler (rotation theo kích thước) ────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "app.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(log_level)

    # ── Console handler ────────────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(log_level)

    # ── Root logger "nightowl" — bắt tất cả sub-logger ────────────────────────
    root_logger = logging.getLogger("nightowl")
    root_logger.setLevel(log_level)

    # Tránh thêm handler trùng nếu hàm bị gọi nhiều lần
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

    # Không propagate lên root Python logger để tránh log trùng với uvicorn
    root_logger.propagate = False
