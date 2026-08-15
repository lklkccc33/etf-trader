# 📋 [System Prompt / PRD] 포트폴리오 구성기 (Portfolio Optimizer) 개발 요구사항 문서 (v4 - 수정본)

> **v2 변경 사항 요약**: `Adj Close` → `auto_adjust` 처리 방식 수정, `max_weight` 실행 가능성 검증 추가, 종목별 데이터 기간 불일치 처리 규칙 추가, 포트폴리오 수익률을 일별 복리 방식으로 정정, 벤치마크 티커 일관성 수정(`QQQ`→`^NDX`), 리스크(변동성/샤프비율) 지표 추가, 에러 핸들링 요구사항 추가.
>
> **v3 변경 사항 요약**: 목적 함수를 **상관관계 절대값 합 최소화 단일 방식**으로 통일. 기존의 목적 함수 1(단순 합 최소화, Raw Sum Minimization)은 제거하고, 절대값 합 최소화 결과만 산출·비교·시각화하도록 수정.
>
> **v4 변경 사항 요약**: 무위험수익률(미국 10년물 국채금리, `^TNX`)을 초과하는 것을 최우선 목표로 하는 **샤프비율 최대화(Maximum Sharpe Ratio) 최적화 단계를 추가**. 기존 상관관계 절대값 최소화 비중은 분산 효과 참고용으로 유지하고, 최종 비중 결정에는 샤프비율 최대화 결과(무위험수익률 초과를 명시적 제약으로 강제)를 사용한다.

## 1. 프로젝트 개요 (Overview)

* 목적: 후보 ETF 리스트와 분석 기간을 입력받아 포시즌(Four Seasons/All-Weather) 전략의 아이디어를 참고하여, 자산 간 상관관계를 최소화하는 방향으로 포트폴리오 비중을 산출하고, 주요 시장 지수(벤치마크)와 수익률·리스크 성과를 비교 분석하는 파이썬 모듈을 구축한다.
* **주의**: 본 모듈은 상관관계 최소화에 집중하며, 완전한 리스크 패리티(risk parity) 기반 All-Weather 전략과는 다르다는 점을 코드 상단 독스트링에 명시한다. (섹션 3.3.1 참고)
* 개발 언어 및 환경: Python 3.9+, `yfinance>=0.2.51`, `pandas`, `numpy`, `scipy`, `matplotlib`

## 2. 입출력 규격 (Input & Output Specifications)

### 2.1 Input Specification

1. 후보 ETF List (CSV 파일):
   * 파일명: `candidate_etfs.csv`
   * 컬럼 구성: `ticker` (ETF 티커), `name` (ETF 이름)
2. 분석 조건 파라미터:
   * `start_date` (예: "2021-01-01")
   * `end_date` (예: "2026-01-01")
   * `max_weight` (개별 자산 최대 비중 제약, 기본값: `0.4`)
3. **입력 검증 (신규)**:
   * `len(tickers) * max_weight >= 1.0` 이 아니면 총합 1.0 제약을 만족할 수 없으므로, 초기화 단계에서 `ValueError`로 명확히 알린다. (예: 종목 3개, max_weight=0.3 → 최대 0.9 < 1.0 → 실행 불가)
   * `start_date < end_date` 검증

### 2.2 Output Specification

1. Console Output: 계산 과정 Log, 상관관계 행렬, 무위험수익률, 최종 비중 및 수익률·리스크 비교 표 출력
2. CSV Output 1 (`portfolio_weights_result.csv`):
   * `ticker`, `name`, `individual_return` (개별 누적 수익률 %), `individual_volatility` (개별 연환산 변동성 %), `weight_abs_min` (상관관계 절대값 합 최소화 비중, 참고용), `weight_sharpe_max` (샤프비율 최대화 비중, 신규 — 최종 결정 비중)
3. CSV Output 2 (`benchmark_comparison_result.csv`):
   * 주요 지수(`Dow Jones`, `Nasdaq Composite`, `S&P 500`, `Russell 2000`, `Nasdaq 100`)와 비교
   * 컬럼: `benchmark_index`, `benchmark_return`, `abs_min_return`, `diff_abs_vs_bm`, `abs_min_sharpe`, `sharpe_max_return`(신규), `diff_sharpe_max_vs_bm`(신규), `sharpe_max_sharpe`(신규)
4. Visual Output:
   * ETF 상관관계 히트맵 (Heatmap, matplotlib 사용 — seaborn 미사용으로 의존성 최소화)
   * 포트폴리오 비중 비교 막대 그래프 (절대값 합 최소화 vs 샤프비율 최대화, Bar Plot)

## 3. 핵심 알고리즘 및 계산 로직 (Core Logic)

### 3.1 데이터 수집 및 전처리 (수정)

* `yfinance`를 사용해 입력된 ETF Ticker와 벤치마크 지수의 종가 데이터를 수집한다.
  * **`yf.download(..., auto_adjust=True)` (yfinance 기본값)를 사용하고, `'Close'` 컬럼을 수정주가 기준 종가로 사용한다.** (`auto_adjust=True`일 때 `'Adj Close'` 컬럼은 존재하지 않으므로 이를 참조하지 않는다.)
  * 벤치마크 Ticker: `^DJI` (Dow Jones), `^IXIC` (Nasdaq Composite), `^GSPC` (S&P 500), `^RUT` (Russell 2000), **`^NDX`** (Nasdaq 100 — 기존 `QQQ`는 추적오차가 있는 ETF이므로 다른 벤치마크들과의 일관성을 위해 실제 지수로 교체)
* **종목별 데이터 기간 불일치 처리 (신규)**: 특정 종목이 `start_date` 이후에 상장되었거나 데이터가 부분적으로 결측된 경우:
  1. 각 종목별로 유효 데이터 시작일을 확인하고, `start_date` 시점에 데이터가 없는 종목은 콘솔에 경고를 출력한 뒤 분석 대상에서 제외한다.
  2. 나머지 종목에 대해서만 공통 거래일 기준으로 정렬 후 `dropna()`를 적용한다. (전체 종목 기준 일괄 `dropna()`로 인해 분석 기간이 과도하게 축소되는 것을 방지)
* 결측치 제거 후 일간 백분율 수익률(Daily Percent Returns) 및 자산 간 피어슨 상관계수 행렬($C$)을 계산한다.
* **에러 핸들링 (신규)**: 티커가 유효하지 않거나 `yfinance` 응답이 비어있는 경우, 해당 종목을 건너뛰고 경고 로그를 남긴다. 유효 종목이 2개 미만으로 남으면 `RuntimeError`를 발생시킨다.
* **무위험수익률 수집 (v4 신규)**: `^TNX`(CBOE 10년물 국채 수익률 지수)의 분석 기간 내 최근 종가를 연율화 무위험수익률로 사용한다. yfinance가 반환하는 종가는 이미 수익률(%) 값이므로 100으로 나눠 소수로 변환한다. (예: 4.163 → 0.04163)

### 3.2 포트폴리오 최적화 (Optimization Engines)

`scipy.optimize.minimize` (알고리즘: `SLSQP`)를 사용해 아래 두 단계의 비중을 각각 산출한다.

* 공통 제약조건 (Constraints & Bounds):
  1. 비중의 총합은 1 ($\sum w_i = 1.0$)
  2. 개별 자산 비중 범위 제한 ($0 \le w_i \le max\_weight$)
  3. 초기 추정값: $1/N$ 동일 비중

**1) 상관관계 절대값 합 최소화 (참고용, 기존 유지)**
* $f(w) = \sum_{i \neq j} \vert w_i \cdot w_j \cdot C_{i,j} \vert$
* 의도: 자산 간 방향성 동조화 자체를 차단하여 상호 독립성 극대화.
* **구현 유의사항**: `abs()`는 $w_i w_j C_{i,j} = 0$ 지점에서 미분 불가능한 non-smooth 함수이며, SLSQP는 gradient 기반(수치미분)이므로 kink 근처에서 수렴이 불안정하거나 지역 최적해(local optimum)에 머물 수 있다. 이를 보완하기 위해 **서로 다른 3개 이상의 초기값(동일비중, 랜덤 시드 2개)으로 재시작(multi-start)하여 가장 낮은 목적함수 값을 갖는 해를 채택**한다.

**2) 샤프비율 최대화 — 무위험수익률 초과 제약 (v4 신규, 최종 비중 결정 방식)**
* 연환산 기대수익률(과거 평균 일별수익률 × 252) 벡터 $\mu$, 연환산 공분산 행렬(일별수익률 공분산 × 252) $\Sigma$, 무위험수익률 $R_f$를 사용한다.
* 목적 함수: $\max_w \dfrac{w \cdot \mu - R_f}{\sqrt{w^T \Sigma w}}$ (구현상 `-Sharpe`를 최소화)
* **추가 제약**: $w \cdot \mu \ge R_f$ (무위험수익률 초과를 목적함수뿐 아니라 명시적 부등식 제약으로도 강제 — "가장 중요한 포인트는 무위험수익률보다 수익이 높아야 한다"는 요구사항을 최적화 단계에서부터 보장)
* **사전 실행 가능성(feasibility) 검증**: `scipy.optimize.linprog`로 제약조건(합=1, bounds) 하에서 달성 가능한 $w \cdot \mu$의 최댓값을 계산한다. 이 값이 $R_f$보다 낮으면 어떤 비중 조합으로도 목표를 달성할 수 없다는 뜻이므로, 종목별 기대수익률을 보여주는 `ValueError`를 즉시 발생시키고 중단한다. (섹션 5의 "1번 방식: 에러로 명확히 알림" 결정 반영)
* 이 목적함수 역시 non-convex할 수 있어 위와 동일하게 multi-start를 적용한다.

### 3.3 수익률 및 리스크 분석 (Performance Calculation) — 수정

* 개별 누적 수익률:
  * $R_{total} = \dfrac{P_{end} - P_{start}}{P_{start}}$
* **포트폴리오 누적 수익률 (계산 방식 수정)**:
  * 기존의 단순 가중합($\sum w_i R_{total,i}$)은 일별 복리 효과를 반영하지 못하는 근사치이므로, 아래와 같이 **일별 수익률 기준 복리 계산**으로 정정한다.
  * $r_{portfolio,t} = \sum_i w_i \cdot r_{i,t}$ (일별 가중 수익률)
  * $R_{portfolio} = \prod_t (1 + r_{portfolio,t}) - 1$ (전체 기간 복리 누적 수익률)
  * 이 방식은 리밸런싱 없이 최초 비중을 유지한다는 가정(buy-and-hold, 일 단위 재조정 근사)을 사용함을 코드 주석에 명시한다.
* **리스크 지표**:
  * 연환산 변동성: $\sigma_{annual} = \sigma_{daily} \times \sqrt{252}$
  * 샤프비율: $Sharpe = \dfrac{R_{portfolio,annualized} - R_f}{\sigma_{annual}}$ (v4: 무위험수익률 0% 가정을 실제 $R_f$로 대체)
* 초과 수익률 차이(%p):
  * $Diff = R_{portfolio} - R_{benchmark}$ (벤치마크 대비)
  * $Diff_{Rf} = R_{portfolio,annualized} - R_f$ (무위험수익률 대비, v4 신규 — 콘솔에 별도 출력)

### 3.3.1 전략적 한계 명시 (신규)

코드 최상단 모듈 독스트링에 다음 내용을 한글로 명시한다:
> "본 모듈은 자산 간 상관관계 최소화를 목적함수로 사용하는 분산 최적화 도구이며, 자산군별 변동성 기여도를 균등화하는 정통 리스크 패리티(All-Weather) 전략과는 다르다. '포시즌'이라는 명칭은 상관관계가 낮은 자산을 조합해 다양한 시장 국면에 대응한다는 아이디어를 차용한 것이며, 실제 경기 국면(성장/인플레이션) 기반 자산배분을 구현하지는 않는다."

## 4. 코드 구현 구조 요구사항 (Architecture Requirements)

객체지향 클래스 구조(`PortfolioOptimizerApp`)로 작성한다.

* `__init__(input_csv, start_date, end_date, max_weight=0.4)`: 초기화 및 입력 검증(2.1의 신규 검증 포함)
* `fetch_data()`: yfinance 데이터 수집, `auto_adjust=True` 기준 전처리, 종목별 기간 불일치 처리, 무위험수익률(`^TNX`) 수집, 에러 핸들링 포함
* `run_optimization()`: 상관관계 절대값 합 최소화 수행 (multi-start 적용, 참고용 비중)
* `run_sharpe_optimization()` (v4 신규): 무위험수익률 초과 제약 하 샤프비율 최대화 수행 (feasibility 사전 검증 + multi-start 적용, 최종 비중)
* `calculate_performance()`: 일별 복리 기준 누적 수익률, 변동성, 샤프비율 계산 및 벤치마크·무위험수익률 대비 차이 분석
* `export_results()`: 결과를 콘솔 출력 및 CSV 파일 저장
* `visualize()`: matplotlib으로 히트맵 및 두 비중(참고용/최종) 비교 차트 생성

## 5. 최종 출력 코드 요청 사항

* 에러 없이 바로 실행 가능한 완벽한 전체 파이썬 코드를 작성한다.
* 코드 내부 주요 로직에 독스트링(Docstring)과 주석을 한글로 상세하게 단다.
* 데이터 수집 실패, 입력값 오류 등 예외 상황에 대한 최소한의 에러 핸들링(`try/except`, 명확한 에러 메시지)을 포함한다.
* 메인 실행 블록(`if __name__ == '__main__':`)에 예시 실행 코드를 포함한다.
