"""统一日志配置。"""
from __future__ import annotations

import logging

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化根日志（幂等），返回应用 logger。"""
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
        _CONFIGURED = True
    return logging.getLogger("second_brain")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"second_brain.{name}")