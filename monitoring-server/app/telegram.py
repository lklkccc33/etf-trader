import logging
import time
from collections import deque

import requests

from .config import settings

logger = logging.getLogger(__name__)

_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

# 장애 알림은 상황당 한 번만 보내므로, 그 한 번이 일시적인 네트워크 오류로
# 유실되면 담당자가 장애를 영영 모르게 된다. 즉시 재시도하고, 그래도 실패하면
# 큐에 넣어 다음 점검 주기에 다시 보낸다.
_MAX_SEND_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 3

_pending: deque[str] = deque(maxlen=20)


def _send_once(text: str) -> tuple[bool, bool]:
    """(성공 여부, 재시도해도 소용없는 오류인지)를 돌려준다."""
    try:
        resp = requests.post(
            _API_URL,
            json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except requests.RequestException as e:
        logger.warning("텔레그램 전송 실패: %s", e)
        return False, False

    if resp.status_code == 200:
        return True, False

    # 4xx는 토큰/chat_id가 잘못된 설정 문제라 재시도해도 절대 성공하지 않는다.
    if 400 <= resp.status_code < 500:
        logger.error(
            "텔레그램 설정 오류로 전송 실패: status=%s body=%s "
            "— TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID를 확인하세요.",
            resp.status_code, resp.text,
        )
        return False, True

    logger.warning("텔레그램 전송 실패: status=%s body=%s", resp.status_code, resp.text)
    return False, False


def _send_with_retry(text: str) -> tuple[bool, bool]:
    for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
        ok, permanent = _send_once(text)
        if ok or permanent:
            return ok, permanent
        logger.warning("텔레그램 전송 재시도 (%d/%d)", attempt, _MAX_SEND_ATTEMPTS)
        if attempt < _MAX_SEND_ATTEMPTS:
            time.sleep(_RETRY_DELAY_SECONDS)
    return False, False


def send_telegram_message(text: str) -> bool:
    # 텔레그램 전송 실패로 감시 루프 자체가 죽으면 안 되므로 예외를 삼키고 로그만 남긴다.
    ok, permanent = _send_with_retry(text)
    if ok:
        return True
    if permanent:
        return False

    _pending.append(text)
    logger.error("텔레그램 전송 실패 — 다음 주기에 재전송합니다. 대기 중인 알림 %d건", len(_pending))
    return False


def flush_pending() -> None:
    """이전 주기에 못 보낸 알림을 다시 보낸다. 매 점검 주기 시작에 호출."""
    if not _pending:
        return

    logger.info("미전송 알림 %d건 재전송 시도", len(_pending))
    for _ in range(len(_pending)):
        text = _pending.popleft()
        ok, permanent = _send_with_retry(text)
        if ok or permanent:
            continue
        # 여전히 안 되면 순서를 지켜 되돌려 놓고 다음 주기를 기다린다.
        _pending.appendleft(text)
        return
