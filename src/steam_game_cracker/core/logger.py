import logging
import logging.config
import logging.handlers
from pathlib import Path
from typing import Any

LOG_DIR: Path = Path(__file__).resolve().parents[3] / "logs"

MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT: int = 5

SIMPLE_FORMAT: str = "%(levelname)s: %(message)s"
DETAILED_FORMAT: str = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def build_logging_config(verbose: bool = False, console: bool = True, file: bool = True) -> dict[str, Any]:
    """Build a logging configuration dict for dictConfig."""
    level = "DEBUG" if verbose else "INFO"
    root_handlers = []
    if console:
        root_handlers.append("console")
    if file:
        root_handlers.append("file")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {"format": SIMPLE_FORMAT},
            "detailed": {"format": DETAILED_FORMAT},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "simple",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": level,
                "formatter": "detailed",
                "filename": str(LOG_DIR / "app.log"),
                "maxBytes": MAX_BYTES,
                "backupCount": BACKUP_COUNT,
                "encoding": "utf-8",
            },
        },
        "root": {
            "level": level,
            "handlers": root_handlers,
        },
    }


_configured: bool = False


def setup_logger(verbose: bool = False, console: bool = True, file: bool = True) -> logging.Logger:
    """Configure console and rotating file logging."""
    global _configured
    if _configured:
        return logging.getLogger()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(build_logging_config(verbose=verbose, console=console, file=file))
    _configured = True
    return logging.getLogger()
