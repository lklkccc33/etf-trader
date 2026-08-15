# 포트폴리오 최적화기 개발 학습 노트

> ETF 후보군을 입력받아 상관관계 기반으로 분산 비중을 구하고, 무위험수익률(10년물 국채금리)을 초과하는 샤프비율 최적 포트폴리오를 산출하는 파이썬 도구를 만들며 정리한 개념들.

---

## 1. 프로젝트가 푸는 문제

1. **1단계 - 분산(Diversification)**: 후보 ETF들 중 서로 상관관계가 낮은 조합을 우대하는 비중 산출 (참고용)
2. **2단계 - 수익 목표 달성**: 같은 ETF 풀에서, "무위험수익률(국채금리)보다 반드시 높은 수익"을 제약조건으로 걸고 위험 대비 수익(샤프비율)이 최대가 되는 비중 산출 (최종 결정)

두 단계를 분리한 이유: 상관관계 최소화는 **수익률을 전혀 고려하지 않는** 방식이라 "국채금리 초과"라는 목표를 보장할 수 없었음. 목표에 수익률 정보가 필요하면 반드시 기대수익률을 다루는 평균-분산(mean-variance) 계열 최적화가 있어야 한다는 게 핵심 교훈.

---

## 2. 핵심 금융 개념

### 2.1 피어슨 상관계수 행렬 (Correlation Matrix)

두 자산의 일별 수익률이 같은 방향으로 움직이는 정도를 -1~1로 나타낸 지표. 포트폴리오 분산투자의 핵심은 "수익률이 아니라 상관관계가 낮은 자산을 섞는 것"이라는 아이디어에서 출발.

```python
daily_returns = prices.pct_change().dropna()
corr_matrix = daily_returns.corr(method="pearson")
```

### 2.2 상관관계 최소화 목적함수 두 가지

- **단순 합 (Raw Sum)**: $f(w) = \sum_{i \neq j} w_i w_j C_{ij}$ → 역상관(음의 상관관계) 자산에 큰 보상을 줘서 헤지 효과를 극대화하려는 방식. (최종적으로 이 프로젝트에서는 제거됨)
- **절대값 합 (Absolute Sum)**: $f(w) = \sum_{i \neq j} |w_i w_j C_{ij}|$ → 상관관계의 방향(양/음)과 무관하게 "동조화 자체"를 억제. 최종 채택된 방식.

두 방식의 차이는 "역상관을 적극적으로 우대할지" vs "그냥 서로 무관하게만 만들지"의 철학 차이.

### 2.3 무위험수익률 (Risk-Free Rate)과 초과수익

투자에서 "리스크를 지지 않고 얻을 수 있는 최소 기준선". 미국 시장에서는 보통 **10년물 국채금리**를 프록시로 사용. 어떤 포트폴리오든 이 기준선을 넘지 못하면 굳이 리스크를 감수할 이유가 없다는 논리.

```
^TNX (CBOE 10년물 국채 수익률 지수)
```

> **실수 기록**: `^TNX`의 yfinance 종가가 "실제 수익률의 10배"로 고시된다는 옛 정보를 그대로 믿고 `/1000`으로 나눴다가, 실제로는 종가가 이미 수익률(%) 그 자체(예: 4.163 = 4.163%)라는 걸 뒤늦게 발견. `/100`으로 수정. → **외부 데이터의 단위/스케일은 항상 실제 값을 찍어서 검증해야 한다**는 교훈.

### 2.4 샤프비율(Sharpe Ratio)과 탄젠시 포트폴리오(Tangency Portfolio)

$$Sharpe = \frac{R_{portfolio} - R_f}{\sigma_{portfolio}}$$

"위험(변동성) 1단위당 얼마의 초과수익을 얻는가"를 나타내는 지표. 이걸 최대화하는 비중을 찾는 문제가 마코위츠(Markowitz) 평균-분산 최적화의 핵심 응용 중 하나이며, 그래프 상에서 무위험수익률 지점에서 효율적 투자선(efficient frontier)에 접하는 지점이라 "탄젠시 포트폴리오"라 부름.

### 2.5 평균-분산 최적화의 근본적 약점: 추정 오차(Estimation Error)

샤프비율 최적화는 상관관계 최소화와 달리 **기대수익률(μ)**이라는 입력이 추가로 필요한데, 이걸 과거 평균 수익률로 추정하면 노이즈가 매우 커서:
- 결과 비중이 소수 종목에 쏠리기 쉽고
- 분석 기간을 조금만 바꿔도 비중이 크게 흔들림

이 프로젝트에서는 리스크 패리티(Risk Parity)나 최소분산(Min-Variance) 같은 "기대수익률 없이" 공분산만 쓰는 대안도 검토했지만, "무위험수익률 초과"라는 목표 자체가 수익률 정보를 요구하기 때문에 결국 평균-분산 계열을 선택함. → **목표에 수익률이 걸려 있으면 추정오차 리스크를 감수할 수밖에 없다**는 트레이드오프.

---

## 3. 최적화 기법 (scipy.optimize)

### 3.1 SLSQP (Sequential Least Squares Programming)

등식/부등식 제약과 변수별 범위(bounds)를 동시에 다룰 수 있는 제약조건부 비선형 최적화 알고리즘. 이 프로젝트의 모든 최적화(상관관계 최소화, 샤프비율 최대화)에 공통으로 사용.

```python
result = minimize(
    objective_fn, x0, method="SLSQP",
    bounds=[(0.0, max_weight)] * n,
    constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}],
)
```

- `bounds`: 개별 비중의 상하한 ($0 \le w_i \le max\_weight$)
- `constraints`: `type="eq"`는 등식(=0), `type="ineq"`는 부등식(≥0)으로 해석됨

샤프비율 최대화에서는 여기에 부등식 제약 하나를 더 추가:

```python
{"type": "ineq", "fun": lambda w: w @ mu - rf}   # w·mu - Rf >= 0
```

### 3.2 Non-smooth 목적함수와 Multi-start

`abs()`가 들어간 목적함수(절대값 합 최소화)는 0을 지나는 지점에서 미분이 불가능한 "꺾이는 점(kink)"이 생김. SLSQP는 gradient(기울기) 기반 알고리즘이라 이런 지점 근처에서 지역 최적해(local optimum)에 갇히기 쉬움.

**대응책**: 초기값을 여러 개(균등비중 + 랜덤 시드 2개) 주고 각각 최적화를 돌린 뒤, 그중 목적함수 값이 가장 낮은 결과를 채택하는 **multi-start** 기법 사용.

```python
initial_guesses = [equal_weights]
for _ in range(2):
    initial_guesses.append(rng.dirichlet(np.ones(n)))  # 합이 1인 랜덤 비중 생성

best_result = None
for x0 in initial_guesses:
    res = minimize(...)
    if res.success and (best_result is None or res.fun < best_result.fun):
        best_result = res
```

> `np.random.default_rng(seed).dirichlet(np.ones(n))`은 "합이 1이고 각 성분이 0~1인 랜덤 벡터"를 만드는 표준적인 방법. 포트폴리오 비중의 랜덤 초기값을 만들 때 자주 씀.

### 3.3 사전 실행 가능성(Feasibility) 검증 — linprog

샤프비율 최적화에 `w·μ ≥ Rf` 제약을 걸면,애초에 이 조건을 만족하는 비중 조합이 존재하지 않을 수 있음(모든 후보 종목의 기대수익률이 Rf보다 낮은 경우 등). 이걸 SLSQP가 실패할 때까지 기다리지 않고 **미리** 확인하는 방법:

```python
# "제약 하에서 w·mu를 최대화하면 얼마까지 가능한가"를 선형계획법(LP)으로 계산
lp_result = linprog(c=-mu, A_eq=[np.ones(n)], b_eq=[1.0], bounds=bounds, method="highs")
max_achievable_return = -lp_result.fun

if max_achievable_return < rf:
    raise ValueError("목표 달성 불가능 — 종목별 기대수익률: ...")
```

- `linprog`는 **선형** 목적함수 + 선형 제약만 다룰 수 있는(비선형 불가) 최적화 함수. `w·μ`는 선형이라 여기 쓰기 딱 맞음.
- `c=-mu`인 이유: `linprog`는 기본적으로 **최소화**만 지원해서, 최대화를 하려면 목적함수에 마이너스를 붙여 "음수의 최소화 = 원래 값의 최대화"로 바꿔줌.
- 이 방식은 사실상 "합이 1이고 상한이 있는 조건에서 기대수익률이 가장 높은 자산부터 최대한 채우는" 그리디(greedy) 배분과 수학적으로 동일한 해를 줌 (분수 배낭 문제/Fractional Knapsack과 같은 구조).

### 3.4 등식 제약을 다루는 다른 방법: 정규화(normalize)

최적화 결과가 수치오차로 합이 정확히 1이 아닐 수 있어 사후에 나눠서 보정:

```python
w = np.clip(best_result.x, 0, max_weight)
w /= w.sum()
```

단, 이 재정규화 과정에서 다른 제약(여기선 `w·μ ≥ Rf`)이 미세하게 깨질 수 있다는 점도 감안해서, 재정규화 후 다시 한 번 체크하는 방어 코드를 추가함.

---

## 4. 수익률 계산에서 배운 것

### 4.1 단순 가중합 vs 일별 복리

포트폴리오 수익률을 "개별 종목 수익률의 가중평균"으로 계산하면($\sum w_i R_i$) **복리 효과가 빠진 근사치**밖에 안 됨. 정확히 하려면:

```python
weighted_daily = daily_returns.values @ weights           # 일별 가중 수익률
cumulative_return = np.prod(1.0 + weighted_daily) - 1.0    # 복리 누적
annualized_return = (1 + cumulative_return) ** (252 / n_days) - 1  # 연환산(CAGR)
```

`252`는 미국 주식시장의 연간 평균 거래일 수 — 일별 변동성/수익률을 연 단위로 환산할 때 표준적으로 쓰는 상수.

### 4.2 연환산 변동성

$$\sigma_{annual} = \sigma_{daily} \times \sqrt{252}$$

변동성은 시간에 따라 "제곱근 법칙(square-root-of-time rule)"으로 스케일링됨 — 수익률처럼 그냥 곱하기가 아니라 √를 씌우는 이유는 분산(variance)이 시간에 비례해서 커지고, 표준편차는 분산의 제곱근이기 때문.

---

## 5. 데이터 수집(yfinance) 실무 이슈

### 5.1 `Adj Close`가 사라진 이유

최신 yfinance는 기본값이 `auto_adjust=True`라서 배당/액면분할 조정이 이미 반영된 값이 `'Close'` 컬럼에 들어있고, 별도의 `'Adj Close'` 컬럼은 아예 없음. 라이브러리 기본 동작이 버전에 따라 바뀔 수 있으니 **가정하지 말고 실제로 실행해서 컬럼을 확인**해야 함.

### 5.2 종목별 상장일 불일치

여러 종목을 한 번에 받아서 `dropna()`를 적용하면, 그중 하나라도 늦게 상장된 종목이 있으면 전체 분석 기간이 그 종목 상장일 이후로 확 줄어들 수 있음. 해결책: 각 종목의 `first_valid_index()`를 확인해서 분석 시작일과 크게 어긋나는 종목은 먼저 제외한 뒤, 나머지로만 공통 구간을 맞춤.

### 5.3 티커 컬럼 구조 차이

`yf.download()`에 티커를 여러 개 주면 `MultiIndex` 컬럼(예: `('Close', 'SPY')`)이 반환되지만, 티커를 1개만 주면 단일 레벨 컬럼이 반환됨. 이 차이를 처리하지 않으면 종목 수에 따라 코드가 깨짐.

```python
if isinstance(raw.columns, pd.MultiIndex):
    close = raw["Close"]
else:
    close = raw[["Close"]]
    close.columns = tickers
```

---

## 6. 실행 환경(Windows) 관련 이슈

### 6.1 콘솔 한글 깨짐

Windows 콘솔의 기본 코드페이지(cp949 등)와 Python의 출력 인코딩이 안 맞으면 한글 출력이 깨짐. `sys.stdout.reconfigure(encoding="utf-8")`로 명시적으로 UTF-8을 강제해서 해결.

### 6.2 matplotlib 한글 폰트 깨짐

matplotlib 기본 폰트(DejaVu Sans)는 한글 글리프가 없어서, 한글이 들어간 제목/라벨이 네모(□)로 깨짐. 시스템에 설치된 한글 폰트(Windows는 보통 `Malgun Gothic`)를 우선순위로 등록해야 함.

```python
for font in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"):
    if font in {f.name for f in matplotlib.font_manager.fontManager.ttflist}:
        matplotlib.rcParams["font.family"] = font
        break
matplotlib.rcParams["axes.unicode_minus"] = False  # 한글 폰트 사용 시 마이너스 기호 깨짐 방지
```

---

## 7. 설계 결정 과정에서 배운 것 (요구사항 명세의 중요성)

PRD를 v1 → v4로 발전시키며 겪은 시행착오:

| 버전 | 변경 내용 | 배운 점 |
|---|---|---|
| v1 → v2 | `Adj Close` 버그, `max_weight` 실행 불가능성 검증, 종목별 상장일 처리, 수익률 복리 계산 정정, 벤치마크 티커 일관성(`QQQ`→`^NDX`) | 처음 설계할 땐 안 보이던 엣지 케이스가 실제 라이브러리 동작을 확인하면서 드러남 |
| v2 → v3 | 목적함수를 절대값 합 최소화 하나로 단순화 | 요구사항은 계속 좁혀질 수 있고, 코드는 그에 맞춰 단순해질 수 있어야 함 |
| v3 → v4 | 샤프비율 최대화 + 무위험수익률 초과 제약 추가 | "가장 중요한 포인트가 뭔지"를 먼저 정하고 나면 여러 후보 방법론(리스크패리티/최소분산/HRP/Kelly 등) 중 무엇을 배제해야 할지 명확해짐 |

**핵심 교훈**: 최적화 문제를 설계할 때는 "무엇을 최적화할지"보다 먼저 "반드시 지켜야 하는 제약이 무엇인지"부터 정해야, 목적함수 선택(리스크패리티 vs 평균-분산 등)이 자연스럽게 좁혀진다.

---

## 8. 실수 모음 (디버깅 회고)

1. `^TNX` 스케일 오해 → 실제 raw 값을 찍어보고 나서야 발견 (`/1000`이 아니라 `/100`이 맞음)
2. matplotlib 한글 폰트 미설정으로 그래프 라벨이 깨짐 → 실제로 이미지를 열어보고 나서야 발견
3. Windows 콘솔 인코딩 문제로 로그가 깨짐 → 실행 로그를 직접 확인하고 나서야 발견

공통점: **"돌아간다"와 "제대로 동작한다"는 다르다.** 코드가 에러 없이 끝까지 실행되는 것과, 출력값이 실제로 맞는 것은 별개의 문제라서, 중간 산출물(로그, 그래프, 숫자)을 직접 눈으로 확인하는 검증 단계가 꼭 필요했다.
