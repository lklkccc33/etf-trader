import logging
import subprocess

from .targets import Target

logger = logging.getLogger(__name__)


def restart_service(target: Target) -> bool:
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", target.systemd_service],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            logger.error(
                "[%s] 재시작 명령 실패: returncode=%s stderr=%s",
                target.name, result.returncode, result.stderr.strip(),
            )
            return False
        logger.info("[%s] 재시작 명령 실행됨", target.name)
        return True
    except (subprocess.SubprocessError, OSError):
        logger.exception("[%s] 재시작 명령 실행 중 오류", target.name)
        return False
