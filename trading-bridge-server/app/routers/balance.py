import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models
from ..auth import verify_signature
from ..deps import get_db
from ..exchanges import ExchangeOrderError, get_exchange_client

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_signature)])


@router.get("/balance/{account_id}")
def get_balance(
    account_id: str,
    exchange_code: str = Query(...),
    currency_code: str = Query("USD"),
    db: Session = Depends(get_db),
):
    account = db.get(models.Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")

    try:
        client = get_exchange_client(db, account)
        result = client.get_balance(exchange_code, currency_code)
        logger.info("balance queried account=%s exchange=%s", account_id, exchange_code)
        return result
    except ExchangeOrderError as exc:
        raise HTTPException(status_code=502, detail=f"{exc.code}: {exc.message}")
    except Exception:
        logger.exception("balance lookup failed unexpectedly")
        raise HTTPException(status_code=502, detail="KIS balance lookup failed")
