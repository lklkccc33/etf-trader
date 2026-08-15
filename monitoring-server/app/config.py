import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    """비어 있으면 시작 자체를 막는다.

    값이 비어 있어도 프로세스는 멀쩡히 뜨고 systemctl status도 초록불이라,
    "알림이 안 온다 = 아무 일도 없다"고 착각한 채 몇 주가 지나갈 수 있다.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name}이(가) 비어 있습니다. .env 파일을 확인하세요.")
    return value


def _number(name: str, default: str, minimum, cast):
    """숫자 설정값을 읽으면서 잘못된 값은 즉시 걸러낸다.

    오타(예: HEALTH_CHECK_RETRIES=0)가 조용히 통과하면 멀쩡한 서버를 계속
    비정상으로 오판해서 재시작시키는 사고가 나므로, 시작 시점에 바로 실패시킨다.
    """
    raw = os.environ.get(name, default).strip() or default
    try:
        value = cast(raw)
    except ValueError:
        # systemd의 EnvironmentFile은 python-dotenv와 달리 값 뒤의 `# 주석`을
        # 떼어주지 않는다. 로컬에서만 되고 서버에서 안 되는 사고의 단골 원인이라
        # 메시지에 힌트를 남긴다.
        hint = " (값 뒤에 # 주석을 붙이지 마세요)" if "#" in raw else ""
        raise ValueError(f"{name} 값이 올바른 숫자가 아닙니다: {raw!r}{hint}") from None
    if value < minimum:
        raise ValueError(f"{name} 값은 {minimum} 이상이어야 합니다 (현재: {value})")
    return value


class Settings:
    TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = _required("TELEGRAM_CHAT_ID")

    LOG_DIR = os.environ.get("LOG_DIR", "./logs")
    TARGETS_FILE = os.environ.get("TARGETS_FILE", "./targets.yaml")

    CHECK_INTERVAL_SECONDS = _number("CHECK_INTERVAL_SECONDS", "300", 1, int)

    # 재부팅 직후에는 감시 대상이 아직 뜨는 중이라 바로 점검하면 오탐이 난다.
    # 첫 점검 전에만 이만큼 기다린다.
    STARTUP_GRACE_SECONDS = _number("STARTUP_GRACE_SECONDS", "60", 0, int)

    # "아무 알림도 안 온다"가 "모니터링이 죽었다"와 구분되도록 살아있다는 신호를
    # 주기적으로 보낸다. 0이면 끔.
    HEARTBEAT_INTERVAL_SECONDS = _number("HEARTBEAT_INTERVAL_SECONDS", "86400", 0, int)

    HEALTH_CHECK_TIMEOUT_SECONDS = _number("HEALTH_CHECK_TIMEOUT_SECONDS", "5", 1, float)
    # 한 번의 점검 안에서 보내는 총 요청 횟수. 최소 1 —
    # 0이면 요청을 한 번도 보내지 않고 무조건 실패로 판단하게 된다.
    HEALTH_CHECK_ATTEMPTS = _number("HEALTH_CHECK_ATTEMPTS", "2", 1, int)
    HEALTH_CHECK_RETRY_DELAY_SECONDS = _number("HEALTH_CHECK_RETRY_DELAY_SECONDS", "3", 0, float)

    # 0으로 두면 자동 재시작을 하지 않고 알림만 보낸다.
    MAX_RESTART_ATTEMPTS = _number("MAX_RESTART_ATTEMPTS", "3", 0, int)
    RESTART_COOLDOWN_SECONDS = _number("RESTART_COOLDOWN_SECONDS", "300", 0, float)
    # 재시작 한도를 세는 기간. 장애→복구→장애가 반복(플래핑)될 때도
    # 이 기간 안에서는 MAX_RESTART_ATTEMPTS를 넘겨 재시작하지 않는다.
    RESTART_WINDOW_SECONDS = _number("RESTART_WINDOW_SECONDS", "3600", 1, float)

    # systemd가 상태 전환 중(activating)일 때는 판단을 미루되, 계속 이 상태면
    # 진짜 문제이므로 이 횟수를 넘기면 비정상으로 본다.
    MAX_TRANSITION_CYCLES = _number("MAX_TRANSITION_CYCLES", "2", 1, int)


settings = Settings()
