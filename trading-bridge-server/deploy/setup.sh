#!/bin/bash
# EC2(Ubuntu 기준)에서 한 번 실행하면 서비스 등록까지 끝나는 셋업 스크립트.
# 사용법: cd trading-bridge-server && bash deploy/setup.sh
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [ ! -f .env ]; then
    cp .env.example .env
    echo ".env 파일을 실제 값(DB, KIS 키, OPERATIONS_API_KEY 등)으로 채운 뒤 다시 실행하세요."
    exit 1
fi

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head

sudo sed \
    -e "s#{{APP_DIR}}#$APP_DIR#g" \
    -e "s#{{USER}}#$(whoami)#g" \
    deploy/trading-bridge.service > /tmp/trading-bridge.service
sudo mv /tmp/trading-bridge.service /etc/systemd/system/trading-bridge.service

sudo systemctl daemon-reload
sudo systemctl enable trading-bridge
sudo systemctl restart trading-bridge

echo "완료. 상태 확인: sudo systemctl status trading-bridge"
echo "로그 확인:      journalctl -u trading-bridge -f   (또는 logs/bridge.log)"
