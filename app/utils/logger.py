import sys
import logging
from pathlib import Path
from loguru import logger

from app.core.config import settings


class InterceptHandler(logging.Handler):
    """Intercept standard logging messages toward loguru."""

    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def configure_logger():
    """Initialize the logger with console and optional file output."""
    logger.remove()

    logger.add(
        sys.stdout,
        level=settings.log_level.upper(),
        colorize=True,
    )

    if settings.log_to_file:
        log_file = Path(settings.log_file_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            settings.log_file_path,
            level=settings.log_level.upper(),
            rotation=settings.log_file_rotation,
            retention=settings.log_file_retention,
            compression=settings.log_file_compression,
            enqueue=True,
            encoding="utf-8",
        )

    # Intercept standard logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Disable specific loggers that are too verbose
    logger.disable("curl_cffi")
