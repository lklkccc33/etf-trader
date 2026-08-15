import datetime as dt

import requests
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..crypto import decrypt, encrypt
from ..rate_limiter import kis_rate_limiter
from .base import ExchangeClient, ExchangeOrderError

TOKEN_REFRESH_BUFFER = dt.timedelta(minutes=10)
TOKEN_TTL = dt.timedelta(hours=23, minutes=50)

DOMAIN = {
    "REAL": settings.KIS_REAL_DOMAIN,
    "VIRTUAL": settings.KIS_VIRTUAL_DOMAIN,
}

# KIS 공식 문서 개정에 따라 달라질 수 있어 연동 착수 시 최신 문서로 재확인 필요
ORDER_TR_ID = {
    "REAL": {"BUY": "JTTT1002U", "SELL": "JTTT1006U"},
    "VIRTUAL": {"BUY": "VTTT1002U", "SELL": "VTTT1001U"},
}
BALANCE_TR_ID = {"REAL": "JTTT3012R", "VIRTUAL": "VTTT3012R"}

# 해외주식 주문체결내역조회(inquire-ccnl) — 다른 tr_id들보다 검증 자료가
# 적음. 실제 연동 전 KIS 공식 문서로 반드시 재확인할 것.
FILL_TR_ID = {"REAL": "JTTT3018R", "VIRTUAL": "VTTT3018R"}


class KISExchangeClient(ExchangeClient):
    def __init__(self, db: Session, account: models.Account):
        self.db = db
        self.account = account
        self.domain = DOMAIN[account.env]
        self.appkey = decrypt(account.encrypted_appkey)
        self.appsecret = decrypt(account.encrypted_appsecret)

    @staticmethod
    def _throttled_post(*args, **kwargs):
        kis_rate_limiter.wait()
        return requests.post(*args, **kwargs)

    @staticmethod
    def _throttled_get(*args, **kwargs):
        kis_rate_limiter.wait()
        return requests.get(*args, **kwargs)

    def _get_access_token(self) -> str:
        cache = self.db.get(models.TokenCache, self.account.account_id)
        now = dt.datetime.utcnow()
        if cache and cache.expires_at > now + TOKEN_REFRESH_BUFFER:
            return decrypt(cache.access_token)
        return self._issue_access_token()

    def _issue_access_token(self) -> str:
        resp = self._throttled_post(
            f"{self.domain}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.appkey,
                "appsecret": self.appsecret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]

        issued_at = dt.datetime.utcnow()
        expires_in = data.get("expires_in")
        ttl = dt.timedelta(seconds=int(expires_in)) if expires_in is not None else TOKEN_TTL
        expires_at = issued_at + ttl

        cache = self.db.get(models.TokenCache, self.account.account_id)
        if cache is None:
            cache = models.TokenCache(account_id=self.account.account_id)
            self.db.add(cache)
        cache.access_token = encrypt(token)
        cache.issued_at = issued_at
        cache.expires_at = expires_at
        self.db.commit()

        return token

    def _get_hashkey(self, body: dict) -> str:
        resp = self._throttled_post(
            f"{self.domain}/uapi/hashkey",
            headers={
                "content-type": "application/json",
                "appkey": self.appkey,
                "appsecret": self.appsecret,
            },
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["HASH"]

    def place_order(
        self,
        *,
        exchange_code: str,
        ticker: str,
        side: str,
        order_type: str,
        volume: str,
        price: str,
    ) -> dict:
        token = self._get_access_token()
        body = {
            "CANO": self.account.cano,
            "ACNT_PRDT_CD": self.account.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": ticker,
            "ORD_QTY": str(volume),
            # 시장가 주문은 지정가를 KIS가 무시하는 관례를 따라 0으로 고정 —
            # 호출자가 보낸 price를 시장가 주문에 그대로 흘려보내지 않음.
            "OVRS_ORD_UNPR": "0" if order_type == "MARKET" else str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00" if order_type == "LIMIT" else "01",
        }
        tr_id = ORDER_TR_ID[self.account.env][side]
        hashkey = self._get_hashkey(body)

        resp = self._throttled_post(
            f"{self.domain}/uapi/overseas-stock/v1/trading/order",
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.appkey,
                "appsecret": self.appsecret,
                "tr_id": tr_id,
                "custtype": "P",
                "hashkey": hashkey,
            },
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise ExchangeOrderError(data.get("msg_cd"), data.get("msg1"))

        output = data.get("output", {})
        return {"order_no": output.get("ODNO"), "raw": data}

    def get_balance(self, exchange_code: str, currency_code: str) -> dict:
        token = self._get_access_token()
        tr_id = BALANCE_TR_ID[self.account.env]

        resp = self._throttled_get(
            f"{self.domain}/uapi/overseas-stock/v1/trading/inquire-balance",
            headers={
                "authorization": f"Bearer {token}",
                "appkey": self.appkey,
                "appsecret": self.appsecret,
                "tr_id": tr_id,
            },
            params={
                "CANO": self.account.cano,
                "ACNT_PRDT_CD": self.account.acnt_prdt_cd,
                "OVRS_EXCG_CD": exchange_code,
                "TR_CRCY_CD": currency_code,
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise ExchangeOrderError(data.get("msg_cd"), data.get("msg1"))

        return data

    def get_order_execution(self, exchange_code: str, order_no: str, order_date: str) -> dict | None:
        """주문번호 기준 체결 내역 조회. 매칭되는 항목이 없으면 None.

        order_date는 YYYYMMDD 형식. tr_id/파라미터/응답 필드명은 KIS 공식
        문서 기준 추정치 — 실제 연동 전 반드시 재확인 필요.
        """
        token = self._get_access_token()
        tr_id = FILL_TR_ID[self.account.env]

        resp = self._throttled_get(
            f"{self.domain}/uapi/overseas-stock/v1/trading/inquire-ccnl",
            headers={
                "authorization": f"Bearer {token}",
                "appkey": self.appkey,
                "appsecret": self.appsecret,
                "tr_id": tr_id,
            },
            params={
                "CANO": self.account.cano,
                "ACNT_PRDT_CD": self.account.acnt_prdt_cd,
                "PDNO": "%",
                "ORD_STRT_DT": order_date,
                "ORD_END_DT": order_date,
                "SLL_BUY_DVSN": "00",
                "CCLD_NCCS_DVSN": "00",
                "OVRS_EXCG_CD": exchange_code,
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": order_no,
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": "",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise ExchangeOrderError(data.get("msg_cd"), data.get("msg1"))

        for item in data.get("output", []):
            if item.get("odno") == order_no:
                return {
                    "order_qty": item.get("ft_ord_qty"),
                    "filled_qty": item.get("ft_ccld_qty"),
                    "filled_price": item.get("ft_ccld_unpr3"),
                }
        return None
