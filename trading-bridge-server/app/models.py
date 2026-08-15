import uuid

from sqlalchemy import (
    DECIMAL,
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from .database import Base


def uuid_pk():
    return Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))


class Account(Base):
    __tablename__ = "accounts"

    account_id = uuid_pk()
    exchange = Column(String(20), nullable=False, default="KIS")
    env = Column(Enum("REAL", "VIRTUAL", name="account_env"), nullable=False)
    cano = Column(String(8), nullable=False)
    acnt_prdt_cd = Column(String(2), nullable=False)
    encrypted_appkey = Column(String(255), nullable=False)
    encrypted_appsecret = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("cano", "acnt_prdt_cd", "env", name="uq_accounts_account"),
    )

    token_cache = relationship("TokenCache", back_populates="account", uselist=False)
    trade_sessions = relationship("TradeSession", back_populates="account")
    trade_records = relationship("TradeRecord", back_populates="account")


class TokenCache(Base):
    __tablename__ = "token_cache"

    account_id = Column(String(36), ForeignKey("accounts.account_id"), primary_key=True)
    access_token = Column(String(512), nullable=False)
    issued_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    account = relationship("Account", back_populates="token_cache")


class TradeSession(Base):
    __tablename__ = "trade_sessions"

    session_id = uuid_pk()
    account_id = Column(String(36), ForeignKey("accounts.account_id"), nullable=False, index=True)
    seed_money = Column(DECIMAL(20, 4), nullable=False)
    started_at = Column(DateTime, nullable=False, server_default=func.now())

    account = relationship("Account", back_populates="trade_sessions")
    trade_records = relationship("TradeRecord", back_populates="session")
    order_errors = relationship("OrderError", back_populates="session")


class TradeRecord(Base):
    __tablename__ = "trade_records"

    record_id = uuid_pk()
    session_id = Column(String(36), ForeignKey("trade_sessions.session_id"), nullable=False, index=True)
    account_id = Column(String(36), ForeignKey("accounts.account_id"), nullable=False, index=True)
    client_order_id = Column(String(64), nullable=True)
    strategy_name = Column(String(100), nullable=False)
    traded_at = Column(DateTime, nullable=False, index=True)
    exchange_code = Column(Enum("NASD", "NYSE", "AMEX", name="exchange_code"), nullable=False)
    ticker = Column(String(20), nullable=False)
    side = Column(Enum("BUY", "SELL", name="trade_side"), nullable=False)
    volume = Column(DECIMAL(20, 4), nullable=False)
    price = Column(DECIMAL(20, 4), nullable=False)
    value = Column(DECIMAL(20, 4), nullable=False)
    balance = Column(DECIMAL(20, 4), nullable=True)
    kis_order_no = Column(String(50), nullable=True)

    # 주문 접수 이후 실제 체결 확인 결과 — 접수 응답만으로는 체결을 보장하지
    # 않기 때문에 별도로 추적함 (app/fills.py 참고)
    fill_status = Column(
        Enum("PENDING", "FILLED", "PARTIAL", "CANCELLED", name="fill_status"),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )
    filled_volume = Column(DECIMAL(20, 4), nullable=True)
    filled_price = Column(DECIMAL(20, 4), nullable=True)
    fill_checked_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("account_id", "client_order_id", name="uq_trade_records_client_order_id"),
    )

    session = relationship("TradeSession", back_populates="trade_records")
    account = relationship("Account", back_populates="trade_records")


class OrderError(Base):
    __tablename__ = "order_errors"

    error_id = uuid_pk()
    session_id = Column(String(36), ForeignKey("trade_sessions.session_id"), nullable=False, index=True)
    client_order_id = Column(String(64), nullable=True)
    requested_at = Column(DateTime, nullable=False, server_default=func.now())
    request_payload = Column(JSON, nullable=True)
    error_code = Column(String(50), nullable=True)
    error_message = Column(String(500), nullable=True)

    session = relationship("TradeSession", back_populates="order_errors")


class UsedNonce(Base):
    """HMAC 서명 재전송 방지용 nonce 기록. 프로세스 메모리가 아니라 DB에
    저장해서 워커를 여러 개 띄워도(또는 재시작해도) 재전송 방지가 유지됨.
    unique PK라 동시에 같은 nonce로 INSERT하면 하나만 성공 — 그게 곧
    원자적인 '한 번만 처리' 보장."""

    __tablename__ = "used_nonces"

    nonce = Column(String(128), primary_key=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
