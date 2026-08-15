import datetime as dt
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import verify_signature
from ..deps import get_db
from ..schemas import SessionCreateRequest, SessionCreateResponse

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_signature)])


@router.post("/sessions", response_model=SessionCreateResponse)
def create_session(body: SessionCreateRequest, db: Session = Depends(get_db)):
    account = db.get(models.Account, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")

    session = models.TradeSession(
        session_id=str(uuid.uuid4()),
        account_id=body.account_id,
        seed_money=body.seed_money,
        started_at=dt.datetime.utcnow(),
    )
    db.add(session)
    db.commit()

    logger.info(
        "session created id=%s account=%s seed_money=%s",
        session.session_id,
        account.account_id,
        body.seed_money,
    )

    return SessionCreateResponse(
        session_id=session.session_id, started_at=session.started_at.isoformat()
    )
