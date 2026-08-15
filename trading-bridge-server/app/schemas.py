from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel


class SessionCreateRequest(BaseModel):
    account_id: str
    seed_money: Decimal


class SessionCreateResponse(BaseModel):
    session_id: str
    started_at: str


class OrderRequest(BaseModel):
    client_order_id: str
    session_id: str
    strategy_name: str
    account_id: str
    exchange_code: Literal["NASD", "NYSE", "AMEX"]
    ticker: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT", "MARKET"]
    volume: Decimal
    price: Decimal


class OrderResponse(BaseModel):
    status: Literal["ACCEPTED", "REJECTED", "ERROR"]
    fill_status: Optional[Literal["PENDING", "FILLED", "PARTIAL", "CANCELLED"]] = None
    kis_order_no: Optional[str] = None
    filled_price: Optional[Decimal] = None
    filled_volume: Optional[Decimal] = None
    balance: Optional[Decimal] = None
    timestamp: str
    duplicate: bool = False
