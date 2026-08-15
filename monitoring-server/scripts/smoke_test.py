"""감지 → 재시작 → 포기 → 복구 흐름이 의도대로 도는지 확인하는 스크립트.

사용법:
  python scripts/smoke_test.py

실제로 텔레그램을 보내거나 서버를 재시작하지는 않는다. 텔레그램 전송과
systemctl 호출만 가짜로 바꿔치기하고, 헬스체크는 이 스크립트가 직접 띄운
로컬 HTTP 서버를 상대로 진짜 요청을 보낸다. 알림이 정확히 필요한 순간에만
(최초 감지 / 재시도 포기 / 복구) 나가는지까지 확인한다.

.env가 없어도 되도록 필요한 환경변수는 여기서 채운다.
"""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "smoke-test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")

from app import config  # noqa: E402
from app.checker import CheckResult  # noqa: E402
from app.engine import run_check_cycle  # noqa: E402
from app.state import TargetState  # noqa: E402
from app.targets import Target  # noqa: E402

PORT = 8123
failures = []


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {actual!r}" + ("" if ok else f" (기대값: {expected!r})"))
    if not ok:
        failures.append(label)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args):
        pass


def result(healthy=True, in_transition=False, reason="정상"):
    return CheckResult(healthy=healthy, in_transition=in_transition, reason=reason)


def main() -> None:
    httpd = HTTPServer(("127.0.0.1", PORT), _HealthHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    config.settings.MAX_RESTART_ATTEMPTS = 3
    config.settings.RESTART_COOLDOWN_SECONDS = 0  # 5분을 기다리지 않고 재시도 흐름 확인
    config.settings.RESTART_WINDOW_SECONDS = 3600

    target = Target("smoke", f"http://127.0.0.1:{PORT}/health", "smoke-service")
    down = Target("smoke", f"http://127.0.0.1:{PORT + 1}/health", "smoke-service")

    print("\n[1] 실제 HTTP 헬스체크 - 살아있는 서버는 정상 판정")
    from app.checker import check_target
    with patch("app.checker._systemd_status", return_value="active"):
        check("정상 판정", check_target(target).healthy, True)
    with patch("app.checker._systemd_status", return_value="active"):
        r = check_target(down)
        check("응답 없는 서버 판정", r.healthy, False)
        check("원인에 헬스체크 언급", "헬스체크" in r.reason, True)
    with patch("app.checker._systemd_status", return_value="failed"):
        r = check_target(target)
        check("프로세스 다운 판정", r.healthy, False)
        check("원인에 systemd 상태 언급", "systemd 상태 failed" in r.reason, True)

    state = TargetState()

    print("\n[2] 정상 상태 - 알림도 재시작도 없어야 함")
    with patch("app.engine.check_target", return_value=result()), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service", return_value=True) as rs:
        run_check_cycle(target, state)
        check("알림 횟수", tg.call_count, 0)
        check("재시작 횟수", rs.call_count, 0)

    print("\n[3] 최초 감지 - 알림 1회 + 즉시 재시작, 알림에 원인 포함")
    bad = result(healthy=False, reason="systemd 상태 failed")
    with patch("app.engine.check_target", return_value=bad), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service", return_value=True) as rs:
        run_check_cycle(target, state)
        check("알림 횟수", tg.call_count, 1)
        check("재시작 횟수", rs.call_count, 1)
        check("알림에 원인 포함", "systemd 상태 failed" in tg.call_args.args[0], True)

    print("\n[4] 계속 비정상 - 재시작은 하되 알림은 더 안 보냄")
    with patch("app.engine.check_target", return_value=bad), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service", return_value=True) as rs:
        run_check_cycle(target, state)
        run_check_cycle(target, state)
        check("추가 알림 횟수", tg.call_count, 0)
        check("누적 재시작 횟수", len(state.restart_times), 3)

    print("\n[5] 한도 소진 - 포기 알림 1회, 재시작 중단")
    with patch("app.engine.check_target", return_value=bad), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service", return_value=True) as rs:
        run_check_cycle(target, state)
        run_check_cycle(target, state)
        check("포기 알림 횟수", tg.call_count, 1)
        check("추가 재시작 횟수", rs.call_count, 0)
        check("포기 상태", state.gave_up, True)

    print("\n[6] 복구 - 복구 알림 1회, 장애 상태 해제")
    with patch("app.engine.check_target", return_value=result()), \
            patch("app.engine.send_telegram_message") as tg:
        run_check_cycle(target, state)
        check("복구 알림 횟수", tg.call_count, 1)
        check("장애 상태 해제", state.in_incident, False)
        check("포기 상태 해제", state.gave_up, False)

    print("\n[7] 플래핑 - 복구 후 재발해도 재시작 한도는 초기화되지 않아야 함")
    with patch("app.engine.check_target", return_value=bad), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service", return_value=True) as rs:
        run_check_cycle(target, state)
        check("재시작 안 함(한도 소진 유지)", rs.call_count, 0)
        check("알림 1회만", tg.call_count, 1)
        check("알림에 한도 소진 안내", "한도" in tg.call_args.args[0], True)

    print("\n[8] 재시작 명령 자체가 실패(sudo 권한 문제) - 즉시 별도 알림")
    sudo_state = TargetState()
    with patch("app.engine.check_target", return_value=bad), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service", return_value=False):
        run_check_cycle(target, sudo_state)
        messages = [c.args[0] for c in tg.call_args_list]
        check("알림 횟수(감지+명령실패)", len(messages), 2)
        check("sudo 안내 포함", any("sudoers" in m for m in messages), True)

    print("\n[9] systemd 상태 전환 중(담당자가 직접 재시작) - 끼어들지 않아야 함")
    transition_state = TargetState()
    transitioning = result(healthy=False, in_transition=True, reason="systemd 상태 전환 중(activating)")
    with patch("app.engine.check_target", return_value=transitioning), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service") as rs:
        run_check_cycle(target, transition_state)
        run_check_cycle(target, transition_state)
        check("알림 횟수", tg.call_count, 0)
        check("재시작 횟수", rs.call_count, 0)
    with patch("app.engine.check_target", return_value=transitioning), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service", return_value=True) as rs:
        run_check_cycle(target, transition_state)
        check("계속 전환 중이면 결국 장애로 판단", tg.call_count, 1)

    print("\n[10] MAX_RESTART_ATTEMPTS=0 (알림만 모드) - 재시작 없이 알림 1회만")
    notify_only = TargetState()
    config.settings.MAX_RESTART_ATTEMPTS = 0
    with patch("app.engine.check_target", return_value=bad), \
            patch("app.engine.send_telegram_message") as tg, \
            patch("app.engine.restart_service") as rs:
        run_check_cycle(target, notify_only)
        run_check_cycle(target, notify_only)
        check("알림 횟수", tg.call_count, 1)
        check("재시작 횟수", rs.call_count, 0)

    print("\n[11] 알림 전송 실패 시 유실되지 않고 다음 주기에 재전송")
    from app import telegram
    with patch("app.telegram._send_with_retry", return_value=(False, False)):
        telegram.send_telegram_message("테스트 알림")
        check("대기 큐에 보관", len(telegram._pending), 1)
    with patch("app.telegram._send_with_retry", return_value=(True, False)) as sender:
        telegram.flush_pending()
        check("다음 주기에 재전송됨", sender.call_count, 1)
        check("대기 큐 비워짐", len(telegram._pending), 0)

    httpd.shutdown()

    print("\n" + "=" * 55)
    if failures:
        print(f"실패 {len(failures)}건: {', '.join(failures)}")
        sys.exit(1)
    print("전부 통과")


if __name__ == "__main__":
    main()
