# 거래브리지 서버 — 프로젝트 문서

> 운영서버(전략 실행 주체)와 한국투자증권(KIS) 해외주식 API 사이를 중계하는 서버.
> REST API로 주문을 받아 KIS로 전달하고, 체결 여부를 확인해 거래를 기록하며,
> 실시간/정기 리포트와 로그를 남긴다.

---

## 1. 전체 아키텍처

```
운영서버 (전략 실행)
   │  HTTPS + HMAC 서명된 REST 요청
   ▼
거래브리지 서버 (FastAPI, 이 프로젝트)
   │  IP 허용목록 → 서명 검증 → 비즈니스 로직
   ├─→ MySQL (계좌/세션/거래기록/nonce 저장)
   ├─→ KIS Open API (주문/잔고/체결조회)
   ├─→ reports/ (실시간·정기 리포트 파일)
   └─→ logs/ (날짜별 로그 파일)
```

![거래브리지 서버 아키텍처](images/architecture.svg)

- **배포 형태**: EC2 한 대 + systemd (Docker/ECS 아님 — 유지관리 단순화를 위해 선택)
- **DB**: MySQL (SQLAlchemy + Alembic 마이그레이션)
- **거래소**: 현재는 한국투자증권(KIS)만 지원, 다른 거래소를 추가할 수 있도록 어댑터 구조로 분리돼 있음
- **동시성 모델**: 기본적으로 **워커(프로세스) 1개**로 운영. 여러 워커를 띄워도 안전하게 동작하도록 일부 안전장치(뒤에서 설명)는 넣어뒀지만, 기본 권장 배포는 단일 워커.

---

## 2. 디렉토리 구조

```
trading-bridge-server/
├── app/
│   ├── main.py              # FastAPI 앱 생성, 미들웨어/라우터 등록, /health
│   ├── config.py             # 환경변수 로딩 (Settings)
│   ├── database.py           # SQLAlchemy 엔진/세션
│   ├── models.py             # DB 테이블 정의 (ORM 모델)
│   ├── schemas.py            # API 요청/응답 스키마 (Pydantic)
│   ├── deps.py                # FastAPI DB 세션 의존성
│   ├── auth.py                 # HMAC 서명 검증 미들웨어(의존성)
│   ├── ip_allowlist.py         # VPC IP 허용목록 미들웨어
│   ├── crypto.py               # 계좌 키/토큰 암호화(Fernet)
│   ├── rate_limiter.py         # KIS 호출 속도 제한
│   ├── fills.py                 # 주문 체결 여부 확인 로직
│   ├── reports.py               # 실시간/정기 리포트 생성
│   ├── scheduler.py             # 정기 작업(리포트, heartbeat, 체결 재확인 등)
│   ├── scheduler_lock.py        # 멀티워커 환경에서 스케줄러 중복 실행 방지
│   ├── logging_config.py        # 로그 파일/콘솔 설정
│   ├── exchanges/                # 거래소 연동 (어댑터 패턴)
│   │   ├── base.py               # 추상 인터페이스 (ExchangeClient)
│   │   ├── kis.py                 # KIS 구현체
│   │   └── registry.py             # account.exchange → 구현체 매핑
│   └── routers/
│       ├── sessions.py            # POST /api/v1/sessions
│       ├── orders.py               # POST /api/v1/orders
│       └── balance.py               # GET  /api/v1/balance/{account_id}
├── alembic/                    # DB 마이그레이션 스크립트
├── deploy/
│   ├── trading-bridge.service    # systemd 유닛 파일
│   └── setup.sh                    # EC2 셋업 스크립트
├── scripts/
│   └── generate_dev_cert.py        # 로컬 TLS 테스트용 자체서명 인증서 생성
├── run_dev.py                   # 로컬 개발 서버 실행 스크립트
├── requirements.txt
└── .env.example
```

---

## 3. 핵심 작동 흐름

### 3.1 계좌 등록 (아직 API로 노출 안 됨 — DB에 직접 insert)

`accounts` 테이블에 KIS 앱키/시크릿을 **암호화해서** 저장한다. 현재는 이 과정이
API로 노출돼 있지 않고, 운영자가 스크립트로 직접 넣는 방식이다.

- `env`: `REAL`(실전투자) 또는 `VIRTUAL`(모의투자) — 같은 계좌번호라도 실전/모의를
  다른 레코드로 등록 가능
- `cano` + `acnt_prdt_cd`: 계좌번호를 앞 8자리 / 뒤 2자리로 나눠 저장 (KIS API 규격)
- `encrypted_appkey` / `encrypted_appsecret`: `app/crypto.py`의 Fernet 대칭키로 암호화

### 3.2 세션 시작 — `POST /api/v1/sessions`

전략이 거래를 시작할 때 세션을 하나 만든다. `seed_money`(시드머니)를 기준으로
이후 수익률을 계산한다. 세션은 "이 계좌로 이 시점부터 거래를 시작한다"는
논리적 구획이며, 이후 주문들은 이 `session_id`에 연결된다.

### 3.3 주문 처리 — `POST /api/v1/orders`  (가장 중요한 흐름)

```
1. 서명 검증 (auth.py) + IP 허용목록 (ip_allowlist.py)
2. client_order_id로 중복 주문인지 확인
   → 이미 성공 기록이 있으면 KIS를 다시 호출하지 않고 그 결과를 그대로 반환
3. KIS에 주문 접수 요청 (exchanges/kis.py: place_order)
   → 실패하면 order_errors 테이블에 기록하고 REJECTED/ERROR 응답
4. trade_records에 "접수됨(PENDING)" 상태로 기록
5. 최대 3회, 1초 간격으로 체결 여부를 짧게 폴링 (fills.py: check_fill)
   → 시장가 주문처럼 즉시 체결되는 경우 여기서 바로 잡힘
6. 여기서 못 잡으면 PENDING으로 남기고, 3분마다 도는 백그라운드 작업이 이어서 확인
7. KIS 잔고를 재조회해 계좌 현재 밸런스를 리포트 파일에 반영
8. 응답 반환 (status=ACCEPTED, fill_status=PENDING/FILLED/PARTIAL 등)
```

![주문 처리 시퀀스](images/order-sequence.svg)

**중요한 개념 하나**: KIS의 주문 API가 성공 응답을 줬다는 건 "주문이 **접수**됐다"는
뜻이지 "**체결**됐다"는 뜻이 아니다. 이 서버는 그 둘을 명확히 구분한다.

| 필드 | 의미 |
|---|---|
| `status` | `ACCEPTED`(접수 성공) / `REJECTED`(거래소가 거부) / `ERROR`(우리 쪽 처리 실패) |
| `fill_status` | `PENDING`(아직 체결 확인 안 됨) / `FILLED`(전량 체결) / `PARTIAL`(부분 체결) / `CANCELLED` |
| `filled_price`, `filled_volume` | **실제 체결된** 가격/수량 (아직 확인 전이면 null) |
| `duplicate` | `true`면 이번 요청이 새로 처리된 게 아니라 이전에 성공한 주문의 결과를 그대로 돌려준 것 |

### 3.4 중복 주문 방지 (idempotency)

운영서버가 네트워크 타임아웃 등으로 같은 주문을 재전송할 수 있다. 이를 위해
`OrderRequest`에 `client_order_id`(운영서버가 생성하는 고유 ID)를 필수로 받는다.

- `trade_records`에 `(account_id, client_order_id)` 유니크 제약이 걸려 있음
- 같은 `client_order_id`로 이미 **성공(접수 확인)** 기록이 있으면 → KIS를 다시
  호출하지 않고 그때 저장된 응답을 그대로 반환
- `REJECTED`/`ERROR`였던 요청은 재시도를 **허용**함 — 그 경우 KIS에 실제로 주문이
  들어갔는지 확신할 수 없어서, 막는 것보다 재시도를 허용하는 게 안전하다고 판단

> ⚠️ 이건 HMAC 서명의 nonce 재전송 방지(3.6)와는 다른 층위의 방지책이다.
> nonce는 "정확히 같은 HTTP 요청"을 막고, `client_order_id`는 "같은 논리적 주문"을
> 다른 서명으로 재전송해도 막는다.

### 3.5 리포트

**실시간 리포트** — 주문이 접수될 때마다 `reports/{account_id}-latest-status.json`을
KIS 잔고 재조회 결과로 갱신한다. (holdings, cash_balance, total_value, return_rate)

**정기 리포트** — `scheduler.py`가 월간(매월 1일)/분기(1·4·7·10월 1일)/연간(1월 1일)에
`reports/{전략명}-{account_id}-{주기}-report.csv`를 생성한다. 내용은
`balance, return_rate, generated_at`.

> 한계: 같은 계좌를 여러 전략이 공유하면 KIS 잔고조회가 계좌 단위라 전략별로
> 밸런스가 분리되지 않는다. 리포트의 balance는 항상 "그 시점 계좌 전체 밸런스".

### 3.6 인증 — HMAC 서명 (`app/auth.py`)

모든 `/api/v1/*` 요청은 아래 4개 헤더가 필요하다.

| 헤더 | 설명 |
|---|---|
| `X-API-Key` | 운영서버 식별용 고정 키 (`.env`의 `OPERATIONS_API_KEY`) |
| `X-Timestamp` | 요청 시각(유닉스 타임, 초). 서버 시각과 300초 이상 차이나면 거부 |
| `X-Nonce` | 요청마다 새로 생성하는 임의 문자열 (UUID 권장) |
| `X-Signature` | `HMAC-SHA256(OPERATIONS_API_SECRET, "{timestamp}.{nonce}." + body)`의 hex |

서명 검증 순서: API Key 비교 → 타임스탬프 범위 확인 → **nonce를 DB(`used_nonces`
테이블)에 원자적으로 예약**(이미 있으면 401) → body로 서명 재계산 후 비교.
같은 nonce로 두 번 요청하면 두 번째는 무조건 `401 replayed nonce`.

### 3.7 배경 작업 (scheduler.py)

| 작업 | 주기 |
|---|---|
| heartbeat 로그 | 1시간마다 |
| 미체결(PENDING) 주문 재확인 | 3분마다 (24시간 지난 주문은 더 이상 확인 안 함) |
| 오래된 nonce 정리 | 10분마다 (1시간 지난 것 삭제) |
| 월간 리포트 | 매월 1일 00:10 |
| 분기 리포트 | 1·4·7·10월 1일 00:20 |
| 연간 리포트 | 1월 1일 00:30 |

워커를 여러 개 띄워도 이 작업들은 MySQL `GET_LOCK` 기반 락을 쥔 워커 **하나만**
실행한다 (`scheduler_lock.py`). 락을 쥔 워커가 죽으면 자동으로 풀려서 다른
워커가 이어받는다.

---

## 4. API 레퍼런스

### `POST /api/v1/sessions`

```json
// 요청
{ "account_id": "uuid", "seed_money": "1000000" }

// 응답 200
{ "session_id": "uuid", "started_at": "2026-08-14T07:07:24" }
```

### `POST /api/v1/orders`

```json
// 요청
{
  "client_order_id": "운영서버가 생성하는 고유 ID",
  "session_id": "uuid",
  "strategy_name": "momentum-etf",
  "account_id": "uuid",
  "exchange_code": "NASD",       // NASD | NYSE | AMEX
  "ticker": "AAPL",
  "side": "BUY",                  // BUY | SELL
  "order_type": "LIMIT",          // LIMIT | MARKET
  "volume": "1",
  "price": "150.00"               // MARKET 주문이어도 필드는 필요(내부적으로 무시됨)
}

// 응답 200 (접수 + 체결 확인까지 완료된 경우)
{
  "status": "ACCEPTED",
  "fill_status": "FILLED",
  "kis_order_no": "TESTORD1",
  "filled_price": "150.5000",
  "filled_volume": "1.0000",
  "balance": "1010000.0000",
  "timestamp": "2026-08-14T07:38:35",
  "duplicate": false
}
```

### `GET /api/v1/balance/{account_id}?exchange_code=NASD&currency_code=USD`

KIS 잔고조회 원본 응답을 그대로 반환한다.

### `GET /health`

인증/IP 필터 없이 항상 접근 가능한 헬스체크. 로드밸런서나 systemd에서 사용.

---

## 5. 데이터베이스 스키마

| 테이블 | 역할 |
|---|---|
| `accounts` | 계좌 정보 (거래소, 실전/모의, 계좌번호, 암호화된 앱키/시크릿) |
| `token_cache` | KIS access_token 캐시 (계좌당 1개, 암호화 저장) |
| `trade_sessions` | 거래 세션 (계좌, 시드머니, 시작시각) |
| `trade_records` | 개별 주문/거래 기록 (접수·체결 정보 포함) |
| `order_errors` | 실패한 주문 요청 기록 (원인 추적용) |
| `used_nonces` | HMAC 재전송 방지용 nonce 기록 |

`trade_records`의 핵심 컬럼:

```
record_id, session_id, account_id, client_order_id(중복방지 키)
strategy_name, traded_at, exchange_code, ticker, side
volume, price, value            ← 요청한 주문 내용
balance                          ← 주문 직후 재조회한 계좌 밸런스
kis_order_no                     ← KIS가 부여한 주문번호
fill_status, filled_volume, filled_price, fill_checked_at  ← 실제 체결 정보
```

마이그레이션 이력 (`alembic/versions/`):
1. `0001` 초기 스키마
2. `0002` `trade_records.balance`를 nullable로 변경 (리포트 갱신 실패 시 0 대신 NULL 저장)
3. `0003` 체결 추적 컬럼 + `client_order_id` 중복방지 컬럼 추가
4. `0004` `used_nonces` 테이블 추가

---

## 6. 환경변수 (`.env`)

| 변수 | 필수 | 설명 |
|---|---|---|
| `DATABASE_URL` | ✅ | `mysql+pymysql://user:pw@host:3306/db` 형식 |
| `ENCRYPTION_KEY` | ✅ | Fernet 키. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`로 생성 |
| `OPERATIONS_API_KEY` | ✅ | 운영서버와 공유하는 API 키 |
| `OPERATIONS_API_SECRET` | ✅ | 운영서버와 공유하는 HMAC 서명용 시크릿 |
| `REPORTS_DIR` | - | 리포트 파일 저장 경로 (기본 `./reports`) |
| `LOG_DIR` | - | 로그 파일 저장 경로 (기본 `./logs`) |
| `TRUSTED_NETWORKS` | - | 콤마로 구분한 CIDR 목록. 비우면 IP 필터링 안 함 (로컬 개발용). 운영에서는 운영서버 IP/대역 지정 필수 |
| `KIS_RATE_LIMIT_PER_SECOND` | - | KIS 호출 속도 제한 (기본 5회/초, 실제 계약 한도로 조정 필요) |

`app/config.py`에 하드코딩된 값(환경변수 아님):
- `KIS_REAL_DOMAIN` = `https://openapi.koreainvestment.com:9443`
- `KIS_VIRTUAL_DOMAIN` = `https://openapivts.koreainvestment.com:29443`

---

## 7. 실행 방법

### 로컬 개발

```bash
cd trading-bridge-server
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # (Linux/Mac: .venv/bin/pip)
cp .env.example .env      # 값 채우기
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python run_dev.py     # http://127.0.0.1:8000
```

- `certs/cert.pem`, `certs/key.pem`이 있으면 자동으로 HTTPS로 뜸
  (`python scripts/generate_dev_cert.py`로 생성)
- Swagger 문서: `http://127.0.0.1:8000/docs`

### 실제 배포 (EC2 + systemd)

```bash
git clone <repo> trading-bridge-server   # 또는 코드 업로드
cd trading-bridge-server
cp .env.example .env      # 실제 값 채우기
bash deploy/setup.sh      # venv 생성 → 마이그레이션 → systemd 등록/시작
```

운영 명령:
```bash
sudo systemctl status trading-bridge     # 상태 확인
sudo systemctl restart trading-bridge    # 코드 수정 후 재시작
journalctl -u trading-bridge -f          # 실시간 로그 (logs/bridge.log와 동일 내용)
```

`deploy/trading-bridge.service`는 `Restart=always`로 설정돼 있어 프로세스가
죽어도 5초 뒤 자동 재시작된다.

---

## 8. 로깅

- `logs/bridge.log` — 자정마다 로테이션(파일명에 날짜가 붙음), 90일 보관
- HTTP 요청 접근 로그(uvicorn), 애플리케이션 로그, APScheduler 로그가 전부 이
  파일 하나에 모인다
- 매시간 `heartbeat ok uptime=...` 로그로 서버 생존을 알 수 있음
- 콘솔(stdout)에도 동일하게 출력되므로 `journalctl`로도 확인 가능

---

## 9. 보안

| 요구사항 | 구현 방법 |
|---|---|
| 거래소 키 암호화 저장 | `app/crypto.py` — Fernet 대칭키 암호화 (`ENCRYPTION_KEY`) |
| 동일 VPC 지정서버만 연결 | ① AWS Security Group(인프라) ② `TrustedNetworkMiddleware`(앱 레벨 IP 허용목록) — 이중 방어 |
| secure REST API 연결 | HMAC-SHA256 서명 + nonce 재전송 방지(DB 기반) + TLS(자체서명 인증서, `scripts/generate_dev_cert.py`) |

---

## 10. 문제 상황별 대처 방법

| 상황 | 서버 동작 | 확인 방법 |
|---|---|---|
| KIS가 주문을 거부(`rt_cd != "0"`) | `status: REJECTED` 응답, `order_errors`에 원인 기록 | `order_errors.error_code`, `error_message` |
| 우리 쪽 예외(암복호화 실패, 네트워크 오류 등) | `status: ERROR` 응답, 스택트레이스는 로그에만, `order_errors`에 `INTERNAL_ERROR`로 기록 | `logs/bridge.log`에서 `order submission failed unexpectedly` 검색 |
| 주문은 접수됐는데 리포트 갱신(잔고 재조회)이 실패 | 주문 자체는 정상 처리(재시도 위험 방지), `balance` 필드만 `null` | `logs/bridge.log`에서 `realtime report update failed` 검색 |
| 같은 주문이 재전송됨(`client_order_id` 동일) | KIS를 다시 호출하지 않고 이전 결과 반환, `duplicate: true` | 응답의 `duplicate` 필드 확인 |
| 정확히 같은 서명 요청이 재전송됨(nonce 동일) | `401 replayed nonce` | - |
| 서명이 안 맞음 | `401 invalid signature` | 운영서버의 서명 생성 로직(타임스탬프/nonce/바디 순서) 확인 |
| 허용되지 않은 IP에서 접근 | `403 forbidden` | `TRUSTED_NETWORKS` 설정 확인, 로그에 차단된 IP 기록됨 |
| 주문이 접수됐지만 아직 체결 안 됨(지정가 미체결 등) | `fill_status: PENDING` 응답, 3분마다 백그라운드에서 재확인 | `trade_records.fill_status`, `fill_checked_at` |
| 정기 리포트 중 특정 계좌만 실패 | 그 계좌만 건너뛰고 나머지는 정상 생성 | `logs/bridge.log`에서 `periodic report ... failed` 검색 |
| 프로세스가 죽음 | systemd가 5초 뒤 자동 재시작 | `sudo systemctl status trading-bridge` |
| 여러 워커를 띄운 경우 스케줄 작업 중복 실행 우려 | MySQL `GET_LOCK`으로 한 워커만 실행, 나머지는 대기 | 로그에 `scheduler started (lock acquired)` 또는 `scheduler lock held by another worker` |

---

## 11. 알려진 한계 (2026-08-14 기준)

- **KIS 실제 API로 검증 안 됨**: tr_id 값, hashkey 필요 여부, 체결내역조회
  (`inquire-ccnl`)의 파라미터/응답 필드명은 공식 문서 기준 추정치. 모의투자
  계좌 발급 후 실제 검증 필요 (코드 곳곳에 `재확인 필요` 주석으로 표시해둠)
- **자동화된 테스트 없음**: 지금까지 모든 검증은 수동 스크립트로 진행. pytest
  같은 회귀 테스트 스위트가 없어서 코드 변경 후 재검증이 수동임
- **실제 EC2/systemd 배포 미검증**: 개발 환경이 Windows라 systemctl을 직접
  실행해보지 못함. 코드/설정 파일은 준비돼 있으나 실제 리눅스 서버에서 최종
  확인 필요
- **계좌 등록 API 없음**: 현재 `accounts` 테이블은 스크립트로 직접 넣어야 함
- **한 계좌를 여러 전략이 공유할 때 리포트가 전략별로 분리되지 않음**
  (KIS 잔고조회가 계좌 단위이기 때문)
- **Rate limiter는 프로세스별로 독립적**: 워커를 여러 개 띄우면 실제 총 호출
  빈도는 워커 수만큼 늘어날 수 있음 (그래서 기본은 워커 1개 권장)

---

## 12. 용어 정리

| 용어 | 의미 |
|---|---|
| 접수(ACCEPTED) | KIS가 주문 요청을 정상적으로 받아들임. 체결을 보장하지 않음 |
| 체결(FILLED/PARTIAL) | 실제로 거래소에서 매매가 이루어짐 |
| client_order_id | 운영서버가 부여하는 주문 고유 ID. 중복 주문 방지에 사용 |
| nonce | 요청마다 달라지는 임의값. 재전송 공격 방지에 사용 (client_order_id와는 목적이 다름) |
| 세션(session) | 계좌 + 시드머니로 시작하는 거래 구획. 수익률 계산의 기준점 |
| 모의투자(VIRTUAL) / 실전투자(REAL) | KIS 계좌 환경 구분. 도메인과 인증 방식이 다름 |
