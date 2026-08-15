"""서버가 실제로 도는지 손으로 확인해보는 스크립트.

사용법:
  1) 터미널 하나에서 서버 실행: python run_dev.py
  2) 다른 터미널에서:        python scripts/smoke_test.py

더미 KIS credential로 테스트 계좌를 하나 만들고, 세션 생성 → 주문 요청까지
HMAC 서명을 직접 계산해서 보내본다. 더미 credential이라 실제 KIS 호출은
실패하지만(status: ERROR), 서버가 크래시 없이 정상 흐름으로 처리하는지는
확인할 수 있다. 실제 KIS 계좌로 검증하려면 accounts 테이블의
encrypted_appkey/encrypted_appsecret을 진짜 값으로(app.crypto.encrypt로
암호화해서) 넣어야 한다.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from app import models
from app.config import settings
from app.database import SessionLocal

BASE_URL = os.environ.get("SMOKE_TEST_BASE_URL", "http://127.0.0.1:8000")


def signed_post(path: str, body: dict) -> requests.Response:
    raw = json.dumps(body).encode()
    ts = str(time.time())
    nonce = str(uuid.uuid4())
    message = f"{ts}.{nonce}.".encode() + raw
    signature = hmac.new(
        settings.OPERATIONS_API_SECRET.encode(), message, hashlib.sha256
    ).hexdigest()
    headers = {
        "content-type": "application/json",
        "X-API-Key": settings.OPERATIONS_API_KEY,
        "X-Timestamp": ts,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }
    return requests.post(BASE_URL + path, data=raw, headers=headers, timeout=10)


def get_or_create_test_account() -> str:
    db = SessionLocal()
    try:
        account = (
            db.query(models.Account)
            .filter(models.Account.cano == "00000001", models.Account.env == "VIRTUAL")
            .first()
        )
        if account is None:
            account = models.Account(
                account_id=str(uuid.uuid4()),
                exchange="KIS",
                env="VIRTUAL",
                cano="00000001",
                acnt_prdt_cd="01",
                encrypted_appkey="dummy",
                encrypted_appsecret="dummy",
            )
            db.add(account)
            db.commit()
            print(f"테스트 계좌 생성: {account.account_id}")
        else:
            print(f"기존 테스트 계좌 재사용: {account.account_id}")
        return account.account_id
    finally:
        db.close()


def main():
    print(f"서버: {BASE_URL}")

    try:
        health = requests.get(BASE_URL + "/health", timeout=5)
        print("헬스체크 ->", health.status_code, health.text)
    except requests.exceptions.ConnectionError:
        print("서버에 연결할 수 없습니다. 먼저 `python run_dev.py`로 서버를 띄워주세요.")
        return

    account_id = get_or_create_test_account()

    resp = signed_post(
        "/api/v1/sessions", {"account_id": account_id, "seed_money": "1000000"}
    )
    print("세션 생성 ->", resp.status_code, resp.text)
    if resp.status_code != 200:
        return
    session_id = resp.json()["session_id"]

    resp = signed_post(
        "/api/v1/orders",
        {
            "client_order_id": f"smoke-test-{uuid.uuid4()}",
            "session_id": session_id,
            "strategy_name": "smoke-test",
            "account_id": account_id,
            "exchange_code": "NASD",
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "volume": "1",
            "price": "100",
        },
    )
    print("주문 요청 ->", resp.status_code, resp.text)
    print(
        "\n더미 credential이라 status가 ERROR로 나오는 게 정상입니다 "
        "(order_errors 테이블에 원인이 기록됩니다)."
    )


if __name__ == "__main__":
    main()
