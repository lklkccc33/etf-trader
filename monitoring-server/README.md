# 모니터링 서버

거래브리지 서버(및 향후 운영서버)를 감시해서, 프로세스는 떠있지만 응답을 안 하는 hang
상태나 프로세스 다운을 감지하면 재시작을 시도하고 Telegram으로 알린다.

거래브리지 서버 자체의 `Restart=always`가 못 잡는 부분(응답 없음 감지 + 알림)을 보완하는
역할이며, **거래브리지 서버와 같은 EC2 인스턴스**에 별도 systemd 서비스로 배포한다.

## 동작 방식

5분(`CHECK_INTERVAL_SECONDS`)마다 각 대상에 대해 두 가지를 확인한다.

| 확인 항목 | 잡아내는 문제 |
| --- | --- |
| `GET /health`가 200인지 | 프로세스는 떠 있는데 응답이 없는 hang 상태 |
| `systemctl is-active`가 `active`인지 | 프로세스 자체가 죽거나 기동 실패한 상태 |

둘 다 확인해서 알림에 **어느 쪽이 문제인지** 적어준다 (조치 방법이 다르므로).
HTTP 확인은 순간적인 네트워크 오류로 오탐하지 않도록 짧은 간격으로 2회 시도한다.

비정상이면 최초 감지 시 알림을 보내고 `sudo systemctl restart <service>`를 실행한다.
그래도 계속 비정상이면 5분 간격으로 최대 3회까지 재시도하고, 한도를 넘기면 재시작을
중단하고 "수동 확인 필요" 알림을 보낸다.

### Telegram 알림이 오는 시점

| 알림 | 언제 |
| --- | --- |
| 🔎 시작 | 모니터링 서버가 뜰 때 (봇 설정이 제대로 됐는지 확인용) |
| 🚨 장애 감지 | 비정상을 처음 발견했을 때 (원인 포함) |
| ⚠️ 재시작 명령 실패 | 재시작 명령 자체가 실행되지 않았을 때 (주로 sudo 권한 문제) |
| ⛔ 재시작 포기 | 재시작 한도를 다 쓰고도 복구되지 않을 때 **(한 번만)** |
| ✅ 복구 | 다시 정상으로 돌아왔을 때 |
| 💚 생존 신호 | 하루 한 번 (`HEARTBEAT_INTERVAL_SECONDS`) |
| ⚠️ 설정 오류 | `targets.yaml`을 읽지 못했을 때 |

재시작을 시도할 때마다 알림을 보내지는 않는다. 포기 알림도 **한 번만** 보내고 그 뒤로는
조용히 있는다 (같은 장애로 5분마다 알림이 쌓이는 걸 막기 위해).
그래서 "장애 알림을 받았는데 복구 알림이 안 왔다" = 아직 안 고쳐진 상태다.

### 오작동을 막기 위한 장치

- **무한 재시작 방지**: 재시작 횟수를 1시간(`RESTART_WINDOW_SECONDS`) 단위로 세서
  최대 3회까지만 허용한다. 장애→복구→장애가 반복(플래핑)돼도 한도가 초기화되지 않는다.
- **재부팅 직후 오탐 방지**: 시작 후 60초(`STARTUP_GRACE_SECONDS`)는 점검하지 않는다.
  감시 대상도 같이 기동 중이기 때문.
- **수동 작업 중 끼어들기 방지**: 담당자가 직접 `systemctl restart`를 실행해서 systemd가
  `activating` 상태이면 판단을 미룬다 (최대 2주기).
- **알림 유실 방지**: 텔레그램 전송이 실패하면 재시도하고, 그래도 안 되면 큐에 넣어
  다음 주기에 다시 보낸다.
- **설정 오류로 감시가 멈추는 것 방지**: `targets.yaml`에 오타가 나도 프로세스가 죽지 않고,
  알림을 보낸 뒤 직전에 읽은 목록으로 감시를 계속한다.

## 감시 대상 추가하기 (예: 운영서버)

1. `targets.yaml`에 항목을 추가한다.

   ```yaml
     - name: operations-server
       health_url: http://localhost:8100/health
       systemd_service: operations-server
       enabled: true
   ```

2. `bash deploy/setup.sh`를 다시 실행한다.

2번을 빼먹으면 감시는 되지만 **재시작 권한이 없어서 재시작만 조용히 실패**한다.
(그 경우 ⚠️ 알림으로 알려주긴 한다.) `setup.sh`가 `targets.yaml`을 읽어 sudoers 파일을
다시 만들어주므로 sudoers를 직접 편집할 일은 없다.

> `/etc/sudoers.d/` 파일을 직접 고쳐야 할 일이 생기면 반드시 `sudo visudo -f <파일>`로
> 편집할 것. 문법이 틀린 파일이 들어가면 그 서버에서 sudo가 전부 동작하지 않게 된다.

## 배포 (EC2, 거래브리지 서버와 같은 인스턴스)

### 1. Telegram 봇 준비 (직접 해야 하는 부분)

봇 토큰은 credential이라 대신 만들어줄 수 없다. 직접 아래 순서로 진행:

1. Telegram에서 [@BotFather](https://t.me/BotFather)와 대화 시작 → `/newbot` 입력 →
   안내에 따라 봇 이름 설정 → 발급된 토큰을 복사 (`TELEGRAM_BOT_TOKEN`)
2. 만든 봇과 대화를 한 번 시작 (아무 메시지나 전송, 예: `/start`)
3. 브라우저에서 `https://api.telegram.org/bot<TOKEN>/getUpdates` 접속 →
   응답 JSON에서 `"chat":{"id": ...}` 값을 찾아 `TELEGRAM_CHAT_ID`로 사용
   - 그룹방에서 알림을 받고 싶다면 봇을 그룹에 초대한 뒤 그룹에서 메시지를 보내고
     동일한 방법으로 chat_id(그룹은 보통 음수)를 확인

### 2. 서버 설정

```bash
cd monitoring-server
cp .env.example .env
```

`.env`에 `TELEGRAM_BOT_TOKEN`과 `TELEGRAM_CHAT_ID`를 채운 뒤:

```bash
bash deploy/setup.sh
```

`setup.sh`가 하는 일 (sudo 없이 일반 사용자로 실행할 것):
- 가상환경 생성 + 의존성 설치
- `targets.yaml`을 읽어 각 대상의 `systemctl restart`만 비밀번호 없이 실행할 수 있도록
  `/etc/sudoers.d/monitoring-restart` 생성 (설치 전에 `visudo -c`로 검증)
- systemd 서비스 등록 및 실행

### 3. 확인

배포 직후 Telegram으로 **🔎 모니터링을 시작합니다** 메시지가 오면 봇 설정까지 정상이다.
안 오면 `.env`의 토큰/chat_id를 확인할 것.

```bash
sudo systemctl status monitoring
journalctl -u monitoring -f
# 또는
tail -f logs/monitor.log
```

## 로컬 개발/테스트

감지 → 재시작 → 포기 → 복구 흐름을 실제로 돌려보는 스크립트가 있다.
텔레그램을 보내거나 서버를 재시작하지는 않으므로 `.env` 없이도 실행된다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/smoke_test.py
```

실제로 감시 루프를 돌려보려면 `.env`를 채운 뒤 `.venv/bin/python monitor.py`.
로컬에는 systemd가 없으므로 대상은 계속 비정상으로 나오고 재시작 명령도 실패하는 것이
정상이다.

> `.env`에 값을 적을 때 값 뒤에 `# 주석`을 붙이지 말 것. systemd는 python-dotenv와 달리
> 주석을 떼어주지 않아서, 로컬에서만 동작하고 서버에서는 기동 실패하는 원인이 된다.
