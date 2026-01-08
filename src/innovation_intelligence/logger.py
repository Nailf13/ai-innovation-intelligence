import logging
import os
from pathlib import Path
from typing import Optional

from innovation_intelligence.config import settings


# ---------------------------------------------------------------------
# Colored Logging Formatter
# ---------------------------------------------------------------------
class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[37m",   # white
        "INFO": "\033[36m",    # cyan
        "WARNING": "\033[33m", # yellow
        "ERROR": "\033[31m",   # red
        "CRITICAL": "\033[41m\033[97m", # white on red bg
    }
    RESET = "\033[0m"

    def format(self, record):
        levelname = record.levelname
        color = self.COLORS.get(levelname, "")
        record.levelname = f"{color}{levelname}{self.RESET}"
        return super().format(record)


# ---------------------------------------------------------------------
# Core logger setup
# ---------------------------------------------------------------------
def setup_logging(
    log_level: Optional[str] = None,
    log_file: Optional[Path] = None,
) -> None:
    """
    Initialize global logging configuration.

    Args:
        log_level (str): e.g. "INFO", "DEBUG", "WARNING"
        log_file (Path): optional path where logs will also be written
    """
    level = getattr(logging, (log_level or settings.log_level).upper(), logging.INFO)

    # Base formatter (no colors, for file logging)
    base_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Console (colored)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(
        ColorFormatter(base_format)
    )

    handlers = [console_handler]

    # Optional file logger
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(base_format))
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
    )


# ---------------------------------------------------------------------
# Helper to get module-specific loggers
# ---------------------------------------------------------------------
def get_logger(name: str) -> logging.Logger:
    """
    Returns a logger with correct project configuration.
    
    Usage:
        from health_intel.logger import get_logger
        log = get_logger(__name__)
    """
    return logging.getLogger(name)


# ---------------------------------------------------------------------
# Auto-initialize on import
# You can remove this if you prefer manual setup in your CLI entrypoint.
# ---------------------------------------------------------------------
setup_logging()
