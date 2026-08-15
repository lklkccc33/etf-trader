from sqlalchemy.orm import Session

from .. import models
from .base import ExchangeClient
from .kis import KISExchangeClient

_REGISTRY = {
    "KIS": KISExchangeClient,
}


def get_exchange_client(db: Session, account: models.Account) -> ExchangeClient:
    client_cls = _REGISTRY.get(account.exchange)
    if client_cls is None:
        raise ValueError(f"unsupported exchange: {account.exchange}")
    return client_cls(db, account)
