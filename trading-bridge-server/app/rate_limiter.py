import threading
import time

from .config import settings


class RateLimiter:
    """단순 최소-간격 스로틀러. 프로세스(워커) 하나 안에서만 유효함 — 워커를
    여러 개 띄우면 워커별로 따로 세니, 실제 총 호출량은 워커 수만큼
    늘어날 수 있음(그래서 systemd 배포는 기본적으로 워커 1개를 권장함)."""

    def __init__(self, calls_per_second: float):
        self._min_interval = 1.0 / calls_per_second if calls_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._min_interval - (now - self._last_call)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_call = time.monotonic()


kis_rate_limiter = RateLimiter(settings.KIS_RATE_LIMIT_PER_SECOND)
