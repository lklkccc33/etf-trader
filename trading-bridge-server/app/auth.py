import hashlib
import hmac
import time

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .deps import get_db

TIMESTAMP_TOLERANCE_SECONDS = 300


async def verify_signature(
    request: Request,
    db: Session = Depends(get_db),
    x_api_key: str = Header(...),
    x_timestamp: str = Header(...),
    x_nonce: str = Header(...),
    x_signature: str = Header(...),
) -> None:
    if not hmac.compare_digest(x_api_key, settings.OPERATIONS_API_KEY):
        raise HTTPException(status_code=401, detail="invalid api key")

    now = time.time()
    try:
        ts = float(x_timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid timestamp")

    if abs(now - ts) > TIMESTAMP_TOLERANCE_SECONDS:
        raise HTTPException(status_code=401, detail="timestamp out of range")

    # nonce를 DB에 원자적으로 예약(INSERT + unique PK) — 프로세스 메모리가
    # 아니라 DB에 있어서 워커를 여러 개 띄워도 재전송 방지가 유지되고,
    # unique 제약 자체가 동시 요청 사이의 경쟁 조건도 막아줌(둘 중 하나만
    # INSERT에 성공).
    try:
        db.add(models.UsedNonce(nonce=x_nonce))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=401, detail="replayed nonce")

    body = await request.body()
    message = f"{x_timestamp}.{x_nonce}.".encode() + body
    expected = hmac.new(
        settings.OPERATIONS_API_SECRET.encode(), message, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, x_signature):
        raise HTTPException(status_code=401, detail="invalid signature")
