"""Logging configuration for NightOwl API.

Ghi log ra file theo ngày với rotation, đồng thời giữ output ra console.
Format thống nhất cho tất cả loggers (nightowl + uvicorn).

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
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    backup_days = int(os.getenv("LOG_BACKUP_DAYS", "10"))

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

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

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(log_level)

    # ── Áp dụng format thống nhất cho nightowl + uvicorn ──────────────────────
    for name in ("nightowl", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.setLevel(log_level)
        # Xóa handler cũ của uvicorn (format khác) rồi gắn lại handler của ta
        logger.handlers.clear()
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.propagate = False


def get_uvicorn_log_config() -> dict:
    """Trả về log_config cho uvicorn để disable formatter riêng của nó.

    Truyền vào uvicorn.run() hoặc --log-config để uvicorn không override
    formatter sau khi setup_logging() đã chạy.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {},
        "handlers": {},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
        },
    }
