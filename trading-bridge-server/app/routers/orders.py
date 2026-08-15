import datetime as dt
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import verify_signature
from ..deps import get_db
from ..exchanges import ExchangeOrderError, get_exchange_client
from ..fills import check_fill
from ..reports import write_realtime_status
from ..schemas import OrderRequest, OrderResponse

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_signature)])

# 주문 응답을 보내기 전에 체결 여부를 짧게 폴링하는 횟수/간격.
# 여기서 못 잡은 주문은 스케줄러의 백그라운드 재확인 작업이 이어받음.
FILL_POLL_ATTEMPTS = 3
FILL_POLL_INTERVAL_SECONDS = 1


def _log_order_error(db: Session, body: OrderRequest, error_code: str, error_message: str) -> None:
    db.add(
        models.OrderError(
            error_id=str(uuid.uuid4()),
            session_id=body.session_id,
            client_order_id=body.client_order_id,
            request_payload=body.model_dump(mode="json"),
            error_code=error_code,
            error_message=error_message[:500],
        )
    )
    db.commit()


def _response_from_record(record: models.TradeRecord, *, duplicate: bool) -> OrderResponse:
    return OrderResponse(
        status="ACCEPTED",
        fill_status=record.fill_status,
        kis_order_no=record.kis_order_no,
        filled_price=record.filled_price,
        filled_volume=record.filled_volume,
        balance=record.balance,
        timestamp=dt.datetime.utcnow().isoformat(),
        duplicate=duplicate,
    )


@router.post("/orders", response_model=OrderResponse)
def create_order(body: OrderRequest, db: Session = Depends(get_db)):
    session = db.get(models.TradeSession, body.session_id)
    if session is None or session.account_id != body.account_id:
        raise HTTPException(status_code=404, detail="session not found")

    account = db.get(models.Account, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")

    # 이미 성공(접수 확인)한 것으로 기록된 client_order_id면 KIS에 다시
    # 주문을 넣지 않고 그때 저장된 결과를 그대로 돌려줌 — 네트워크 재시도로
    # 인한 중복 주문 방지. KIS 주문 API 자체는 멱등이 아니므로, 우리가 성공을
    # "확인한" 경우에 한해서만 막고, REJECTED/ERROR는 재시도를 허용함
    # (그 경우 KIS에 실제로 주문이 들어갔는지 불확실하기 때문).
    existing = (
        db.query(models.TradeRecord)
        .filter(
            models.TradeRecord.account_id == body.account_id,
            models.TradeRecord.client_order_id == body.client_order_id,
        )
        .first()
    )
    if existing is not None:
        logger.info(
            "duplicate order request client_order_id=%s record=%s",
            body.client_order_id,
            existing.record_id,
        )
        return _response_from_record(existing, duplicate=True)

    try:
        client = get_exchange_client(db, account)
        result = client.place_order(
            exchange_code=body.exchange_code,
            ticker=body.ticker,
            side=body.side,
            order_type=body.order_type,
            volume=str(body.volume),
            price=str(body.price),
        )
    except ExchangeOrderError as exc:
        logger.warning(
            "order rejected session=%s ticker=%s side=%s code=%s message=%s",
            body.session_id,
            body.ticker,
            body.side,
            exc.code,
            exc.message,
        )
        _log_order_error(db, body, exc.code or "REJECTED", exc.message or "")
        return OrderResponse(status="REJECTED", timestamp=dt.datetime.utcnow().isoformat())
    except Exception as exc:
        logger.exception("order submission failed unexpectedly")
        _log_order_error(db, body, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")
        return OrderResponse(status="ERROR", timestamp=dt.datetime.utcnow().isoformat())

    record = models.TradeRecord(
        record_id=str(uuid.uuid4()),
        session_id=body.session_id,
        account_id=body.account_id,
        client_order_id=body.client_order_id,
        strategy_name=body.strategy_name,
        traded_at=dt.datetime.utcnow(),
        exchange_code=body.exchange_code,
        ticker=body.ticker,
        side=body.side,
        volume=body.volume,
        price=body.price,
        value=body.volume * body.price,
        balance=None,
        kis_order_no=result["order_no"],
    )
    db.add(record)
    db.commit()

    logger.info(
        "order accepted record=%s session=%s strategy=%s ticker=%s side=%s volume=%s price=%s kis_order_no=%s",
        record.record_id,
        body.session_id,
        body.strategy_name,
        body.ticker,
        body.side,
        body.volume,
        body.price,
        result["order_no"],
    )

    # 접수 직후 짧게 체결 여부를 폴링 — 시장가 주문 등 즉시 체결되는 경우
    # 여기서 바로 잡힘. 못 잡으면 PENDING으로 남고 스케줄러가 이어서 확인.
    for attempt in range(FILL_POLL_ATTEMPTS):
        try:
            changed = check_fill(db, account, record)
        except Exception:
            logger.exception("fill poll attempt failed record=%s", record.record_id)
            break
        if changed and record.fill_status != "PENDING":
            break
        if attempt < FILL_POLL_ATTEMPTS - 1:
            time.sleep(FILL_POLL_INTERVAL_SECONDS)

    # 주문은 이미 접수됐으므로 리포트 갱신 실패가 응답 실패로 이어지면 안 됨
    # (호출측이 실패로 오인하고 동일 주문을 재시도할 위험)
    try:
        report_status = write_realtime_status(
            db, account, session, body.exchange_code, currency_code="USD"
        )
        record.balance = report_status["total_value"]
        db.commit()
    except Exception:
        logger.exception("realtime report update failed after an accepted order")

    # KIS 응답은 주문이 "접수"됐다는 뜻일 뿐 실제 체결을 보장하지 않음
    # (지정가 주문은 미체결/부분체결일 수 있음) — ACCEPTED로 정확히 표현하고,
    # 실제 체결 여부는 fill_status/filled_price/filled_volume으로 별도 전달.
    return _response_from_record(record, duplicate=False)
