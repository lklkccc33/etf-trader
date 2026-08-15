import logging
import time

from app.config import settings
from app.engine import run_check_cycle
from app.logging_config import configure_logging
from app.state import TargetState
from app.targets import Target, load_targets
from app.telegram import flush_pending, send_telegram_message

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    logger.info("모니터링 서버 시작 (점검 주기 %d초)", settings.CHECK_INTERVAL_SECONDS)

    interval = settings.CHECK_INTERVAL_SECONDS
    interval_text = f"{interval // 60}분" if interval >= 60 else f"{interval}초"

    # 시작 알림은 "봇 토큰과 chat_id가 실제로 동작하는지"를 배포 시점에 바로
    # 확인해주는 역할도 한다. 이 메시지가 안 오면 설정이 틀린 것.
    send_telegram_message(f"🔎 [monitoring] 모니터링을 시작합니다. (점검 주기 {interval_text})")

    if settings.STARTUP_GRACE_SECONDS:
        # 재부팅 직후 감시 대상이 아직 기동 중일 때 오탐하지 않도록 잠시 기다린다.
        logger.info("기동 대기 %d초", settings.STARTUP_GRACE_SECONDS)
        time.sleep(settings.STARTUP_GRACE_SECONDS)

    states: dict[str, TargetState] = {}
    targets: list[Target] = []
    config_error_notified = False
    last_heartbeat = time.monotonic()

    while True:
        # 이전 주기에 네트워크 문제로 못 보낸 알림이 있으면 먼저 재전송한다.
        flush_pending()

        # targets.yaml은 매 주기마다 다시 읽어서 대상 추가/삭제가 재시작 없이 반영되게 한다.
        # 다만 이 파일에 오타가 나면 예외가 그대로 올라와 프로세스가 죽고,
        # systemd의 Restart=always와 맞물려 아무 알림 없이 무한 재시작만 반복하게 된다.
        # (감시가 멈춘 걸 아무도 모르는 상태가 가장 위험하다)
        # 그래서 여기서 잡아 알림을 보내고, 직전에 성공적으로 읽은 목록으로 감시를 계속한다.
        try:
            targets = load_targets()
            if config_error_notified:
                config_error_notified = False
                logger.info("targets.yaml 정상 복구됨")
                send_telegram_message("✅ [monitoring] targets.yaml을 다시 정상적으로 읽었습니다.")
        except Exception as e:
            logger.exception("targets.yaml을 읽지 못했습니다")
            if not config_error_notified:
                config_error_notified = True
                fallback = (
                    f"직전 목록({', '.join(t.name for t in targets)})으로 감시를 계속합니다."
                    if targets else "감시 대상이 없어 아무것도 감시하지 못하는 상태입니다."
                )
                send_telegram_message(
                    f"⚠️ [monitoring] targets.yaml을 읽지 못했습니다.\n\n{e}\n\n{fallback}"
                )

        for target in targets:
            state = states.setdefault(target.name, TargetState())
            try:
                run_check_cycle(target, state)
            except Exception:
                logger.exception("[%s] 점검 중 예상치 못한 오류", target.name)

        # 목록에서 빠진 대상의 상태는 정리한다 (지웠다 다시 추가했을 때
        # 예전 장애 상태를 이어받지 않도록).
        current = {t.name for t in targets}
        for stale in [name for name in states if name not in current]:
            del states[stale]

        now = time.monotonic()
        if settings.HEARTBEAT_INTERVAL_SECONDS and now - last_heartbeat >= settings.HEARTBEAT_INTERVAL_SECONDS:
            last_heartbeat = now
            names = ", ".join(t.name for t in targets) or "없음"
            send_telegram_message(f"💚 [monitoring] 정상 동작 중입니다. 감시 대상: {names}")

        time.sleep(settings.CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
