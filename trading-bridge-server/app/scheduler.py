import datetime as dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import models
from .database import SessionLocal
from .fills import check_fill
from .reports import write_periodic_reports
from .scheduler_lock import SchedulerLock, run_when_lock_acquired

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="UTC")
_lock = SchedulerLock()
_started_at: dt.datetime | None = None

# 접수 직후 짧은 폴링에서 못 잡은 주문을 얼마나 오래 재확인할지. 그 이후엔
# PENDING으로 남지만 스케줄러가 더 이상 조회하지 않음(오래된 주문 대상
# 무한 재조회 방지).
PENDING_FILL_MAX_AGE = dt.timedelta(hours=24)

# nonce는 타임스탬프 허용오차(auth.TIMESTAMP_TOLERANCE_SECONDS=300초)보다
# 오래되면 재전송 검사에서 어차피 걸리므로, 여유를 두고 이보다 오래된
# 것만 정리.
NONCE_RETENTION = dt.timedelta(hours=1)


def _heartbeat() -> None:
    uptime = dt.datetime.utcnow() - _started_at if _started_at else None
    logger.info("heartbeat ok uptime=%s", uptime)


def _cleanup_used_nonces() -> None:
    db = SessionLocal()
    try:
        cutoff = dt.datetime.utcnow() - NONCE_RETENTION
        deleted = (
            db.query(models.UsedNonce).filter(models.UsedNonce.created_at < cutoff).delete()
        )
        db.commit()
        if deleted:
            logger.info("used_nonces cleanup deleted=%d", deleted)
    except Exception:
        logger.exception("used_nonces cleanup failed")
    finally:
        db.close()


def _reconcile_pending_fills() -> None:
    db = SessionLocal()
    try:
        cutoff = dt.datetime.utcnow() - PENDING_FILL_MAX_AGE
        pending = (
            db.query(models.TradeRecord)
            .filter(models.TradeRecord.fill_status == "PENDING")
            .filter(models.TradeRecord.traded_at >= cutoff)
            .all()
        )
        checked = 0
        for record in pending:
            account = db.get(models.Account, record.account_id)
            if account is None:
                continue
            check_fill(db, account, record)
            checked += 1
        logger.info("pending fill reconcile done checked=%d", checked)
    except Exception:
        logger.exception("pending fill reconcile job failed")
    finally:
        db.close()


def _run_periodic_report(period: str) -> None:
    db = SessionLocal()
    try:
        count = write_periodic_reports(db, period)
        logger.info("periodic report job done period=%s count=%d", period, count)
    except Exception:
        logger.exception("periodic report job failed period=%s", period)
    finally:
        db.close()


def _register_jobs() -> None:
    _scheduler.add_job(
        _heartbeat, IntervalTrigger(hours=1), id="heartbeat", replace_existing=True
    )
    _scheduler.add_job(
        _cleanup_used_nonces,
        IntervalTrigger(minutes=10),
        id="cleanup_used_nonces",
        replace_existing=True,
    )
    _scheduler.add_job(
        _reconcile_pending_fills,
        IntervalTrigger(minutes=3),
        id="reconcile_pending_fills",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_periodic_report,
        CronTrigger(day=1, hour=0, minute=10),
        args=["monthly"],
        id="report_monthly",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_periodic_report,
        CronTrigger(month="1,4,7,10", day=1, hour=0, minute=20),
        args=["quarterly"],
        id="report_quarterly",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_periodic_report,
        CronTrigger(month=1, day=1, hour=0, minute=30),
        args=["yearly"],
        id="report_yearly",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("scheduler started (lock acquired)")


def start_scheduler() -> None:
    global _started_at
    _started_at = dt.datetime.utcnow()

    # 워커를 여러 개 띄워도 실제로 작업을 실행하는 건 락을 쥔 워커 하나뿐 —
    # 나머지는 백그라운드에서 재시도하다가 그 워커가 죽으면 이어받음.
    run_when_lock_acquired(_lock, _register_jobs)


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _lock.release()
    logger.info("scheduler stopped")
