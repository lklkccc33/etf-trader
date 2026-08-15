import logging
import threading
import time

from sqlalchemy import text

from .database import engine

logger = logging.getLogger(__name__)

_LOCK_NAME = "trading_bridge_scheduler"
_RETRY_INTERVAL_SECONDS = 30


class SchedulerLock:
    """MySQL GET_LOCK 기반 싱글톤 락 — 워커를 여러 개 띄워도 스케줄 작업
    (정기 리포트, heartbeat, 체결 재확인)은 그중 하나만 실제로 실행하게 함.
    Redis 같은 별도 인프라 없이 이미 쓰고 있는 MySQL로 처리.

    락은 커넥션에 묶여 있어서, 락을 쥔 프로세스가 죽으면(커넥션 종료)
    자동으로 풀려서 다른 워커가 이어받을 수 있음.

    MySQL이 아닌 DB(로컬 개발의 SQLite 등)에서는 GET_LOCK을 쓸 수 없으므로
    항상 락을 획득한 것으로 간주 — 단일 프로세스라고 가정.
    """

    def __init__(self):
        self._conn = None

    def try_acquire(self) -> bool:
        if engine.dialect.name != "mysql":
            return True

        conn = engine.connect()
        result = conn.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": _LOCK_NAME}).scalar()
        if result == 1:
            self._conn = conn
            return True
        conn.close()
        return False

    def release(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _LOCK_NAME})
        except Exception:
            pass
        finally:
            self._conn.close()
            self._conn = None


def run_when_lock_acquired(lock: SchedulerLock, on_acquired) -> None:
    """락을 바로 잡으면 즉시 on_acquired() 실행. 못 잡으면(다른 워커가 이미
    스케줄러를 돌리고 있음) 백그라운드 스레드에서 주기적으로 재시도하다가
    잡히면(예: 그 워커가 죽어서 락이 풀리면) 그때 on_acquired() 실행."""
    if lock.try_acquire():
        on_acquired()
        return

    logger.info("scheduler lock held by another worker — will retry in background")

    def _retry_loop():
        while True:
            time.sleep(_RETRY_INTERVAL_SECONDS)
            if lock.try_acquire():
                logger.info("scheduler lock acquired, starting scheduled jobs")
                on_acquired()
                return

    threading.Thread(target=_retry_loop, daemon=True, name="scheduler-lock-retry").start()
