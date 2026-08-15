import logging
import subprocess
import time
from dataclasses import dataclass

import requests

from .config import settings
from .targets import Target

logger = logging.getLogger(__name__)

# systemd가 상태를 바꾸는 중이라 아직 정상/비정상을 판단하면 안 되는 상태들.
# (예: 담당자가 직접 systemctl restart를 실행한 직후)
_TRANSITIONAL = ("activating", "deactivating", "reloading")


@dataclass(frozen=True)
class CheckResult:
    healthy: bool
    in_transition: bool
    reason: str


def _check_health(target: Target) -> tuple[bool, str]:
    last_reason = "확인 실패"
    for attempt in range(1, settings.HEALTH_CHECK_ATTEMPTS + 1):
        try:
            resp = requests.get(target.health_url, timeout=settings.HEALTH_CHECK_TIMEOUT_SECONDS)
            if resp.status_code == 200:
                return True, "정상"
            last_reason = f"헬스체크 HTTP {resp.status_code}"
        except requests.Timeout:
            # 프로세스는 살아있는데 응답만 없는 상태 — 이 모니터링 서버의 존재 이유.
            last_reason = f"헬스체크 응답 없음(타임아웃 {settings.HEALTH_CHECK_TIMEOUT_SECONDS}초)"
        except requests.RequestException as e:
            last_reason = f"헬스체크 연결 실패({type(e).__name__})"

        logger.warning(
            "[%s] %s (%d/%d)",
            target.name, last_reason, attempt, settings.HEALTH_CHECK_ATTEMPTS,
        )
        if attempt < settings.HEALTH_CHECK_ATTEMPTS:
            time.sleep(settings.HEALTH_CHECK_RETRY_DELAY_SECONDS)

    return False, last_reason


def _systemd_status(target: Target) -> str | None:
    """systemctl is-active 결과 문자열. 명령 자체가 실패하면 None."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", target.systemd_service],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):
        logger.exception("[%s] systemctl is-active 실행 실패", target.name)
        return None


def check_target(target: Target) -> CheckResult:
    """헬스체크와 systemd 상태를 모두 확인한다.

    둘 중 하나만 보고 단축 평가하지 않는 이유: "프로세스가 죽었다"와
    "프로세스는 떠 있는데 응답이 없다"는 담당자가 취할 조치가 다르므로
    알림에 어느 쪽인지 적어줘야 한다.
    """
    health_ok, health_reason = _check_health(target)
    status = _systemd_status(target)

    if status in _TRANSITIONAL:
        return CheckResult(healthy=False, in_transition=True, reason=f"systemd 상태 전환 중({status})")

    problems = []
    if not health_ok:
        problems.append(health_reason)
    if status is None:
        problems.append("systemctl 상태를 확인하지 못함")
    elif status != "active":
        problems.append(f"systemd 상태 {status}")

    if problems:
        return CheckResult(healthy=False, in_transition=False, reason=" / ".join(problems))
    return CheckResult(healthy=True, in_transition=False, reason="정상")
