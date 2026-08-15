#!/bin/bash
# EC2(Ubuntu 기준, 거래브리지 서버와 같은 인스턴스)에서 실행.
# 사용법: cd monitoring-server && bash deploy/setup.sh
#
# targets.yaml에 감시 대상을 추가한 뒤에도 이 스크립트를 다시 실행해야
# 새 대상의 재시작 권한(sudoers)이 반영된다.
set -e

if [ "$(id -u)" -eq 0 ]; then
    echo "이 스크립트는 sudo 없이 일반 사용자로 실행하세요 (필요한 곳에서만 sudo를 씁니다)."
    echo "  올바른 실행:  bash deploy/setup.sh"
    exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env   # 봇 토큰이 들어가는 파일이라 남이 읽지 못하게 한다
    echo ".env 파일을 실제 값(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)으로 채운 뒤 다시 실행하세요."
    exit 1
fi
chmod 600 .env

# sudoers에 적을 경로와 실제로 실행될 경로가 달라지면 재시작이 조용히 실패하므로,
# 추측하지 않고 이 시스템에서 실제로 쓰이는 경로를 그대로 사용한다.
SYSTEMCTL="$(command -v systemctl || true)"
if [ -z "$SYSTEMCTL" ]; then
    echo "systemctl을 찾을 수 없습니다. systemd가 동작하는 리눅스에서 실행하세요."
    exit 1
fi

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

SERVICE_USER="$(whoami)"

# 재시작 권한은 targets.yaml에 적힌 대상에 대해서만 자동 생성한다.
# (여기서 targets.yaml이나 .env가 잘못돼 있으면 아래 명령이 실패하며 이유를 알려준다)
SERVICES="$(.venv/bin/python -c "
from app.targets import load_targets
for t in load_targets():
    print(t.systemd_service)
")"

if [ -z "$SERVICES" ]; then
    echo "targets.yaml에 활성화된 감시 대상이 없습니다. enabled: true 항목을 확인하세요."
    exit 1
fi

SUDOERS_FILE="/etc/sudoers.d/monitoring-restart"
TMP_SUDOERS="$(mktemp)"
{
    echo "# monitoring-server가 감시 대상을 재시작할 때만 쓰는 권한."
    echo "# deploy/setup.sh가 targets.yaml을 읽어 자동 생성함 - 직접 수정하지 말 것."
    for svc in $SERVICES; do
        echo "$SERVICE_USER ALL=(root) NOPASSWD: $SYSTEMCTL restart $svc"
    done
} > "$TMP_SUDOERS"

# 반드시 설치 "전에" 검증할 것 - 문법이 틀린 파일이 /etc/sudoers.d/에 들어가면
# 그 순간부터 이 서버의 sudo 전체가 동작하지 않는다(복구하려면 콘솔 접속 필요).
sudo visudo -c -f "$TMP_SUDOERS"
sudo install -m 0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_FILE"
rm -f "$TMP_SUDOERS"
echo "sudoers 설정 완료: $SUDOERS_FILE"
for svc in $SERVICES; do
    echo "  - $SYSTEMCTL restart $svc"
done

TMP_UNIT="$(mktemp)"
sed \
    -e "s#{{APP_DIR}}#$APP_DIR#g" \
    -e "s#{{USER}}#$SERVICE_USER#g" \
    deploy/monitoring.service > "$TMP_UNIT"
sudo install -m 0644 -o root -g root "$TMP_UNIT" /etc/systemd/system/monitoring.service
rm -f "$TMP_UNIT"

sudo systemctl daemon-reload
sudo systemctl enable monitoring
sudo systemctl restart monitoring

echo
echo "완료. 잠시 후 텔레그램으로 '모니터링을 시작합니다' 메시지가 오면 정상입니다."
echo "  상태 확인: sudo systemctl status monitoring"
echo "  로그 확인: journalctl -u monitoring -f   (또는 logs/monitor.log)"
