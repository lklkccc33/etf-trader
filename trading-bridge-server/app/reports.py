import csv
import datetime as dt
import json
import logging
import os
import re
from decimal import Decimal

from sqlalchemy.orm import Session

from . import models
from .config import settings
from .exchanges import get_exchange_client

logger = logging.getLogger(__name__)

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename_part(value: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("_", value)


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def write_realtime_status(
    db: Session,
    account: models.Account,
    session: models.TradeSession,
    exchange_code: str,
    currency_code: str,
) -> dict:
    client = get_exchange_client(db, account)
    balance = client.get_balance(exchange_code, currency_code)

    # output1/output2 구조와 필드명(frcr_dncl_amt1, tot_evlu_pfls_amt 등)은
    # KIS 해외주식 잔고조회 응답 기준 — 실제 연동 시 최신 문서로 재확인 필요.
    holdings = balance.get("output1", [])
    summary = (balance.get("output2") or [{}])[0]

    cash_balance = summary.get("frcr_dncl_amt1")
    total_value = Decimal(summary.get("tot_evlu_pfls_amt") or "0")
    seed_money = session.seed_money or Decimal("0")
    return_rate = (
        ((total_value - seed_money) / seed_money * 100) if seed_money else Decimal("0")
    )

    status = {
        "account_id": account.account_id,
        "session_id": session.session_id,
        "holdings": holdings,
        "cash_balance": cash_balance,
        "total_value": total_value,
        "return_rate": return_rate.quantize(Decimal("0.0001")),
    }

    os.makedirs(settings.REPORTS_DIR, exist_ok=True)
    path = os.path.join(settings.REPORTS_DIR, f"{account.account_id}-latest-status.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2, default=_json_default)

    return status


def _latest_strategy_account_pairs(db: Session):
    """(strategy_name, account_id)별로 가장 최근 거래의 exchange_code를 함께 반환.

    계좌 잔고는 KIS 잔고조회가 거래소 단위라 전략별로 분리되지 않음 —
    같은 계좌를 여러 전략이 공유하면 balance는 계좌 전체 값이 됨(알려진 한계).
    """
    rows = (
        db.query(
            models.TradeRecord.strategy_name,
            models.TradeRecord.account_id,
            models.TradeRecord.exchange_code,
        )
        .order_by(models.TradeRecord.traded_at.desc())
        .all()
    )
    seen = set()
    pairs = []
    for strategy_name, account_id, exchange_code in rows:
        key = (strategy_name, account_id)
        if key in seen:
            continue
        seen.add(key)
        pairs.append((strategy_name, account_id, exchange_code))
    return pairs


def write_periodic_reports(db: Session, period: str) -> int:
    """period: 'monthly' | 'quarterly' | 'yearly'. 생성된 리포트 개수를 반환."""
    os.makedirs(settings.REPORTS_DIR, exist_ok=True)
    written = 0

    for strategy_name, account_id, exchange_code in _latest_strategy_account_pairs(db):
        account = db.get(models.Account, account_id)
        session = (
            db.query(models.TradeSession)
            .filter(models.TradeSession.account_id == account_id)
            .order_by(models.TradeSession.started_at.desc())
            .first()
        )
        if account is None or session is None:
            continue

        try:
            status = write_realtime_status(db, account, session, exchange_code, "USD")
        except Exception:
            logger.exception(
                "periodic report balance lookup failed strategy=%s account=%s period=%s",
                strategy_name,
                account_id,
                period,
            )
            continue

        filename = f"{_safe_filename_part(strategy_name)}-{account_id}-{period}-report.csv"
        path = os.path.join(settings.REPORTS_DIR, filename)
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["balance", "return_rate", "generated_at"])
                writer.writerow(
                    [status["total_value"], status["return_rate"], dt.datetime.utcnow().isoformat()]
                )
        except OSError:
            logger.exception(
                "periodic report file write failed strategy=%s account=%s period=%s",
                strategy_name,
                account_id,
                period,
            )
            continue

        logger.info("periodic report written path=%s", path)
        written += 1

    return written
