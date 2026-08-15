import logging
import time

from .checker import check_target
from .config import settings
from .restart import restart_service
from .state import TargetState
from .targets import Target
from .telegram import send_telegram_message

logger = logging.getLogger(__name__)

_SUDO_HINT = (
    "\n\n※ 재시작 명령 자체가 실행되지 않았습니다. sudo 권한 설정을 확인하세요"
    " (/etc/sudoers.d/monitoring-restart). 감시 대상을 추가한 뒤"
    " deploy/setup.sh를 다시 실행하지 않으면 이 상태가 됩니다."
)


def _budget_left(state: TargetState, now: float) -> int:
    state.prune_restarts(now, settings.RESTART_WINDOW_SECONDS)
    return settings.MAX_RESTART_ATTEMPTS - len(state.restart_times)


def _do_restart(target: Target, state: TargetState, now: float) -> None:
    state.restart_times.append(now)
    ok = restart_service(target)
    state.last_restart_failed = not ok
    if not ok:
        # 서비스 장애와 권한 설정 오류는 담당자가 할 일이 전혀 다르므로 바로 알린다.
        # (15분 뒤 "3회 재시도 실패" 알림만 받으면 엉뚱한 곳을 파게 된다)
        send_telegram_message(f"⚠️ [{target.name}] 재시작 명령을 실행하지 못했습니다.{_SUDO_HINT}")


def _give_up(target: Target, state: TargetState) -> None:
    if state.gave_up:
        return
    state.gave_up = True
    logger.error("[%s] 재시작 한도 소진, 재시작 중단", target.name)
    message = (
        f"⛔ [{target.name}] {settings.MAX_RESTART_ATTEMPTS}회 재시작했지만 계속 실패했습니다. "
        f"자동 재시작을 중단합니다. 수동 확인이 필요합니다."
    )
    if state.last_restart_failed:
        message += _SUDO_HINT
    send_telegram_message(message)


def run_check_cycle(target: Target, state: TargetState) -> None:
    # 쿨다운/한도 계산에는 monotonic을 쓴다 — NTP가 시계를 뒤로 돌리면
    # time.time() 기준 경과시간이 음수가 되어 재시작이 영영 안 일어난다.
    now = time.monotonic()
    result = check_target(target)

    if result.in_transition:
        state.transition_cycles += 1
        if state.transition_cycles <= settings.MAX_TRANSITION_CYCLES:
            logger.info("[%s] %s — 판단 보류", target.name, result.reason)
            return
        logger.warning("[%s] 상태 전환이 %d주기째 끝나지 않음", target.name, state.transition_cycles)
    else:
        state.transition_cycles = 0

    if result.healthy:
        if state.in_incident:
            logger.info("[%s] 정상 복구됨", target.name)
            send_telegram_message(f"✅ [{target.name}] 정상 상태로 복구되었습니다.")
        state.clear_incident()
        return

    if not state.in_incident:
        state.in_incident = True
        logger.warning("[%s] 비정상 상태 감지: %s", target.name, result.reason)

        budget = _budget_left(state, now)
        if settings.MAX_RESTART_ATTEMPTS == 0:
            detail = "자동 재시작은 꺼져 있습니다(MAX_RESTART_ATTEMPTS=0). 수동 확인이 필요합니다."
        elif budget <= 0:
            window_min = int(settings.RESTART_WINDOW_SECONDS // 60)
            detail = (
                f"최근 {window_min}분 안에 재시작 한도({settings.MAX_RESTART_ATTEMPTS}회)를 "
                f"이미 소진해 자동 재시작을 하지 않습니다. 수동 확인이 필요합니다."
            )
        else:
            detail = "재시작을 시도합니다."

        send_telegram_message(f"🚨 [{target.name}] 비정상 상태가 감지되었습니다.\n\n원인: {result.reason}\n\n{detail}")

        if settings.MAX_RESTART_ATTEMPTS == 0 or budget <= 0:
            state.gave_up = True
            return

        _do_restart(target, state, now)
        return

    if state.gave_up:
        return

    if _budget_left(state, now) <= 0:
        _give_up(target, state)
        return

    last_restart = max(state.restart_times, default=None)
    if last_restart is None or now - last_restart >= settings.RESTART_COOLDOWN_SECONDS:
        _do_restart(target, state, now)
