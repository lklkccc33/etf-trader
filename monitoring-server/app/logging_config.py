import logging
import os
from logging.handlers import TimedRotatingFileHandler

from .config import settings


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(settings.LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(settings.LOG_DIR, "monitor.log"),
        when="midnight",
        backupCount=90,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
