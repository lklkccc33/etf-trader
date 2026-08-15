import datetime as dt
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from . import models
from .exchanges import get_exchange_client

logger = logging.getLogger(__name__)


def check_fill(db: Session, account: models.Account, record: models.TradeRecord) -> bool:
    """거래소에 record.kis_order_no의 체결 여부를 물어보고 결과를 반영.

    반환값은 fill_status가 실제로 바뀌었는지 여부. 조회 자체가 실패하면
    (네트워크 오류 등) 예외를 삼키고 False를 반환 — 호출자가 다음 기회에
    다시 시도할 수 있도록 record는 PENDING으로 남겨둠.
    """
    if not record.kis_order_no:
        return False

    client = get_exchange_client(db, account)
    order_date = record.traded_at.strftime("%Y%m%d")

    try:
        result = client.get_order_execution(record.exchange_code, record.kis_order_no, order_date)
    except Exception:
        logger.exception(
            "fill check failed record=%s kis_order_no=%s", record.record_id, record.kis_order_no
        )
        return False

    record.fill_checked_at = dt.datetime.utcnow()

    if result is None:
        db.commit()
        return False

    filled_qty = Decimal(result.get("filled_qty") or "0")
    order_qty = Decimal(result.get("order_qty") or "0")
    filled_price = Decimal(result.get("filled_price") or "0")

    previous_status = record.fill_status
    if filled_qty <= 0:
        record.fill_status = "PENDING"
    elif order_qty and filled_qty < order_qty:
        record.fill_status = "PARTIAL"
        record.filled_volume = filled_qty
        record.filled_price = filled_price
    else:
        record.fill_status = "FILLED"
        record.filled_volume = filled_qty
        record.filled_price = filled_price

    db.commit()

    if record.fill_status != previous_status:
        logger.info(
            "fill status changed record=%s %s -> %s", record.record_id, previous_status, record.fill_status
        )
        return True
    return False
