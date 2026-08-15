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
        filename=os.path.join(settings.LOG_DIR, "bridge.log"),
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

    # uvicorn의 access/error 로거는 propagate=False + 자체 콘솔 핸들러라
    # 기본적으로 우리 파일에 남지 않음 — "모든 동작 기록" 요구사항을 위해
    # 파일 핸들러만 직접 붙임 (콘솔 핸들러는 중복 출력을 피하려고 제외).
    for name in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addHandler(file_handler)
