# -*- coding: utf-8 -*-
"""
포트폴리오 구성기 (Portfolio Optimizer)
==========================================

본 모듈은 후보 ETF 목록과 분석 기간을 입력받아, (1) 자산 간 상관관계를 최소화하는
방향의 참고용 비중과 (2) 무위험수익률(10년물 국채금리)을 초과하는 제약 하에서
샤프비율을 최대화하는 비중을 각각 산출하고, 주요 시장 지수(벤치마크) 및
무위험수익률과의 성과를 비교하는 도구다.

[전략적 한계]
본 모듈은 자산 간 상관관계 최소화를 목적함수로 사용하는 분산 최적화 도구이며,
자산군별 변동성 기여도를 균등화하는 정통 리스크 패리티(All-Weather) 전략과는
다르다. '포시즌'이라는 명칭은 상관관계가 낮은 자산을 조합해 다양한 시장 국면에
대응한다는 아이디어를 차용한 것이며, 실제 경기 국면(성장/인플레이션) 기반
자산배분을 구현하지는 않는다.

요구사항: PRD v2 (portfolio_optimizer_prd.md) 참고
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 헤드리스 환경에서도 저장이 가능하도록 비대화형 백엔드 사용
import matplotlib.pyplot as plt
import yfinance as yf
from scipy.optimize import minimize, linprog

warnings.filterwarnings("ignore", category=FutureWarning)

# Windows 콘솔(cp949 등)에서 한글 출력이 깨지는 것을 방지한다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# matplotlib 기본 폰트(DejaVu Sans)는 한글 글리프가 없어 그래프의 한글 라벨이
# 깨지므로, 시스템에 존재하는 한글 폰트를 우선순위로 등록한다.
for _font in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"):
    if _font in {f.name for f in matplotlib.font_manager.fontManager.ttflist}:
        matplotlib.rcParams["font.family"] = _font
        break
matplotlib.rcParams["axes.unicode_minus"] = False  # 한글 폰트 사용 시 마이너스 기호 깨짐 방지

TRADING_DAYS_PER_YEAR = 252

BENCHMARK_TICKERS = {
    "Dow Jones": "^DJI",
    "Nasdaq Composite": "^IXIC",
    "S&P 500": "^GSPC",
    "Russell 2000": "^RUT",
    "Nasdaq 100": "^NDX",
}

# CBOE 10년물 국채 수익률 지수. yfinance가 반환하는 종가가 이미 수익률(%) 값이므로
# 소수 비율로 변환하려면 100으로 나눈다. (예: 4.163 -> 4.163% -> 0.04163)
RISK_FREE_TICKER = "^TNX"
RISK_FREE_SCALE = 100.0


class PortfolioOptimizerApp:
    """상관관계 최소화 기반 포트폴리오 구성 및 벤치마크 비교 애플리케이션."""

    def __init__(self, input_csv, start_date, end_date, max_weight=0.4):
        """
        Parameters
        ----------
        input_csv : str
            후보 ETF 목록 CSV 경로. 컬럼: ticker, name
        start_date, end_date : str
            분석 기간 (YYYY-MM-DD)
        max_weight : float
            개별 자산 최대 비중 제약 (기본값 0.4)
        """
        if start_date >= end_date:
            raise ValueError(f"start_date({start_date})는 end_date({end_date})보다 이전이어야 합니다.")

        if not os.path.exists(input_csv):
            raise FileNotFoundError(f"후보 ETF CSV 파일을 찾을 수 없습니다: {input_csv}")

        self.candidates = pd.read_csv(input_csv)
        if not {"ticker", "name"}.issubset(self.candidates.columns):
            raise ValueError("입력 CSV는 'ticker', 'name' 컬럼을 포함해야 합니다.")

        n_candidates = len(self.candidates)
        if n_candidates < 2:
            raise ValueError("최적화를 위해서는 후보 ETF가 최소 2개 이상 필요합니다.")

        # 개별 비중 상한 제약이 총합 1.0을 만족할 수 없는 경우 사전에 차단한다.
        # (예: 종목 3개, max_weight=0.3 -> 최대 0.9 < 1.0 이므로 실행 불가)
        if n_candidates * max_weight < 1.0:
            raise ValueError(
                f"제약조건이 실행 불가능합니다: 종목수({n_candidates}) x max_weight({max_weight}) "
                f"= {n_candidates * max_weight:.2f} < 1.0. max_weight를 높이거나 종목을 추가하세요."
            )

        self.start_date = start_date
        self.end_date = end_date
        self.max_weight = max_weight

        # fetch_data() 이후 채워지는 값들
        self.tickers = None          # 최종적으로 분석에 사용되는 티커 리스트
        self.prices = None           # 정제된 종가 DataFrame (분석 대상 ETF)
        self.daily_returns = None    # 일별 수익률 DataFrame
        self.corr_matrix = None      # 상관계수 행렬
        self.benchmark_returns = None  # 벤치마크별 누적 수익률 dict
        self.risk_free_rate = None   # 무위험수익률(10년물 국채금리, 연율화 소수)

        # run_optimization() 이후 채워지는 값들
        self.weights = None

        # run_sharpe_optimization() 이후 채워지는 값
        self.weights_sharpe_max = None

        # calculate_performance() 이후 채워지는 값들
        self.performance_table = None
        self.benchmark_table = None

    # ------------------------------------------------------------------
    # 내부 유틸리티
    # ------------------------------------------------------------------
    @staticmethod
    def _download_close_prices(tickers, start_date, end_date):
        """yfinance에서 종가(수정주가)를 내려받아 티커를 컬럼으로 갖는 DataFrame으로 반환한다.

        yfinance는 auto_adjust=True(기본값)일 때 'Adj Close' 컬럼을 별도로 제공하지
        않고 'Close'가 이미 배당/액면분할 조정된 종가이므로 이를 그대로 사용한다.
        """
        raw = yf.download(
            tickers,
            start=start_date,
            end=end_date,
            auto_adjust=True,
            progress=False,
            group_by="column",
        )

        if raw.empty:
            return pd.DataFrame()

        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"]
        else:
            # 티커가 1개일 때는 컬럼이 단일 레벨로 반환된다.
            close = raw[["Close"]]
            close.columns = tickers

        return close

    # ------------------------------------------------------------------
    # 1) 데이터 수집 및 전처리
    # ------------------------------------------------------------------
    def fetch_data(self):
        """ETF 및 벤치마크 데이터를 수집하고 정제한다."""
        candidate_tickers = self.candidates["ticker"].tolist()
        print(f"[fetch_data] ETF {len(candidate_tickers)}종목 데이터 수집 시작: {candidate_tickers}")

        try:
            close_prices = self._download_close_prices(candidate_tickers, self.start_date, self.end_date)
        except Exception as e:
            raise RuntimeError(f"yfinance 데이터 수집 중 오류가 발생했습니다: {e}") from e

        if close_prices.empty:
            raise RuntimeError("데이터를 하나도 수집하지 못했습니다. 티커와 기간을 확인하세요.")

        # 전체 구간이 비어있는(존재하지 않거나 상장폐지된) 티커는 제외한다.
        valid_cols = [c for c in close_prices.columns if close_prices[c].notna().any()]
        dropped = sorted(set(close_prices.columns) - set(valid_cols))
        if dropped:
            print(f"[fetch_data] 경고: 데이터가 없어 제외된 티커: {dropped}")
        close_prices = close_prices[valid_cols]

        # 종목별 상장일이 start_date보다 늦어 데이터가 늦게 시작하는 경우,
        # 전체를 한꺼번에 dropna 하면 분석 가능 기간이 과도하게 줄어들 수 있다.
        # 이를 막기 위해 각 종목의 유효 시작일을 확인하고, 분석 시작일(start_date) 근처에
        # 데이터가 없는 종목은 별도로 제외한 뒤 나머지 종목으로만 공통 구간을 정렬한다.
        requested_start = pd.Timestamp(self.start_date)
        keep_cols = []
        for col in close_prices.columns:
            first_valid = close_prices[col].first_valid_index()
            if first_valid is None:
                continue
            gap_days = (first_valid - requested_start).days
            if gap_days > 30:
                print(
                    f"[fetch_data] 경고: '{col}' 종목은 분석 시작일보다 {gap_days}일 늦게 상장되어 "
                    f"제외합니다. (첫 데이터: {first_valid.date()})"
                )
                continue
            keep_cols.append(col)

        close_prices = close_prices[keep_cols].dropna(how="any")

        if len(keep_cols) < 2 or close_prices.shape[0] < 2:
            raise RuntimeError("유효한 종목 수 또는 공통 거래일 수가 부족하여 분석을 진행할 수 없습니다.")

        self.tickers = keep_cols
        self.prices = close_prices
        self.daily_returns = close_prices.pct_change().dropna(how="any")
        self.corr_matrix = self.daily_returns.corr(method="pearson")

        print(f"[fetch_data] 최종 분석 대상: {self.tickers} ({close_prices.shape[0]}개 거래일)")
        print("[fetch_data] 상관계수 행렬:")
        print(self.corr_matrix.round(3))

        # 벤치마크 지수 수집 및 개별 누적 수익률 계산
        print(f"[fetch_data] 벤치마크 데이터 수집 시작: {list(BENCHMARK_TICKERS.values())}")
        try:
            bm_prices = self._download_close_prices(
                list(BENCHMARK_TICKERS.values()), self.start_date, self.end_date
            )
        except Exception as e:
            raise RuntimeError(f"벤치마크 데이터 수집 중 오류가 발생했습니다: {e}") from e

        self.benchmark_returns = {}
        for name, ticker in BENCHMARK_TICKERS.items():
            if ticker not in bm_prices.columns or bm_prices[ticker].dropna().empty:
                print(f"[fetch_data] 경고: 벤치마크 '{name}'({ticker}) 데이터를 가져오지 못해 제외합니다.")
                continue
            series = bm_prices[ticker].dropna()
            total_return = (series.iloc[-1] - series.iloc[0]) / series.iloc[0]
            self.benchmark_returns[name] = total_return

        # 무위험수익률(10년물 국채금리) 수집: 분석 기간 내 가장 최근 종가를
        # '현재 시점에 가입 가능한' 연율화 무위험수익률로 사용한다.
        print(f"[fetch_data] 무위험수익률 데이터 수집: {RISK_FREE_TICKER}")
        try:
            rf_prices = self._download_close_prices([RISK_FREE_TICKER], self.start_date, self.end_date)
        except Exception as e:
            raise RuntimeError(f"무위험수익률 데이터 수집 중 오류가 발생했습니다: {e}") from e

        if rf_prices.empty or RISK_FREE_TICKER not in rf_prices.columns or rf_prices[RISK_FREE_TICKER].dropna().empty:
            raise RuntimeError(f"무위험수익률({RISK_FREE_TICKER}) 데이터를 가져오지 못했습니다.")

        self.risk_free_rate = rf_prices[RISK_FREE_TICKER].dropna().iloc[-1] / RISK_FREE_SCALE
        print(f"[fetch_data] 무위험수익률(연율화, 최근 10년물 국채금리 기준): {self.risk_free_rate * 100:.2f}%")

        return self

    # ------------------------------------------------------------------
    # 2) 포트폴리오 최적화
    # ------------------------------------------------------------------
    def run_optimization(self):
        """상관관계 절대값 합을 최소화하는 비중을 산출한다."""
        if self.corr_matrix is None:
            raise RuntimeError("run_optimization()보다 먼저 fetch_data()를 호출해야 합니다.")

        n = len(self.tickers)
        C = self.corr_matrix.values
        bounds = [(0.0, self.max_weight)] * n
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        equal_weights = np.full(n, 1.0 / n)

        # 목적함수: 상관관계 절대값 합 최소화
        #   f(w) = sum_{i != j} |w_i * w_j * C_ij|
        # 의도: 자산 간 방향성 동조화 자체를 차단하여 상호 독립성 극대화.
        # abs()는 w_i*w_j*C_ij = 0 지점에서 미분 불가능한 non-smooth 함수라
        # SLSQP(gradient 기반)가 지역 최적해에 머물 수 있다. 이를 보완하기 위해
        # 서로 다른 초기값(균등비중 + 랜덤 2회)으로 재시작(multi-start)하여
        # 그중 목적함수 값이 가장 낮은 해를 채택한다.
        def abs_sum_objective(w):
            outer = np.abs(np.outer(w, w) * C)
            return outer.sum() - np.sum(np.diag(outer))

        rng = np.random.default_rng(42)
        initial_guesses = [equal_weights]
        for _ in range(2):
            raw = rng.dirichlet(np.ones(n))
            initial_guesses.append(raw)

        best_result = None
        for x0 in initial_guesses:
            res = minimize(
                abs_sum_objective, x0, method="SLSQP",
                bounds=bounds, constraints=constraints,
            )
            if res.success and (best_result is None or res.fun < best_result.fun):
                best_result = res

        if best_result is None:
            print("[run_optimization] 경고: 절대값 합 최소화가 모든 초기값에서 수렴하지 않았습니다. 마지막 결과를 사용합니다.")
            best_result = res

        self.weights = np.clip(best_result.x, 0, self.max_weight)
        self.weights /= self.weights.sum()

        print("[run_optimization] 절대값 합 최소화 비중:", dict(zip(self.tickers, self.weights.round(4))))

        return self

    # ------------------------------------------------------------------
    # 2-1) 샤프비율 최대화 (무위험수익률 초과 제약)
    # ------------------------------------------------------------------
    def run_sharpe_optimization(self):
        """무위험수익률(10년물 국채금리)을 초과하는 조건에서 샤프비율을 최대화하는 비중을 산출한다."""
        if self.daily_returns is None or self.risk_free_rate is None:
            raise RuntimeError("run_sharpe_optimization()보다 먼저 fetch_data()를 호출해야 합니다.")

        n = len(self.tickers)
        mu = self.daily_returns.mean().values * TRADING_DAYS_PER_YEAR   # 연환산 기대수익률(과거 평균 기준)
        sigma = self.daily_returns.cov().values * TRADING_DAYS_PER_YEAR  # 연환산 공분산 행렬
        rf = self.risk_free_rate
        bounds = [(0.0, self.max_weight)] * n

        # 1) 실행 가능성(feasibility) 사전 검증
        #    제약조건(합=1, 0<=w<=max_weight) 하에서 달성 가능한 기대수익률의 최댓값을
        #    선형계획법(LP)으로 구해, 그 값이 무위험수익률보다 낮으면 애초에 목표를
        #    달성할 수 있는 비중 조합이 존재하지 않는다는 뜻이므로 즉시 중단한다.
        lp_result = linprog(c=-mu, A_eq=[np.ones(n)], b_eq=[1.0], bounds=bounds, method="highs")
        if not lp_result.success:
            raise RuntimeError(f"기대수익률 상한 계산(LP)에 실패했습니다: {lp_result.message}")

        max_achievable_return = -lp_result.fun
        if max_achievable_return < rf:
            detail = "\n".join(
                f"  - {t}: 연환산 기대수익률 {m * 100:.2f}%"
                for t, m in zip(self.tickers, mu)
            )
            raise ValueError(
                f"현재 후보 ETF와 max_weight={self.max_weight} 제약으로는 무위험수익률"
                f"(연 {rf * 100:.2f}%)을 초과하는 포트폴리오를 구성할 수 없습니다.\n"
                f"(제약 조건 하 최대 달성 가능 기대수익률: {max_achievable_return * 100:.2f}%)\n"
                f"종목별 연환산 기대수익률(과거 평균 기준):\n{detail}\n"
                f"-> max_weight를 높이거나, 기대수익률이 더 높은 ETF를 후보에 추가하세요."
            )

        # 2) 샤프비율 최대화: maximize (w·mu - rf) / sqrt(w^T sigma w)
        #    무위험수익률 초과를 명시적 부등식 제약(w·mu >= rf)으로 강제한다.
        def neg_sharpe(w):
            port_return = w @ mu
            port_vol = np.sqrt(w @ sigma @ w)
            if port_vol == 0:
                return 0.0
            return -(port_return - rf) / port_vol

        constraints = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
            {"type": "ineq", "fun": lambda w: w @ mu - rf},
        ]

        rng = np.random.default_rng(7)
        equal_weights = np.full(n, 1.0 / n)
        initial_guesses = [equal_weights]
        for _ in range(2):
            initial_guesses.append(rng.dirichlet(np.ones(n)))

        best_result = None
        for x0 in initial_guesses:
            res = minimize(
                neg_sharpe, x0, method="SLSQP",
                bounds=bounds, constraints=constraints,
            )
            if res.success and (best_result is None or res.fun < best_result.fun):
                best_result = res

        if best_result is None:
            raise RuntimeError("샤프비율 최적화가 모든 초기값에서 수렴하지 않았습니다.")

        w = np.clip(best_result.x, 0, self.max_weight)
        w /= w.sum()

        # 클리핑 후 재정규화 과정에서 초과수익 제약이 수치오차로 미세하게 깨질 수 있어 재확인한다.
        if w @ mu < rf - 1e-6:
            print("[run_sharpe_optimization] 경고: 재정규화 이후 초과수익 제약이 근소하게 깨졌습니다 (수치 오차 가능성).")

        self.weights_sharpe_max = w

        print("[run_sharpe_optimization] 샤프비율 최대화 비중:", dict(zip(self.tickers, w.round(4))))
        print(
            f"[run_sharpe_optimization] 포트폴리오 연환산 기대수익률: {(w @ mu) * 100:.2f}% "
            f"(무위험수익률 {rf * 100:.2f}% 대비 +{(w @ mu - rf) * 100:.2f}%p)"
        )

        return self

    # ------------------------------------------------------------------
    # 3) 수익률 및 리스크 분석
    # ------------------------------------------------------------------
    def _portfolio_stats(self, weights):
        """주어진 비중에 대한 (누적수익률, 연환산수익률, 연환산변동성, 샤프비율)을 계산한다."""
        # 일별 가중 수익률을 복리로 누적한다. (리밸런싱 없이 최초 비중을 유지한다는
        # buy-and-hold 가정하의 일 단위 근사이며, 단순 가중합보다 실제 복리 효과를 반영한다.)
        weighted_daily = self.daily_returns.values @ weights
        cumulative_return = np.prod(1.0 + weighted_daily) - 1.0

        n_days = len(weighted_daily)
        annualized_vol = weighted_daily.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
        annualized_return = (1.0 + cumulative_return) ** (TRADING_DAYS_PER_YEAR / n_days) - 1.0
        sharpe = annualized_return / annualized_vol if annualized_vol > 0 else np.nan

        return cumulative_return, annualized_return, annualized_vol, sharpe

    def calculate_performance(self):
        """개별/포트폴리오 수익률, 리스크 지표 및 벤치마크·무위험수익률 대비 성과를 계산한다."""
        if self.weights is None:
            raise RuntimeError("calculate_performance()보다 먼저 run_optimization()을 호출해야 합니다.")
        if self.weights_sharpe_max is None:
            raise RuntimeError("calculate_performance()보다 먼저 run_sharpe_optimization()을 호출해야 합니다.")

        individual_return = (self.prices.iloc[-1] - self.prices.iloc[0]) / self.prices.iloc[0]
        individual_vol = self.daily_returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)

        self.performance_table = pd.DataFrame({
            "ticker": self.tickers,
            "name": [self.candidates.set_index("ticker")["name"].get(t, t) for t in self.tickers],
            "individual_return": (individual_return.values * 100).round(2),
            "individual_volatility": (individual_vol.values * 100).round(2),
            "weight_abs_min": self.weights.round(4),
            "weight_sharpe_max": self.weights_sharpe_max.round(4),
        })

        abs_return, abs_return_annual, abs_vol, abs_sharpe = self._portfolio_stats(self.weights)
        sharpe_return, sharpe_return_annual, sharpe_vol, sharpe_sharpe = self._portfolio_stats(self.weights_sharpe_max)

        rows = []
        for name, bm_return in self.benchmark_returns.items():
            rows.append({
                "benchmark_index": name,
                "benchmark_return": round(bm_return * 100, 2),
                "abs_min_return": round(abs_return * 100, 2),
                "diff_abs_vs_bm": round((abs_return - bm_return) * 100, 2),
                "abs_min_sharpe": round(abs_sharpe, 3),
                "sharpe_max_return": round(sharpe_return * 100, 2),
                "diff_sharpe_max_vs_bm": round((sharpe_return - bm_return) * 100, 2),
                "sharpe_max_sharpe": round(sharpe_sharpe, 3),
            })
        self.benchmark_table = pd.DataFrame(rows)

        print("\n[calculate_performance] 포트폴리오 비중 및 개별 성과:")
        print(self.performance_table.to_string(index=False))
        print("\n[calculate_performance] 벤치마크 비교:")
        print(self.benchmark_table.to_string(index=False))
        print(
            f"\n[calculate_performance] 무위험수익률(연 {self.risk_free_rate * 100:.2f}%) 대비 "
            f"샤프비율 최대화 포트폴리오 연환산 수익률 {sharpe_return_annual * 100:.2f}% "
            f"(초과분 +{(sharpe_return_annual - self.risk_free_rate) * 100:.2f}%p)"
        )

        return self

    # ------------------------------------------------------------------
    # 4) 결과 저장
    # ------------------------------------------------------------------
    def export_results(self, weights_csv="portfolio_weights_result.csv",
                        benchmark_csv="benchmark_comparison_result.csv"):
        """분석 결과를 CSV로 저장한다."""
        if self.performance_table is None:
            raise RuntimeError("export_results()보다 먼저 calculate_performance()를 호출해야 합니다.")

        self.performance_table.to_csv(weights_csv, index=False, encoding="utf-8-sig")
        self.benchmark_table.to_csv(benchmark_csv, index=False, encoding="utf-8-sig")

        print(f"\n[export_results] 저장 완료: {weights_csv}, {benchmark_csv}")
        return self

    # ------------------------------------------------------------------
    # 5) 시각화
    # ------------------------------------------------------------------
    def visualize(self, heatmap_path="correlation_heatmap.png", bar_path="portfolio_weights_comparison.png"):
        """상관관계 히트맵과 비중 비교 막대 그래프를 생성해 파일로 저장한다."""
        if self.corr_matrix is None:
            raise RuntimeError("visualize()보다 먼저 fetch_data()를 호출해야 합니다.")

        # --- 상관관계 히트맵 ---
        fig, ax = plt.subplots(figsize=(6, 5))
        im = ax.imshow(self.corr_matrix.values, cmap="RdBu_r", vmin=-1, vmax=1)
        ax.set_xticks(range(len(self.tickers)))
        ax.set_yticks(range(len(self.tickers)))
        ax.set_xticklabels(self.tickers, rotation=45, ha="right")
        ax.set_yticklabels(self.tickers)
        for i in range(len(self.tickers)):
            for j in range(len(self.tickers)):
                ax.text(j, i, f"{self.corr_matrix.values[i, j]:.2f}",
                         ha="center", va="center", color="black", fontsize=8)
        ax.set_title("ETF 상관관계 히트맵")
        fig.colorbar(im, ax=ax, label="Pearson Correlation")
        fig.tight_layout()
        fig.savefig(heatmap_path, dpi=150)
        plt.close(fig)

        # --- 비중 비교 막대 그래프 (절대값 합 최소화 vs 샤프비율 최대화) ---
        fig, ax = plt.subplots(figsize=(7, 5))
        x = np.arange(len(self.tickers))
        width = 0.35
        ax.bar(x - width / 2, self.weights, width, color="tab:orange", label="절대값 합 최소화")
        ax.bar(x + width / 2, self.weights_sharpe_max, width, color="tab:blue", label="샤프비율 최대화")
        ax.set_xticks(x)
        ax.set_xticklabels(self.tickers, rotation=45, ha="right")
        ax.set_ylabel("비중")
        ax.set_title("포트폴리오 비중 비교")
        ax.legend()
        fig.tight_layout()
        fig.savefig(bar_path, dpi=150)
        plt.close(fig)

        print(f"[visualize] 저장 완료: {heatmap_path}, {bar_path}")
        return self


def _ensure_sample_input(csv_path):
    """예시 실행을 위한 샘플 후보 ETF CSV가 없으면 생성한다."""
    if os.path.exists(csv_path):
        return
    sample = pd.DataFrame({
        "ticker": ["SPY", "TLT", "GLD", "DBC", "VNQ"],
        "name": ["미국 대형주(S&P500)", "미국 장기국채", "금", "원자재", "미국 리츠"],
    })
    sample.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[main] 샘플 후보 ETF CSV를 생성했습니다: {csv_path}")


if __name__ == "__main__":
    CANDIDATE_CSV = "candidate_etfs.csv"
    _ensure_sample_input(CANDIDATE_CSV)

    try:
        app = PortfolioOptimizerApp(
            input_csv=CANDIDATE_CSV,
            start_date="2021-01-01",
            end_date="2026-01-01",
            max_weight=0.4,
        )
        (
            app.fetch_data()
               .run_optimization()
               .run_sharpe_optimization()
               .calculate_performance()
               .export_results()
               .visualize()
        )
    except Exception as exc:
        print(f"[오류] 포트폴리오 최적화 실행 중 문제가 발생했습니다: {exc}", file=sys.stderr)
        sys.exit(1)
