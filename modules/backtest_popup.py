import csv
import logging
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from tkinter import ttk, messagebox, filedialog

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
import yfinance as yf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from fundamental_score import safe_get_float, calculate_valuation_score, calculate_factor_score, calculate_piotroski_fscore

try:
    from tkcalendar import DateEntry as _DateEntry
    _has_calendar = True
except ImportError:
    _has_calendar = False

import config
from help_texts import STRATEGY_HELP, BACKTEST_INPUT_HELP, CHART_HELP, RESULT_HELP
from ui_components import Tooltip, HelpTooltip

plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.family'] = 'Malgun Gothic'

strategy_options = ["ma_cross", "macd", "rsi", "macd_rsi", "bollinger", "momentum_signal", "momentum_return_ma", "ichimoku"]

# 전략 한글 표시명 매핑
STRATEGY_DISPLAY_NAMES = {
    "ma_cross": "이동평균 교차",
    "macd": "MACD 교차",
    "rsi": "RSI 과매수/과매도",
    "macd_rsi": "MACD+RSI 복합",
    "bollinger": "볼린저 밴드",
    "momentum_signal": "종합 모멘텀",
    "momentum_return_ma": "수익률+MA 교차",
    "ichimoku": "일목균형표",
}
# 표시명→내부값 역매핑
STRATEGY_DISPLAY_TO_KEY = {v: k for k, v in STRATEGY_DISPLAY_NAMES.items()}
strategy_display_options = [STRATEGY_DISPLAY_NAMES[k] for k in strategy_options]

# 단위 한글 표시명 매핑
UNIT_DISPLAY_NAMES = {"d": "일", "mo": "개월", "y": "년"}
UNIT_DISPLAY_TO_KEY = {v: k for k, v in UNIT_DISPLAY_NAMES.items()}
unit_display_options = ["일", "개월", "년"]

# Strategy descriptions for tooltip/display (Phase 12-3)
STRATEGY_DESCRIPTIONS = {
    "ma_cross": "이동평균 교차 (단기/장기 MA 크로스)",
    "macd": "MACD 교차 (MACD/Signal 크로스)",
    "rsi": "RSI 과매수/과매도 기반",
    "macd_rsi": "MACD + RSI 복합 전략",
    "bollinger": "볼린저 밴드 터치/반등 전략",
    "momentum_signal": "종합 모멘텀 신호 전략",
    "momentum_return_ma": "모멘텀 수익률 + MA 교차",
}

# Phase 7-2: API retry
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1


def _retry_download(ticker_symbol, start, end):
    """yf.download with retry logic. Uses SQLite cache when available."""
    # Try cache first
    try:
        from data_cache import get_cached_history
        data = get_cached_history(ticker_symbol, start=start, end=end, interval="1d")
        if not data.empty:
            return data
    except ImportError:
        pass
    except Exception as e:
        logging.warning(f"[BACKTEST] Cache fallback: {e}")

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            data = yf.download(ticker_symbol, start=start, end=end)
            return data
        except (ConnectionError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logging.warning(f"[BACKTEST] Download retry {attempt + 1} for {ticker_symbol}: {e}")
                time.sleep(delay)
    raise last_error


def calculate_rsi_for_backtest(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _get_commission_rate():
    """config에서 수수료율 조회."""
    return config.config.get("backtest", {}).get("commission_rate", 0.001)


def _get_slippage_pct():
    """config에서 슬리피지율 조회."""
    return config.config.get("backtest", {}).get("slippage_pct", 0.0005)


# Backward compatibility
COMMISSION_RATE = 0.001


def _safe_division(exit_price, entry_price):
    """Phase 3-6: Safe profit calculation avoiding division by zero.
    Applies round-trip commission (buy + sell) and slippage."""
    if entry_price > 0:
        commission = _get_commission_rate()
        slippage = _get_slippage_pct()
        raw_return = (exit_price - entry_price) / entry_price
        # Deduct round-trip commission (buy + sell) and slippage (buy + sell)
        return raw_return - (commission * 2) - (slippage * 2)
    return 0.0


def open_backtest_popup(stock, on_search_callback=None, app_state=None):
    ticker_symbol = stock.split('(')[-1].split(')')[0]
    # 종목명 추출 (예: "NVIDIA Corporation (NVDA)" → "NVIDIA Corporation")
    stock_display = stock.strip()
    if '(' in stock:
        company_name_part = stock.split('(')[0].strip()
        stock_display = f"{company_name_part} ({ticker_symbol})" if company_name_part else ticker_symbol
    else:
        stock_display = ticker_symbol

    # Track all open figures for cleanup (Phase 4-1)
    open_figures = []

    def cleanup_figures():
        """Close all matplotlib figures when popup is destroyed."""
        for fig in open_figures:
            try:
                plt.close(fig)
            except Exception:
                pass
        open_figures.clear()

    def _get_unit_key():
        """현재 단위 콤보박스의 표시명을 내부 키로 변환."""
        return UNIT_DISPLAY_TO_KEY.get(period_unit_var.get(), period_unit_var.get())

    def _get_method_key():
        """현재 전략 콤보박스의 표시명을 내부 키로 변환."""
        return STRATEGY_DISPLAY_TO_KEY.get(method_var.get(), method_var.get())

    def update_dates(save=False):
        try:
            value_text = period_value_entry.get()
            if not value_text.isdigit():
                return
            value = int(value_text)
            # Phase 5-3: Range validation
            if value < 1 or value > 9999:
                return

            unit = _get_unit_key()
            now = datetime.now()
            if unit == 'd':
                start = now - timedelta(days=value)
            elif unit == 'mo':
                start = now - timedelta(days=value * 30)
            elif unit == 'y':
                start = now - timedelta(days=value * 365)
            else:
                start = now
            unit_names = {'d': '일', 'mo': '개월', 'y': '년'}
            unit_label = f"{value}{unit_names.get(unit, unit)}"
            period_range_label.config(
                text=f"분석 기간: {start.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')} ({unit_label})"
            )

            if save:
                config.config["backtest"]["period"] = value
                config.config["backtest"]["unit"] = unit
                config.save_config(config.get_config())

        except ValueError as e:
            # Phase 3-8: Show error instead of silent pass
            logging.error(f"[BACKTEST] update_dates error: {e}")
            messagebox.showerror("오류", f"날짜 계산 오류: {e}")

    def save_and_search():
        mode = bt_period_mode_var.get()
        config.config["backtest"]["period_mode"] = mode

        if mode == "absolute":
            # 절대 날짜 모드
            start_str = bt_start_date_entry.get().strip()
            end_str = bt_end_date_entry.get().strip()
            try:
                datetime.strptime(start_str, '%Y-%m-%d')
                datetime.strptime(end_str, '%Y-%m-%d')
            except ValueError:
                messagebox.showerror("오류", "날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)")
                return
            if start_str >= end_str:
                messagebox.showerror("오류", "시작 날짜가 종료 날짜보다 이전이어야 합니다.")
                return
            config.config["backtest"]["start_date"] = start_str
            config.config["backtest"]["end_date"] = end_str
            value = None
            unit = None
        else:
            # 상대 기간 모드
            value_text = period_value_entry.get().strip()
            if not value_text.isdigit():
                messagebox.showerror("오류", "기간 숫자는 정수로 입력하세요.")
                return

            value = int(value_text)
            if value < 1 or value > 9999:
                messagebox.showerror("오류", "1~9999 범위의 숫자를 입력하세요.")
                return

            unit = _get_unit_key()
            if unit not in ('d', 'mo', 'y'):
                messagebox.showerror("오류", "기간 단위를 일, 개월, 년 중 하나로 선택하세요.")
                return
            config.config["backtest"]["period"] = value
            config.config["backtest"]["unit"] = unit

        method = _get_method_key()
        config.config["backtest"]["method"] = method
        config.config["backtest"]["stoploss_enabled"] = stoploss_enabled_var.get()
        try:
            sl_pct = float(stoploss_pct_var.get())
        except ValueError:
            sl_pct = 5.0
        config.config["backtest"]["stoploss_pct"] = sl_pct
        config.config["backtest"]["regime_filter"] = regime_filter_var.get()

        # 추적 손절 설정 저장
        config.config["backtest"]["trailing_enabled"] = trailing_enabled_var.get()
        config.config["backtest"]["trailing_type"] = trailing_type_var.get()
        try:
            config.config["backtest"]["trailing_param"] = float(trailing_param_var.get())
        except ValueError:
            pass

        # 포지션 사이징 설정 저장
        config.config["backtest"]["position_sizing"] = position_sizing_var.get()

        # 워크포워드 설정 저장
        config.config["backtest"]["walk_forward_enabled"] = walk_forward_var.get()

        # 수수료/슬리피지 저장
        try:
            config.config["backtest"]["commission_rate"] = float(commission_var.get()) / 100.0
        except ValueError:
            pass
        try:
            config.config["backtest"]["slippage_pct"] = float(slippage_var.get()) / 100.0
        except ValueError:
            pass

        config.save_config(config.get_config())

        stoploss = sl_pct / 100.0 if stoploss_enabled_var.get() else None
        use_regime = regime_filter_var.get()
        trailing = None
        if trailing_enabled_var.get():
            try:
                trailing = (trailing_type_var.get(), float(trailing_param_var.get()))
            except ValueError:
                trailing = None
        pos_sizing = position_sizing_var.get()
        use_walk_forward = walk_forward_var.get()

        # 분석 중 로딩 표시
        search_btn.config(state=tk.DISABLED)
        loading_bar = ttk.Progressbar(btn_frame, mode='indeterminate', length=120)
        loading_bar.pack(side=tk.LEFT, padx=5)
        loading_bar.start(15)
        popup.update_idletasks()

        def _finish():
            try:
                loading_bar.stop()
                loading_bar.destroy()
                search_btn.config(state=tk.NORMAL)
            except tk.TclError:
                pass

        def _run():
            try:
                if mode == "absolute":
                    start_str = bt_start_date_entry.get().strip()
                    end_str = bt_end_date_entry.get().strip()
                    run_backtest(ticker_symbol, None, None, method, stoploss, use_regime,
                                 trailing=trailing, pos_sizing=pos_sizing,
                                 use_walk_forward=use_walk_forward,
                                 start_date=start_str, end_date=end_str)
                else:
                    run_backtest(ticker_symbol, value, unit, method, stoploss, use_regime,
                                 trailing=trailing, pos_sizing=pos_sizing,
                                 use_walk_forward=use_walk_forward)
            finally:
                try:
                    popup.after(0, _finish)
                except tk.TclError:
                    pass

        threading.Thread(target=_run, daemon=True).start()

    # --- Plot functions ---

    # 비교/민감도 실행 중 개별 전략 차트 팝업 억제 플래그
    _suppress_chart = [False]

    def _create_graph_popup(fig, title, help_text=""):
        """차트를 result_container 안에 임베딩."""
        if _suppress_chart[0]:
            try:
                plt.close(fig)
            except Exception:
                pass
            return None
        # matplotlib 기본 figure manager 창이 뜨지 않도록 제거 (FigureCanvasTkAgg로 직접 임베딩)
        try:
            plt.close(fig)
        except Exception:
            pass
        open_figures.append(fig)

        chart_frame = tk.LabelFrame(result_container, text=title, font=("Arial", 10, "bold"))
        chart_frame.pack(fill=tk.X, padx=10, pady=5)

        canvas = FigureCanvasTkAgg(fig, master=chart_frame)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        canvas.draw()

        # 차트 읽는 법
        if help_text:
            help_frame = tk.LabelFrame(chart_frame, text="차트 읽는 법", font=("Arial", 9, "bold"))
            help_frame.pack(fill=tk.X, padx=8, pady=(0, 2))
            tk.Label(help_frame, text=help_text, font=("Arial", 9), justify=tk.LEFT,
                     wraplength=700, anchor="w").pack(padx=8, pady=4, anchor="w")

        # PNG 저장 버튼
        tk.Button(chart_frame, text="PNG 저장",
                  command=lambda f=fig: _save_fig_png(f)).pack(pady=3)

        return chart_frame

    def _show_result_summary(profits, buy_dates, sell_dates, close_series):
        """백테스트 결과 요약 패널 — 투자 판단에 유의미한 지표만 표시."""
        if not profits:
            return

        total_return = (1 + pd.Series(profits)).prod() - 1

        # 연환산 수익률: 분석 기간 기준
        if len(close_series) >= 2:
            total_days = (close_series.index[-1] - close_series.index[0]).days
        else:
            total_days = 1
        if total_days > 0:
            annual_return = (1 + total_return) ** (365 / total_days) - 1
        else:
            annual_return = 0.0

        # MDD (Maximum Drawdown): 누적 수익 곡선 기준
        equity = [1.0]
        for p in profits:
            equity.append(equity[-1] * (1 + p))
        equity = pd.Series(equity)
        peak = equity.cummax()
        drawdown = (equity - peak) / peak
        mdd = drawdown.min()

        # MDD 발생 날짜: drawdown이 최저인 거래의 매도일
        mdd_idx = drawdown.idxmin()  # equity index (0=초기, 1=첫거래 후, ...)
        if mdd_idx >= 1 and (mdd_idx - 1) < len(sell_dates):
            mdd_date = pd.Timestamp(sell_dates[mdd_idx - 1]).strftime('%Y-%m-%d')
        else:
            mdd_date = ""

        # MDD 회복 기간 계산
        mdd_recovery_text = ""
        if mdd < 0 and mdd_idx > 0:
            trough_val = equity.iloc[mdd_idx]
            pre_peak_val = peak.iloc[mdd_idx]
            # 트로프 이후 이전 피크값 회복까지의 거래 수
            recovered = False
            for ri in range(mdd_idx + 1, len(equity)):
                if equity.iloc[ri] >= pre_peak_val:
                    recovery_trades = ri - mdd_idx
                    mdd_recovery_text = f"{recovery_trades}거래"
                    recovered = True
                    break
            if not recovered:
                mdd_recovery_text = "미회복"

        # 최대 수익 / 최대 손실 거래 + 날짜
        best_idx = profits.index(max(profits))
        worst_idx = profits.index(min(profits))
        best_trade = profits[best_idx]
        worst_trade = profits[worst_idx]

        def _fmt_trade_date(idx, buy_list, sell_list):
            """매수~매도 날짜 문자열 생성."""
            parts = []
            if idx < len(buy_list):
                bd = buy_list[idx]
                parts.append(pd.Timestamp(bd).strftime('%Y-%m-%d'))
            if idx < len(sell_list):
                sd = sell_list[idx]
                parts.append(pd.Timestamp(sd).strftime('%Y-%m-%d'))
            if len(parts) == 2:
                return f"{parts[0]} ~ {parts[1]}"
            elif parts:
                return parts[0]
            return ""

        best_date = _fmt_trade_date(best_idx, buy_dates, sell_dates)
        worst_date = _fmt_trade_date(worst_idx, buy_dates, sell_dates)

        # 승률 / 손익비 / Profit Factor / 샤프비율 / 소르티노비율
        profit_series = pd.Series(profits)
        winning = profit_series[profit_series > 0]
        losing = profit_series[profit_series < 0]
        total_trades = len(profits)
        win_count = len(winning)
        win_rate = win_count / total_trades if total_trades > 0 else 0.0

        avg_win = winning.mean() if len(winning) > 0 else 0.0
        avg_loss = abs(losing.mean()) if len(losing) > 0 else 0.0
        payoff_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')

        total_gain = winning.sum() if len(winning) > 0 else 0.0
        total_loss = abs(losing.sum()) if len(losing) > 0 else 0.0
        profit_factor = total_gain / total_loss if total_loss > 0 else float('inf')

        # 샤프비율 (연환산, 동적 무위험수익률)
        risk_free = config.get_risk_free_rate()
        risk_free_per_trade = risk_free / 252  # 일일 무위험수익률 근사
        if len(profits) >= 2 and profit_series.std() > 0:
            excess_returns = profit_series - risk_free_per_trade
            sharpe = excess_returns.mean() / profit_series.std() * np.sqrt(252)
        else:
            sharpe = 0.0

        # 소르티노비율 (하방 변동성만 사용)
        downside = profit_series[profit_series < 0]
        if len(downside) >= 2 and downside.std() > 0:
            sortino = (profit_series.mean() - risk_free_per_trade) / downside.std() * np.sqrt(252)
        else:
            sortino = 0.0

        summary_frame = tk.LabelFrame(result_container, text="백테스트 결과 요약", font=("Arial", 10, "bold"))
        summary_frame.pack(fill=tk.X, padx=10, pady=5)

        def _fmt_ratio(val):
            if val == float('inf'):
                return "∞"
            return f"{val:.2f}"

        # SPY 벤치마크 비교
        spy_return_text = ""
        alpha_text = ""
        try:
            if len(close_series) >= 2:
                spy_start = close_series.index[0].strftime('%Y-%m-%d')
                spy_end = close_series.index[-1].strftime('%Y-%m-%d')
                spy_data = yf.download("SPY", start=spy_start, end=spy_end)
                if isinstance(spy_data.columns, pd.MultiIndex):
                    spy_data.columns = spy_data.columns.get_level_values(0)
                if not spy_data.empty and len(spy_data) >= 2:
                    spy_buy_hold = (spy_data['Close'].iloc[-1] / spy_data['Close'].iloc[0]) - 1
                    spy_return_text = f"{spy_buy_hold:.2%}"
                    alpha = total_return - spy_buy_hold
                    alpha_text = f"{alpha:+.2%}"
        except Exception as e:
            logging.warning(f"[BENCHMARK] SPY comparison failed: {e}")

        rows = [
            ("총 수익률", f"{total_return:.2%}"),
            ("연환산 수익률", f"{annual_return:.2%}"),
            ("최대 낙폭 (MDD)", f"{mdd:.2%}  ({mdd_date})" if mdd_date else f"{mdd:.2%}"),
            ("MDD 회복 기간", mdd_recovery_text if mdd_recovery_text else "N/A"),
            ("최대 수익 거래", f"{best_trade:.2%}  ({best_date})"),
            ("최대 손실 거래", f"{worst_trade:.2%}  ({worst_date})"),
            ("거래 횟수", f"{total_trades}회"),
            ("승률", f"{win_rate:.1%} ({win_count}/{total_trades})"),
            ("평균 손익비", _fmt_ratio(payoff_ratio)),
            ("Profit Factor", _fmt_ratio(profit_factor)),
            ("샤프 비율", f"{sharpe:.2f}"),
            ("소르티노 비율", f"{sortino:.2f}"),
            ("수수료", f"거래당 {_get_commission_rate():.2%} (왕복 {_get_commission_rate()*2:.2%})"),
            ("슬리피지", f"거래당 {_get_slippage_pct():.2%} (왕복 {_get_slippage_pct()*2:.2%})"),
            ("무위험이자율", f"{risk_free:.2%} (10년 국채)"),
        ]
        if spy_return_text:
            rows.append(("SPY 수익률 (Buy&Hold)", spy_return_text))
        if alpha_text:
            rows.append(("알파 (초과수익)", alpha_text))
        for label_text, value_text in rows:
            row_frame = tk.Frame(summary_frame)
            row_frame.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(row_frame, text=label_text, font=("Arial", 10), anchor="w", width=16).pack(side=tk.LEFT)
            # "?" 아이콘 + 설명 툴팁
            help_desc = RESULT_HELP.get(label_text, "")
            if help_desc:
                q_label = tk.Label(row_frame, text="?", font=("Arial", 9, "bold"), fg="#4A90D9",
                                   cursor="question_arrow")
                q_label.pack(side=tk.LEFT, padx=(2, 4))
                HelpTooltip(q_label, help_desc)
            fg = "#000000"
            if value_text.startswith("-"):
                fg = "#E74C3C"
            elif not value_text.startswith("0") and "0.00%" not in value_text:
                fg = "#2E7D32"
            tk.Label(row_frame, text=value_text, font=("Arial", 10, "bold"), anchor="e", fg=fg).pack(side=tk.RIGHT)

    def _show_holdings_comparison(profits, buy_dates, sell_dates, close_series):
        """보유 종목이면 전략 vs 실제 보유 성과 비교 표시."""
        if app_state is None:
            return
        try:
            import holdings_manager
            holdings = getattr(app_state, 'holdings', None)
            if not holdings:
                return
            holding = holdings_manager.get_holding(holdings, ticker_symbol)
            if not holding or holding["quantity"] <= 0:
                return

            h_avg = holding["avg_price"]
            h_qty = holding["quantity"]
            h_realized = holding["total_realized_pnl"]
            transactions = holding.get("transactions", [])

            # 가장 이른 매수일 찾기
            first_buy_date = None
            for tx in transactions:
                if tx["type"] == "buy" and tx.get("date"):
                    try:
                        d = datetime.strptime(tx["date"], "%Y-%m-%d")
                        if first_buy_date is None or d < first_buy_date:
                            first_buy_date = d
                    except ValueError:
                        pass

            if close_series.empty or len(close_series) < 2:
                return

            # 현재가 (close_series 마지막)
            current_price = float(close_series.iloc[-1])

            # 보유 성과 계산
            total_invested = 0.0
            for tx in transactions:
                if tx["type"] == "buy":
                    total_invested += tx["quantity"] * tx["price"]

            unrealized_pnl = (current_price - h_avg) * h_qty
            total_pnl = unrealized_pnl + h_realized
            hold_return = total_pnl / total_invested if total_invested > 0 else 0

            # 전략 수익률
            strategy_return = (1 + pd.Series(profits)).prod() - 1 if profits else 0

            # 비교 프레임
            comp_frame = tk.LabelFrame(result_container, text="★ 보유 vs 전략 비교",
                                        font=("Arial", 10, "bold"), fg="#1A5276")
            comp_frame.pack(fill=tk.X, padx=10, pady=5)

            # 보유 정보 요약
            hold_sign = "+" if hold_return >= 0 else ""
            hold_color = "#2E7D32" if hold_return >= 0 else "#E74C3C"
            strat_sign = "+" if strategy_return >= 0 else ""
            strat_color = "#2E7D32" if strategy_return >= 0 else "#E74C3C"

            diff = strategy_return - hold_return
            diff_sign = "+" if diff >= 0 else ""
            diff_color = "#2E7D32" if diff >= 0 else "#E74C3C"

            comp_rows = [
                ("보유 수익률 (Buy&Hold)", f"{hold_sign}{hold_return:.2%}", hold_color),
                ("  투자금", f"${total_invested:,.0f}", "#000"),
                ("  미실현 손익", f"{'+'if unrealized_pnl>=0 else ''}${unrealized_pnl:,.0f}", hold_color),
                ("  실현 손익", f"{'+'if h_realized>=0 else ''}${h_realized:,.0f}",
                 "#2E7D32" if h_realized >= 0 else "#E74C3C"),
                ("전략 수익률", f"{strat_sign}{strategy_return:.2%}", strat_color),
                ("전략 - 보유 차이", f"{diff_sign}{diff:.2%}", diff_color),
            ]

            for label_text, value_text, fg in comp_rows:
                row_frame = tk.Frame(comp_frame)
                row_frame.pack(fill=tk.X, padx=8, pady=1)
                tk.Label(row_frame, text=label_text, font=("Arial", 10), anchor="w", width=20).pack(side=tk.LEFT)
                tk.Label(row_frame, text=value_text, font=("Arial", 10, "bold"), anchor="e", fg=fg).pack(side=tk.RIGHT)

            # 판정
            if diff > 0.01:
                verdict = "전략이 보유보다 유리합니다"
                verdict_color = "#2E7D32"
            elif diff < -0.01:
                verdict = "보유(Buy&Hold)가 전략보다 유리합니다"
                verdict_color = "#E74C3C"
            else:
                verdict = "전략과 보유 성과가 비슷합니다"
                verdict_color = "#666"

            tk.Label(comp_frame, text=f"→ {verdict}", font=("Arial", 10, "bold"),
                     fg=verdict_color).pack(padx=8, pady=(3, 5), anchor="w")

            # 누적 수익률 비교 차트
            if first_buy_date and len(close_series) >= 2:
                # 전략 에쿼티 커브
                strategy_equity = [1.0]
                for p in profits:
                    strategy_equity.append(strategy_equity[-1] * (1 + p))

                # 보유 에쿼티 커브: close_series 기준 (매수일 이후)
                buy_date_ts = pd.Timestamp(first_buy_date)
                hold_series = close_series[close_series.index >= buy_date_ts]
                if len(hold_series) >= 2:
                    hold_equity = hold_series / hold_series.iloc[0]

                    fig = Figure(figsize=(8, 4)); ax = fig.add_subplot(111)
                    # 전략 커브 (거래 시점 기준이라 x축을 sell_dates로 매핑)
                    strat_dates = [close_series.index[0]]
                    for sd in sell_dates:
                        strat_dates.append(pd.Timestamp(sd))
                    if len(strat_dates) == len(strategy_equity):
                        ax.plot(strat_dates, strategy_equity, label=f"전략 ({strategy_return:.1%})",
                                color="#4A90D9", linewidth=2)
                    # 보유 커브
                    ax.plot(hold_equity.index, hold_equity.values,
                            label=f"보유 ({hold_return:.1%})",
                            color="#E67E22", linewidth=2, linestyle="--")
                    ax.axhline(y=1.0, color="gray", linewidth=0.5, linestyle=":")
                    ax.set_title(f"{ticker_symbol} 전략 vs 보유 성과 비교", fontsize=12, fontweight="bold")
                    ax.set_ylabel("누적 수익률")
                    ax.legend(fontsize=9)
                    ax.grid(alpha=0.3)
                    fig.tight_layout()

                    popup.after(0, lambda f=fig: _create_graph_popup(f, f"{ticker_symbol} 전략 vs 보유 비교",
                                        "파란선: 전략 매매 수익 | 주황 점선: 보유(Buy&Hold) 수익"))

        except Exception as e:
            logging.warning(f"[BACKTEST] Holdings comparison error: {e}")

    def _show_monte_carlo(profits, close_series):
        """몬테카를로 시뮬레이션 — 거래 수익률을 부트스트랩하여 전략 신뢰구간 추정."""
        if not profits or len(profits) < 3:
            return

        n_simulations = 1000
        n_trades = len(profits)
        profits_arr = np.array(profits)

        # 부트스트랩: 거래 수익률을 복원추출하여 N번 시뮬레이션
        sim_returns = np.zeros(n_simulations)
        sim_mdds = np.zeros(n_simulations)
        for i in range(n_simulations):
            sampled = np.random.choice(profits_arr, size=n_trades, replace=True)
            # 누적 수익률
            equity = np.cumprod(1 + sampled)
            sim_returns[i] = equity[-1] - 1
            # MDD
            peak = np.maximum.accumulate(equity)
            dd = (equity - peak) / peak
            sim_mdds[i] = dd.min()

        # 통계 계산
        mean_return = np.mean(sim_returns)
        median_return = np.median(sim_returns)
        ci_5 = np.percentile(sim_returns, 5)
        ci_25 = np.percentile(sim_returns, 25)
        ci_75 = np.percentile(sim_returns, 75)
        ci_95 = np.percentile(sim_returns, 95)
        prob_loss = np.mean(sim_returns < 0) * 100
        avg_mdd = np.mean(sim_mdds)

        # 결과 프레임
        mc_frame = tk.LabelFrame(result_container, text="몬테카를로 시뮬레이션 (1,000회)",
                                  font=("Arial", 10, "bold"))
        mc_frame.pack(fill=tk.X, padx=10, pady=5)

        mc_rows = [
            ("평균 수익률", f"{mean_return:.2%}"),
            ("중앙값 수익률", f"{median_return:.2%}"),
            ("90% 신뢰구간", f"{ci_5:.2%} ~ {ci_95:.2%}"),
            ("50% 신뢰구간", f"{ci_25:.2%} ~ {ci_75:.2%}"),
            ("손실 확률", f"{prob_loss:.1f}%"),
            ("평균 MDD", f"{avg_mdd:.2%}"),
        ]
        for label_text, value_text in mc_rows:
            row_frame = tk.Frame(mc_frame)
            row_frame.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(row_frame, text=label_text, font=("Arial", 9), anchor="w", width=16).pack(side=tk.LEFT)
            fg = "#E74C3C" if "손실" in label_text or "MDD" in label_text else "#000"
            tk.Label(row_frame, text=value_text, font=("Arial", 9, "bold"), anchor="e", fg=fg).pack(side=tk.RIGHT)

        # 분포 차트 (result_container 내 임베드)
        chart_frame = tk.LabelFrame(result_container, text="몬테카를로 분포 차트",
                                     font=("Arial", 10, "bold"))
        chart_frame.pack(fill=tk.X, padx=10, pady=5)

        fig = Figure(figsize=(10, 3.5)); ax1 = fig.add_subplot(1, 2, 1); ax2 = fig.add_subplot(1, 2, 2)

        # 수익률 분포
        ax1.hist(sim_returns * 100, bins=50, color="#4A90D9", alpha=0.7, edgecolor="white")
        ax1.axvline(x=0, color="#E74C3C", linewidth=1.5, linestyle="--", label="손익분기")
        ax1.axvline(x=mean_return * 100, color="#2E7D32", linewidth=1.5, label=f"평균 {mean_return:.1%}")
        actual_return = (1 + pd.Series(profits)).prod() - 1
        ax1.axvline(x=actual_return * 100, color="#E67E22", linewidth=2, label=f"실제 {actual_return:.1%}")
        ax1.set_title("수익률 분포 (MC)", fontsize=11, fontweight="bold")
        ax1.set_xlabel("수익률 (%)")
        ax1.set_ylabel("빈도")
        ax1.legend(fontsize=8)

        # MDD 분포
        ax2.hist(sim_mdds * 100, bins=50, color="#E74C3C", alpha=0.7, edgecolor="white")
        ax2.axvline(x=avg_mdd * 100, color="#000", linewidth=1.5, label=f"평균 MDD {avg_mdd:.1%}")
        ax2.set_title("MDD 분포 (MC)", fontsize=11, fontweight="bold")
        ax2.set_xlabel("MDD (%)")
        ax2.set_ylabel("빈도")
        ax2.legend(fontsize=8)

        fig.tight_layout()
        open_figures.append(fig)
        canvas_mc = FigureCanvasTkAgg(fig, master=chart_frame)
        canvas_mc.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        canvas_mc.draw()

    def _save_trades_csv(buy_dates, sell_dates, profits):
        """Phase 12-1: Export trade history as CSV."""
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"{ticker_symbol}_trades.csv"
        )
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["날짜", "유형", "수익률"])
                for i, bd in enumerate(buy_dates):
                    writer.writerow([str(bd), "매수", ""])
                    if i < len(sell_dates):
                        p = profits[i] if i < len(profits) else ""
                        writer.writerow([str(sell_dates[i]), "매도", f"{p:.4f}" if isinstance(p, float) else ""])
            messagebox.showinfo("저장 완료", f"거래 내역이 저장되었습니다:\n{path}")
        except Exception as e:
            messagebox.showerror("저장 실패", f"CSV 저장 오류: {e}")

    def plot_macd_backtest(ticker_symbol, close_prices, macd_line, signal_line, buy_signals, sell_signals):
        fig = Figure(figsize=(10, 6)); ax1 = fig.add_subplot(111)

        ax1.plot(close_prices.index, close_prices, label='주가', color='black')
        ax1.set_ylabel('가격 ($)')
        ax1.yaxis.set_major_formatter(ticker.FormatStrFormatter('$%.2f'))
        ax1.grid()

        for idx, buy in enumerate(buy_signals):
            ax1.scatter(close_prices.index[buy], close_prices.iloc[buy], marker='^', color='green',
                        label='매수 신호' if idx == 0 else "")
        for idx, sell in enumerate(sell_signals):
            ax1.scatter(close_prices.index[sell], close_prices.iloc[sell], marker='v', color='red',
                        label='매도 신호' if idx == 0 else "")

        ax2 = ax1.twinx()
        ax2.plot(close_prices.index, macd_line, label='MACD 선', color='blue')
        ax2.plot(close_prices.index, signal_line, label='시그널 선', color='orange')
        ax2.set_ylabel('MACD 지표')

        ax1.set_title(f"{stock_display} MACD 교차 백테스트")

        lines_labels = [ax.get_legend_handles_labels() for ax in [ax1, ax2]]
        lines, labels = [sum(lol, []) for lol in zip(*lines_labels)]
        ax1.legend(lines, labels, loc='upper left')

        return fig, f"{stock_display} 백테스트 결과", CHART_HELP.get("macd", "")

    def plot_rsi_backtest(ticker_symbol, close_prices, rsi, buy_signals, sell_signals):
        fig = Figure(figsize=(10, 8)); ax1 = fig.add_subplot(2, 1, 1); ax2 = fig.add_subplot(2, 1, 2, sharex=ax1)

        ax1.plot(close_prices.index, close_prices, label='주가', color='black')
        ax1.set_ylabel('가격 ($)')
        ax1.yaxis.set_major_formatter(ticker.FormatStrFormatter('$%.2f'))
        ax1.grid()

        for idx, buy in enumerate(buy_signals):
            ax1.scatter(close_prices.index[buy], close_prices.iloc[buy], marker='^', color='green',
                        label='매수 신호' if idx == 0 else "")
        for idx, sell in enumerate(sell_signals):
            ax1.scatter(close_prices.index[sell], close_prices.iloc[sell], marker='v', color='red',
                        label='매도 신호' if idx == 0 else "")

        rsi_upper = config.config["current"]["rsi"]['upper']
        rsi_lower = config.config["current"]["rsi"]['lower']
        ax2.plot(close_prices.index, rsi, label='RSI', color='purple')
        ax2.axhline(rsi_upper, color='red', linestyle='--', label=f'과매수 ({rsi_upper})')
        ax2.axhline(rsi_lower, color='green', linestyle='--', label=f'과매도 ({rsi_lower})')
        ax2.set_ylabel('RSI 값')
        ax2.set_ylim(0, 100)
        ax2.grid()

        ax1.set_title(f"{stock_display} RSI 백테스트")

        lines_labels = [ax.get_legend_handles_labels() for ax in [ax1, ax2]]
        lines, labels = [sum(lol, []) for lol in zip(*lines_labels)]
        ax1.legend(lines, labels, loc='upper left')

        return fig, f"{stock_display} 백테스트 결과", CHART_HELP.get("rsi", "")

    def plot_macd_rsi_backtest(data, buy_dates, sell_dates, ticker_name):
        fig = Figure(figsize=(14, 10))

        ax1 = fig.add_subplot(3, 1, 1)
        ax1.set_title(f"{ticker_name} MACD+RSI 복합 백테스트")
        ax1.plot(data["Close"], label="주가", color="black")
        ma_s = config.config["current"]["ma_cross"]["short"]
        ma_l = config.config["current"]["ma_cross"]["long"]
        ax1.plot(data["Close"].rolling(window=ma_s).mean(), label=f'MA({ma_s})', linestyle="--", color="blue")
        ax1.plot(data["Close"].rolling(window=ma_l).mean(), label=f'MA({ma_l})', linestyle="--", color="orange")

        first_buy = True
        for date in buy_dates:
            if date in data.index:
                ax1.axvline(x=date, color='green', linestyle='--', alpha=0.2)
                ax1.scatter(date, data.loc[date, "Close"], marker="^", color="green",
                            label="매수 신호" if first_buy else "")
                first_buy = False

        first_sell = True
        for date in sell_dates:
            if date in data.index:
                ax1.axvline(x=date, color='red', linestyle='--', alpha=0.2)
                ax1.scatter(date, data.loc[date, "Close"], marker="v", color="red",
                            label="매도 신호" if first_sell else "")
                first_sell = False

        ax1.set_ylabel("가격 ($)")
        ax1.legend(loc="upper left")

        rsi_period = config.config["current"]["rsi"]['period']
        rsi_upper = config.config["current"]["rsi"]['upper']
        rsi_lower = config.config["current"]["rsi"]['lower']

        ax2 = fig.add_subplot(3, 1, 2)
        ax2.plot(data["RSI"], label=f'RSI ({rsi_period})', color="purple")
        ax2.axhline(rsi_upper, linestyle="--", color="red", alpha=0.5)
        ax2.axhline(rsi_lower, linestyle="--", color="green", alpha=0.5)
        ax2.set_ylabel("RSI 값")
        ax2.legend(loc="upper left")

        ax3 = fig.add_subplot(3, 1, 3)
        ax3.plot(data["MACD"], label="MACD", color="blue")
        ax3.plot(data["Signal"], label="시그널 선", color="red")
        ax3.axhline(0, linestyle="--", color="black", alpha=0.3)
        ax3.set_ylabel("MACD 지표")
        ax3.legend(loc="upper left")

        fig.tight_layout()
        return fig, f"{ticker_name} MACD+RSI 백테스트", CHART_HELP.get("macd_rsi", "")

    def plot_bollinger(data, buy_dates, sell_dates, ticker_name):
        fig = Figure(figsize=(12, 6)); ax = fig.add_subplot(111)
        ax.plot(data.index, data['Close'], label='주가', color='black')
        ax.plot(data.index, data['UpperBand'], label='상단 밴드', linestyle='--')
        ax.plot(data.index, data['LowerBand'], label='하단 밴드', linestyle='--')
        ax.scatter(buy_dates, data.loc[buy_dates]['Close'], marker='^', color='green', label='매수 신호', s=100)
        ax.scatter(sell_dates, data.loc[sell_dates]['Close'], marker='v', color='red', label='매도 신호', s=100)
        ax.set_title(f"{ticker_name} 볼린저밴드 백테스트")
        ax.set_xlabel("날짜")
        ax.set_ylabel("가격 ($)")
        ax.legend()
        ax.grid()
        return fig, f"{ticker_name} 볼린저밴드 백테스트", CHART_HELP.get("bollinger", "")

    def plot_ma_cross(data, buy_dates, sell_dates, ticker_name, chart_key="ma_cross"):
        fig = Figure(figsize=(12, 6)); ax = fig.add_subplot(111)
        ma_s = config.config["current"]["ma_cross"]["short"]
        ma_l = config.config["current"]["ma_cross"]["long"]
        ax.plot(data.index, data['Close'], label='주가', color='black')
        ax.plot(data.index, data['Short_MA'], label=f'단기 MA ({ma_s})', linestyle='--')
        ax.plot(data.index, data['Long_MA'], label=f'장기 MA ({ma_l})', linestyle='--')
        ax.scatter(buy_dates, data.loc[buy_dates]['Close'], marker='^', color='green', label='매수 신호', s=100)
        ax.scatter(sell_dates, data.loc[sell_dates]['Close'], marker='v', color='red', label='매도 신호', s=100)
        ax.set_title(f"{ticker_name} 이동평균 교차 백테스트")
        ax.set_xlabel("날짜")
        ax.set_ylabel("가격 ($)")
        ax.legend()
        ax.grid()
        return fig, f"{ticker_name} MA교차 백테스트", CHART_HELP.get(chart_key, "")

    def plot_momentum_with_indicators(data, short_ma, long_ma, upper_band, lower_band, buy_dates, sell_dates, rsi, macd,
                                      signal, ticker_name):
        fig = Figure(figsize=(14, 10))
        gs = fig.add_gridspec(3, 1, height_ratios=[2, 1, 1])
        ax_price = fig.add_subplot(gs[0])
        ax_rsi = fig.add_subplot(gs[1], sharex=ax_price)
        ax_macd = fig.add_subplot(gs[2], sharex=ax_price)

        ma_s = config.config['current']['ma_cross']['short']
        ma_l = config.config['current']['ma_cross']['long']

        ax_price.plot(data.index, data['Close'], label='주가', color='black', linewidth=1.5)
        ax_price.plot(data.index, short_ma, label=f'단기 MA ({ma_s})', linestyle='--', color='blue', linewidth=1.5)
        ax_price.plot(data.index, long_ma, label=f'장기 MA ({ma_l})', linestyle='--', color='orange', linewidth=1.5)
        ax_price.fill_between(data.index, lower_band, upper_band, color='lightgray', alpha=0.3,
                              label='볼린저 밴드 영역')

        if buy_dates:
            ax_price.scatter(buy_dates, data.loc[buy_dates, 'Close'], marker='^', color='green', label='매수 신호',
                             s=100, edgecolor='black')
        if sell_dates:
            ax_price.scatter(sell_dates, data.loc[sell_dates, 'Close'], marker='v', color='red', label='매도 신호',
                             s=100, edgecolor='black')

        for buy, sell in zip(buy_dates, sell_dates):
            ax_price.axvspan(buy, sell, color='lightgreen', alpha=0.3)

        ax_price.set_title(f"{ticker_name} 모멘텀 전략 백테스트", fontsize=16)
        ax_price.set_ylabel("가격 ($)")
        ax_price.legend()
        ax_price.grid(linestyle='--', alpha=0.7)

        rsi_period = config.config["current"]["rsi"]['period']
        rsi_upper = config.config["current"]["rsi"]['upper']
        rsi_lower = config.config["current"]["rsi"]['lower']

        ax_rsi.plot(data.index, rsi, label=f'RSI ({rsi_period})', color='purple')
        ax_rsi.axhline(rsi_upper, linestyle='--', color='red', alpha=0.5)
        ax_rsi.axhline(rsi_lower, linestyle='--', color='green', alpha=0.5)
        ax_rsi.set_ylabel("RSI 값")
        ax_rsi.legend()
        ax_rsi.grid(linestyle='--', alpha=0.7)

        ax_macd.plot(data.index, macd, label='MACD', color='blue')
        ax_macd.plot(data.index, signal, label='시그널 선', color='red')
        ax_macd.set_ylabel("MACD 지표")
        ax_macd.legend()
        ax_macd.grid(linestyle='--', alpha=0.7)

        ax_macd.set_xlabel("날짜")
        fig.tight_layout()
        return fig, f"{ticker_name} 모멘텀 백테스트", CHART_HELP.get("momentum_signal", "")

    # Phase 8-1: Strategy functions split from run_backtest

    def _run_macd(data, close_prices):
        macd_short = close_prices.ewm(span=config.config["current"]["macd"]["short"], adjust=False).mean()
        macd_long = close_prices.ewm(span=config.config["current"]["macd"]["long"], adjust=False).mean()
        macd_line = macd_short - macd_long
        signal_line = macd_line.ewm(span=config.config["current"]["macd"]["signal"], adjust=False).mean()

        buy_signals = []
        sell_signals = []

        for i in range(1, len(macd_line)):
            macd_prev = macd_line.iloc[i - 1].item()
            macd_now = macd_line.iloc[i].item()
            signal_prev = signal_line.iloc[i - 1].item()
            signal_now = signal_line.iloc[i].item()

            if macd_prev < signal_prev and macd_now > signal_now:
                buy_signals.append(i)
            elif macd_prev > signal_prev and macd_now < signal_now:
                sell_signals.append(i)

        chart_info = plot_macd_backtest(stock_display, close_prices, macd_line, signal_line, buy_signals, sell_signals)
        return [], [], [], chart_info

    def _run_rsi(data, close_prices):
        period = config.config["current"]["rsi"]['period']
        rsi = calculate_rsi_for_backtest(close_prices, period)

        buy_signals = []
        sell_signals = []
        lower = config.config["current"]["rsi"]['lower']
        upper = config.config["current"]["rsi"]['upper']
        for i in range(1, len(rsi)):
            if rsi.iloc[i].item() < lower:
                buy_signals.append(i)
            elif rsi.iloc[i].item() > upper:
                sell_signals.append(i)

        chart_info = plot_rsi_backtest(stock_display, close_prices, rsi, buy_signals, sell_signals)
        return [], [], [], chart_info

    def _run_macd_rsi(data, close_prices):
        macd_conf = config.config["current"]["macd"]
        rsi_period = config.config["current"]["rsi"]['period']

        short_ema = data["Close"].ewm(span=macd_conf["short"], adjust=False).mean()
        long_ema = data["Close"].ewm(span=macd_conf["long"], adjust=False).mean()
        macd = short_ema - long_ema
        signal = macd.ewm(span=macd_conf["signal"], adjust=False).mean()

        delta = data["Close"].diff()
        gain = delta.clip(lower=0).rolling(window=rsi_period).mean()
        loss = -delta.clip(upper=0).rolling(window=rsi_period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        data["MACD"] = macd
        data["Signal"] = signal
        data["RSI"] = rsi

        in_position = False
        entry_price = 0
        buy_dates, sell_dates, profits = [], [], []

        lower = config.config["current"]["rsi"]['lower']
        upper = config.config["current"]["rsi"]['upper']
        for i in range(1, len(data)):
            prev_macd, prev_signal = macd.iloc[i - 1], signal.iloc[i - 1]
            curr_macd, curr_signal = macd.iloc[i], signal.iloc[i]
            rsi_val = rsi.iloc[i]

            if not in_position and prev_macd < prev_signal and curr_macd > curr_signal and rsi_val < lower:
                entry_price = data["Close"].iloc[i]
                buy_dates.append(data.index[i])
                in_position = True
            elif in_position and (curr_macd < curr_signal or rsi_val > upper):
                exit_price = data["Close"].iloc[i]
                profits.append(_safe_division(exit_price, entry_price))
                sell_dates.append(data.index[i])
                in_position = False

        if in_position:
            exit_price = data["Close"].iloc[-1]
            profits.append(_safe_division(exit_price, entry_price))

        chart_info = None
        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[MACD+RSI] Total return: {total_return:.2%}")
            chart_info = plot_macd_rsi_backtest(data, buy_dates, sell_dates, stock_display)
        else:
            if not _suppress_chart[0]:
                popup.after(0, lambda: messagebox.showinfo("알림", f"[{ticker_symbol}] MACD+RSI 전략으로 거래 없음"))

        return buy_dates, sell_dates, profits, chart_info

    def _run_bollinger(data, close_prices):
        window = config.config["current"]["bollinger"]["period"]
        num_std = config.config["current"]["bollinger"]["std_dev_multiplier"]

        ma = close_prices.rolling(window=window).mean()
        std = close_prices.rolling(window=window).std()
        upper_band = ma + (std * num_std)
        lower_band = ma - (std * num_std)

        data['MA'] = ma
        data['STD'] = std
        data['UpperBand'] = upper_band
        data['LowerBand'] = lower_band

        expected_cols = ['LowerBand', 'UpperBand']
        if all(col in data.columns for col in expected_cols):
            data = data.dropna(subset=expected_cols)
        else:
            logging.error(f"[BACKTEST] Missing columns: {expected_cols}, found: {data.columns.tolist()}")
            return [], [], [], None

        use_rebound = config.config["current"]["bollinger"]["use_rebound"]
        buy_dates = []
        sell_dates = []
        in_position = False
        entry_price = 0
        profits = []

        if use_rebound:
            for i in range(len(data) - 2):
                if not in_position:
                    if data['Close'].iloc[i] < data['LowerBand'].iloc[i]:
                        if data['Close'].iloc[i + 1] > data['Close'].iloc[i]:
                            in_position = True
                            entry_price = data['Close'].iloc[i + 1]
                            buy_dates.append(data.index[i + 1])
                else:
                    if data['Close'].iloc[i] > data['UpperBand'].iloc[i]:
                        if data['Close'].iloc[i + 1] < data['Close'].iloc[i]:
                            exit_price = data['Close'].iloc[i + 1]
                            profits.append(_safe_division(exit_price, entry_price))
                            sell_dates.append(data.index[i + 1])
                            in_position = False
            if in_position:
                exit_price = data['Close'].iloc[-1]
                profits.append(_safe_division(exit_price, entry_price))
        else:
            buy_signal = data['Close'] < data['LowerBand']
            sell_signal = data['Close'] > data['UpperBand']

            for i in range(len(data)):
                if not in_position and buy_signal.iloc[i]:
                    in_position = True
                    entry_price = data['Close'].iloc[i]
                    buy_dates.append(data.index[i])
                elif in_position and sell_signal.iloc[i]:
                    exit_price = data['Close'].iloc[i]
                    profits.append(_safe_division(exit_price, entry_price))
                    sell_dates.append(data.index[i])
                    in_position = False
            if in_position:
                exit_price = data['Close'].iloc[-1]
                profits.append(_safe_division(exit_price, entry_price))

        chart_info = None
        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[Bollinger] Total return: {total_return:.2%}")
            chart_info = plot_bollinger(data, buy_dates, sell_dates, stock_display)
        else:
            logging.info("[Bollinger] No trades")
            if not _suppress_chart[0]:
                popup.after(0, lambda: messagebox.showerror("데이터 없음", "[볼린저 밴드]를 확인할 수 없습니다. 기간을 더 늘려주세요."))

        return buy_dates, sell_dates, profits, chart_info

    def _run_ma_cross(data, close_prices):
        short_window = config.config["current"]["ma_cross"]["short"]
        long_window = config.config["current"]["ma_cross"]["long"]

        short_ma = data['Close'].rolling(window=short_window).mean()
        long_ma = data['Close'].rolling(window=long_window).mean()

        data['Short_MA'] = short_ma
        data['Long_MA'] = long_ma

        buy_signal = short_ma > long_ma
        sell_signal = short_ma < long_ma

        in_position = False
        entry_price = 0
        profits = []
        buy_dates = []
        sell_dates = []

        for i in range(len(data)):
            if not in_position and buy_signal.iloc[i]:
                in_position = True
                entry_price = data['Close'].iloc[i]
                buy_dates.append(data.index[i])
            elif in_position and sell_signal.iloc[i]:
                exit_price = data['Close'].iloc[i]
                profits.append(_safe_division(exit_price, entry_price))
                sell_dates.append(data.index[i])
                in_position = False

        if in_position:
            exit_price = data['Close'].iloc[-1]
            profits.append(_safe_division(exit_price, entry_price))

        chart_info = None
        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[MA Cross] Total return: {total_return:.2%}")
            chart_info = plot_ma_cross(data, buy_dates, sell_dates, stock_display)
        else:
            if not _suppress_chart[0]:
                popup.after(0, lambda: messagebox.showerror("데이터 없음", "[이동평균 교차]를 확인할 수 없습니다. 기간을 더 늘려주세요."))

        return buy_dates, sell_dates, profits, chart_info

    def _run_momentum_signal(data, close_prices):
        macd_short_span = config.config['current']['macd']['short']
        macd_long_span = config.config['current']['macd']['long']
        macd_signal_span = config.config['current']['macd']['signal']
        rsi_period = config.config['current']['rsi']['period']
        bb_period = config.config['current']['bollinger']['period']
        bb_num_std = config.config['current']['bollinger']['std_dev_multiplier']
        use_rebound = config.config['current']['bollinger']['use_rebound']
        rsi_lower = config.config['current']['rsi']['lower']
        rsi_upper = config.config['current']['rsi']['upper']

        # Phase 7-3: Calculate indicators once
        rsi = calculate_rsi_for_backtest(data['Close'], period=rsi_period)
        rolling_mean = data['Close'].rolling(window=bb_period).mean()
        rolling_std = data['Close'].rolling(window=bb_period).std()
        upper_band = rolling_mean + (rolling_std * bb_num_std)
        lower_band = rolling_mean - (rolling_std * bb_num_std)

        ema_short = data['Close'].ewm(span=macd_short_span, adjust=False).mean()
        ema_long = data['Close'].ewm(span=macd_long_span, adjust=False).mean()
        macd = ema_short - ema_long
        signal = macd.ewm(span=macd_signal_span, adjust=False).mean()

        short_ma = data['Close'].rolling(window=config.config['current']['ma_cross']['short']).mean()
        long_ma = data['Close'].rolling(window=config.config['current']['ma_cross']['long']).mean()

        # Phase 7-3: Use numpy for signal generation
        macd_signal_arr = np.where(macd > signal, "BUY", "SELL")
        ma_signal_arr = np.where(short_ma > long_ma, "BUY", "SELL")

        # Bollinger signal
        close_arr = data['Close'].values
        lower_arr = lower_band.values
        upper_arr = upper_band.values
        bb_signal_arr = np.full(len(data), "HOLD", dtype=object)

        if use_rebound:
            for i in range(len(data) - 1):
                if close_arr[i] < lower_arr[i]:
                    if close_arr[i + 1] > close_arr[i]:
                        bb_signal_arr[i] = "BUY"
                elif close_arr[i] > upper_arr[i]:
                    if close_arr[i + 1] < close_arr[i]:
                        bb_signal_arr[i] = "SELL"
        else:
            bb_signal_arr = np.where(close_arr < lower_arr, "BUY",
                                     np.where(close_arr > upper_arr, "SELL", "HOLD"))

        rsi_signal_arr = np.where(rsi < rsi_lower, "BUY",
                                   np.where(rsi > rsi_upper, "SELL", "HOLD"))

        # Combined signal using numpy scoring
        from market_trend_manager import MACD_WEIGHT, MA_WEIGHT, BB_WEIGHT, RSI_WEIGHT
        from market_trend_manager import STRONG_BUY_THRESHOLD, BUY_THRESHOLD, SELL_THRESHOLD, STRONG_SELL_THRESHOLD

        scores = np.zeros(len(data))
        scores += np.where(macd_signal_arr == "BUY", MACD_WEIGHT, np.where(macd_signal_arr == "SELL", -MACD_WEIGHT, 0))
        scores += np.where(ma_signal_arr == "BUY", MA_WEIGHT, np.where(ma_signal_arr == "SELL", -MA_WEIGHT, 0))
        scores += np.where(bb_signal_arr == "BUY", BB_WEIGHT, np.where(bb_signal_arr == "SELL", -BB_WEIGHT, 0))
        scores += np.where(rsi_signal_arr == "BUY", RSI_WEIGHT, np.where(rsi_signal_arr == "SELL", -RSI_WEIGHT, 0))

        combined = np.where(scores >= STRONG_BUY_THRESHOLD, "STRONG BUY",
                   np.where(scores >= BUY_THRESHOLD, "BUY",
                   np.where(scores <= STRONG_SELL_THRESHOLD, "STRONG SELL",
                   np.where(scores <= SELL_THRESHOLD, "SELL", "HOLD"))))

        in_position = False
        entry_price = 0
        profits = []
        buy_dates = []
        sell_dates = []

        for i in range(len(data)):
            sig = combined[i]
            if not in_position and sig in ("BUY", "STRONG BUY"):
                in_position = True
                entry_price = data['Close'].iloc[i]
                buy_dates.append(data.index[i])
            elif in_position and sig in ("SELL", "STRONG SELL"):
                exit_price = data['Close'].iloc[i]
                profits.append(_safe_division(exit_price, entry_price))
                sell_dates.append(data.index[i])
                in_position = False

        if in_position:
            exit_price = data['Close'].iloc[-1]
            profits.append(_safe_division(exit_price, entry_price))

        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[Momentum] Total return: {total_return:.2%}")
        else:
            logging.info("[Momentum] No trades")

        chart_info = plot_momentum_with_indicators(data, short_ma, long_ma, upper_band, lower_band, buy_dates, sell_dates,
                                                    rsi, macd, signal, stock_display)
        return buy_dates, sell_dates, profits, chart_info

    def _run_momentum_return_ma(data, close_prices):
        short_window = config.config['current']['ma_cross']['short']
        long_window = config.config['current']['ma_cross']['long']
        return_window = config.config['current']['momentum_return']['return_window']
        return_threshold = config.config['current']['momentum_return']['threshold']

        data['Short_MA'] = data['Close'].rolling(window=short_window).mean()
        data['Long_MA'] = data['Close'].rolling(window=long_window).mean()
        data['Return'] = data['Close'] / data['Close'].shift(return_window) - 1

        buy_dates = []
        sell_dates = []
        in_position = False
        entry_price = 0
        profits = []

        for i in range(return_window, len(data)):
            ret = data['Return'].iloc[i]
            s_ma = data['Short_MA'].iloc[i]
            l_ma = data['Long_MA'].iloc[i]

            if not in_position and ret >= return_threshold and s_ma > l_ma:
                in_position = True
                entry_price = data['Close'].iloc[i]
                buy_dates.append(data.index[i])
            elif in_position and (s_ma < l_ma or ret < 0):
                exit_price = data['Close'].iloc[i]
                profits.append(_safe_division(exit_price, entry_price))
                sell_dates.append(data.index[i])
                in_position = False

        if in_position:
            exit_price = data['Close'].iloc[-1]
            profits.append(_safe_division(exit_price, entry_price))

        chart_info = None
        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[Momentum Return + MA] Total return: {total_return:.2%}")
            chart_info = plot_ma_cross(data, buy_dates, sell_dates, stock_display, chart_key="momentum_return_ma")
        else:
            if not _suppress_chart[0]:
                popup.after(0, lambda: messagebox.showerror("데이터 없음", "[모멘텀 수익률 + MA 교차] 거래 없음"))

        return buy_dates, sell_dates, profits, chart_info

    def plot_ichimoku(data, buy_dates, sell_dates, ticker_name):
        """Ichimoku Cloud 차트 렌더링."""
        fig = Figure(figsize=(12, 6)); ax = fig.add_subplot(111)
        ax.plot(data.index, data['Close'], label='종가', color='black', linewidth=1)
        ax.plot(data.index, data['Tenkan'], label='전환선(9)', color='blue', linewidth=0.8, linestyle='--')
        ax.plot(data.index, data['Kijun'], label='기준선(26)', color='red', linewidth=0.8, linestyle='--')

        # Cloud shading
        if 'Senkou_A' in data.columns and 'Senkou_B' in data.columns:
            sa = data['Senkou_A']
            sb = data['Senkou_B']
            ax.fill_between(data.index, sa, sb,
                            where=(sa >= sb), color='green', alpha=0.15, label='양운')
            ax.fill_between(data.index, sa, sb,
                            where=(sa < sb), color='red', alpha=0.15, label='음운')

        buy_prices = [data['Close'].get(d, None) for d in buy_dates if d in data.index]
        sell_prices = [data['Close'].get(d, None) for d in sell_dates if d in data.index]
        valid_buys = [d for d in buy_dates if d in data.index]
        valid_sells = [d for d in sell_dates if d in data.index]
        ax.scatter(valid_buys, buy_prices, marker='^', color='green', s=100, zorder=5, label='매수')
        ax.scatter(valid_sells, sell_prices, marker='v', color='red', s=100, zorder=5, label='매도')

        ax.set_title(f"{ticker_name} 일목균형표 백테스트", fontsize=12, fontweight='bold')
        ax.legend(fontsize=8, loc='upper left')
        ax.grid(alpha=0.3)
        fig.tight_layout()
        return fig, f"{ticker_name} 일목균형표 백테스트", CHART_HELP.get("ichimoku", "")

    def _run_ichimoku(data, close_prices):
        """일목균형표 전략: 전환선/기준선 교차 + 구름 돌파."""
        from stock_score import calculate_ichimoku as _calc_ichimoku
        ichimoku = _calc_ichimoku(data, tenkan=9, kijun=26, senkou_b=52)
        if ichimoku is None:
            if not _suppress_chart[0]:
                popup.after(0, lambda: messagebox.showerror("데이터 없음", "일목균형표 계산에 충분한 데이터가 없습니다.\n기간을 늘려주세요."))
            return [], [], [], None

        data['Tenkan'] = ichimoku['tenkan_sen']
        data['Kijun'] = ichimoku['kijun_sen']
        data['Senkou_A'] = ichimoku['senkou_a']
        data['Senkou_B'] = ichimoku['senkou_b']

        buy_dates = []
        sell_dates = []
        in_position = False
        entry_price = 0
        profits = []

        for i in range(1, len(data)):
            tenkan_now = data['Tenkan'].iloc[i]
            kijun_now = data['Kijun'].iloc[i]
            tenkan_prev = data['Tenkan'].iloc[i - 1]
            kijun_prev = data['Kijun'].iloc[i - 1]
            close_now = data['Close'].iloc[i]

            if pd.isna(tenkan_now) or pd.isna(kijun_now):
                continue

            # Cloud boundaries at current position
            sa = data['Senkou_A'].iloc[i] if not pd.isna(data['Senkou_A'].iloc[i]) else 0
            sb = data['Senkou_B'].iloc[i] if not pd.isna(data['Senkou_B'].iloc[i]) else 0
            cloud_top = max(sa, sb)

            if not in_position:
                # Buy: Tenkan crosses above Kijun AND price above cloud
                if tenkan_prev <= kijun_prev and tenkan_now > kijun_now and close_now > cloud_top:
                    in_position = True
                    entry_price = close_now
                    buy_dates.append(data.index[i])
            else:
                # Sell: Tenkan crosses below Kijun
                if tenkan_prev >= kijun_prev and tenkan_now < kijun_now:
                    exit_price = close_now
                    profits.append(_safe_division(exit_price, entry_price))
                    sell_dates.append(data.index[i])
                    in_position = False

        if in_position:
            exit_price = data['Close'].iloc[-1]
            profits.append(_safe_division(exit_price, entry_price))

        chart_info = None
        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[Ichimoku] Total return: {total_return:.2%}")
            chart_info = plot_ichimoku(data, buy_dates, sell_dates, stock_display)
        else:
            if not _suppress_chart[0]:
                popup.after(0, lambda: messagebox.showerror("데이터 없음", "[일목균형표] 거래 없음. 기간을 늘려주세요."))

        return buy_dates, sell_dates, profits, chart_info

    # Phase 8-1: Strategy dispatch dictionary
    strategy_dispatch = {
        "macd": _run_macd,
        "rsi": _run_rsi,
        "macd_rsi": _run_macd_rsi,
        "bollinger": _run_bollinger,
        "ma_cross": _run_ma_cross,
        "momentum_signal": _run_momentum_signal,
        "momentum_return_ma": _run_momentum_return_ma,
        "ichimoku": _run_ichimoku,
    }

    def _apply_stoploss(data, buy_dates, sell_dates, profits, stoploss_pct):
        """기존 전략 결과에 손절 로직을 후처리로 적용합니다."""
        if stoploss_pct is None or not buy_dates:
            return buy_dates, sell_dates, profits

        close = data['Close']
        new_buy_dates = []
        new_sell_dates = []
        new_profits = []

        for i, bd in enumerate(buy_dates):
            if bd not in close.index:
                continue
            entry_price = close.loc[bd]
            stoploss_price = entry_price * (1 - stoploss_pct)

            # 해당 매수일 이후 데이터
            if i < len(sell_dates):
                sd = sell_dates[i]
                # 매수~매도 사이에서 손절가 도달 여부 확인
                mask = (close.index > bd) & (close.index <= sd)
                segment = close[mask]
                hit = segment[segment <= stoploss_price]
                if not hit.empty:
                    # 손절가에 먼저 도달
                    sl_date = hit.index[0]
                    sl_price = close.loc[sl_date]
                    new_buy_dates.append(bd)
                    new_sell_dates.append(sl_date)
                    new_profits.append(_safe_division(sl_price, entry_price))
                else:
                    # 손절 안 됨 → 원래 매도 유지
                    new_buy_dates.append(bd)
                    new_sell_dates.append(sd)
                    new_profits.append(profits[i] if i < len(profits) else 0.0)
            else:
                # 아직 매도 안 된 마지막 포지션
                mask = close.index > bd
                segment = close[mask]
                hit = segment[segment <= stoploss_price]
                if not hit.empty:
                    sl_date = hit.index[0]
                    sl_price = close.loc[sl_date]
                    new_buy_dates.append(bd)
                    new_sell_dates.append(sl_date)
                    new_profits.append(_safe_division(sl_price, entry_price))
                else:
                    new_buy_dates.append(bd)
                    if i < len(profits):
                        new_profits.append(profits[i])

        return new_buy_dates, new_sell_dates, new_profits

    def _apply_trailing_stop(data, buy_dates, sell_dates, profits, trail_type, trail_param):
        """추적 손절 후처리.
        trail_type: 'pct' (퍼센트) 또는 'atr' (ATR 기반)
        trail_param: 퍼센트 값 또는 ATR 배수
        """
        if not buy_dates:
            return buy_dates, sell_dates, profits

        close = data['Close']

        # ATR 계산 (ATR 방식일 때)
        atr_series = None
        if trail_type == 'atr':
            from stock_score import calculate_atr
            atr_series = calculate_atr(data, period=14)

        new_buy_dates = []
        new_sell_dates = []
        new_profits = []

        for i, bd in enumerate(buy_dates):
            if bd not in close.index:
                continue
            entry_price = close.loc[bd]

            # 매도일 결정 (기존 매도일까지 또는 데이터 끝)
            if i < len(sell_dates):
                end_date = sell_dates[i]
                mask = (close.index >= bd) & (close.index <= end_date)
            else:
                mask = close.index >= bd
            segment = close[mask]

            if segment.empty:
                continue

            # 추적 손절 시뮬레이션
            peak_price = entry_price
            trail_exit = False

            for j in range(1, len(segment)):
                current = segment.iloc[j]
                if current > peak_price:
                    peak_price = current

                if trail_type == 'pct':
                    stop_price = peak_price * (1 - trail_param / 100.0)
                elif trail_type == 'atr' and atr_series is not None:
                    idx = segment.index[j]
                    if idx in atr_series.index and not pd.isna(atr_series.loc[idx]):
                        stop_price = peak_price - (atr_series.loc[idx] * trail_param)
                    else:
                        continue
                else:
                    continue

                if current <= stop_price:
                    # 추적 손절 발동
                    new_buy_dates.append(bd)
                    new_sell_dates.append(segment.index[j])
                    new_profits.append(_safe_division(current, entry_price))
                    trail_exit = True
                    break

            if not trail_exit:
                # 추적 손절 미발동 → 원래 매도 유지
                new_buy_dates.append(bd)
                if i < len(sell_dates):
                    new_sell_dates.append(sell_dates[i])
                    new_profits.append(profits[i] if i < len(profits) else 0.0)
                elif i < len(profits):
                    new_profits.append(profits[i])

        return new_buy_dates, new_sell_dates, new_profits

    def _calculate_position_sizes(profits, data, method='full', risk_pct=2.0, atr_mult=2.0):
        """포지션 사이즈 계산.
        method: 'full' (100%), 'kelly' (켈리 공식), 'atr' (ATR 기반), 'fixed' (고정 비율)
        Returns: 포지션 사이즈 리스트 (0~1 범위)
        """
        n = len(profits)
        if method == 'full':
            return [1.0] * n

        if method == 'kelly':
            # 켈리 공식: f* = (W*R - L) / R
            # W = 승률, R = 평균수익/평균손실, L = 패률
            p_series = pd.Series(profits)
            wins = p_series[p_series > 0]
            losses = p_series[p_series < 0]
            if len(wins) == 0 or len(losses) == 0:
                return [1.0] * n
            win_rate = len(wins) / len(p_series)
            avg_win = wins.mean()
            avg_loss = abs(losses.mean())
            if avg_loss == 0:
                return [1.0] * n
            ratio = avg_win / avg_loss
            kelly = win_rate - (1 - win_rate) / ratio
            kelly = max(0.05, min(1.0, kelly))  # 5%~100% 범위 제한
            return [kelly] * n

        if method == 'atr':
            from stock_score import calculate_atr
            atr = calculate_atr(data, period=14)
            sizes = []
            close = data['Close']
            for i in range(n):
                try:
                    last_atr = atr.iloc[-1]
                    last_close = close.iloc[-1]
                    if last_atr > 0 and last_close > 0:
                        risk_amount = last_close * (risk_pct / 100.0)
                        size = risk_amount / (last_atr * atr_mult)
                        sizes.append(max(0.05, min(1.0, size)))
                    else:
                        sizes.append(1.0)
                except Exception:
                    sizes.append(1.0)
            return sizes

        if method == 'fixed':
            fixed_size = risk_pct / 100.0
            return [max(0.05, min(1.0, fixed_size))] * n

        return [1.0] * n

    def _compute_regime_mask(start_str, end_str):
        """SPY 기반 시장 레짐 마스크를 계산. True=상승장/횡보, False=하락장."""
        try:
            spy = yf.download("SPY", start=start_str, end=end_str)
            if isinstance(spy.columns, pd.MultiIndex):
                spy.columns = spy.columns.get_level_values(0)
            if spy.empty:
                return None
            ma20 = spy['Close'].rolling(20).mean()
            ma60 = spy['Close'].rolling(60).mean()
            # True = 상승/횡보(매수 허용), False = 하락(매수 억제)
            regime = ma20 >= ma60 * 0.99
            return regime
        except Exception as e:
            logging.warning(f"[REGIME] SPY 데이터 실패: {e}")
            return None

    def _apply_regime_filter(data, buy_dates, sell_dates, profits, regime_mask):
        """하락장 구간의 매수 시그널을 제거."""
        if regime_mask is None:
            return buy_dates, sell_dates, profits

        new_buy = []
        new_sell = []
        new_profits = []
        for i, bd in enumerate(buy_dates):
            # 매수일이 레짐 마스크에서 상승/횡보인 경우만 유지
            try:
                nearest = regime_mask.index.get_indexer([bd], method='nearest')[0]
                if nearest >= 0 and nearest < len(regime_mask) and regime_mask.iloc[nearest]:
                    new_buy.append(bd)
                    if i < len(sell_dates):
                        new_sell.append(sell_dates[i])
                    if i < len(profits):
                        new_profits.append(profits[i])
            except Exception:
                new_buy.append(bd)
                if i < len(sell_dates):
                    new_sell.append(sell_dates[i])
                if i < len(profits):
                    new_profits.append(profits[i])

        return new_buy, new_sell, new_profits

    def _run_walk_forward(data, close_prices, method, stoploss, trailing, pos_sizing,
                          train_ratio=0.7):
        """워크포워드 테스트: 데이터를 70/30으로 분할하여 인샘플/아웃오브샘플 성과 비교."""
        total_len = len(data)
        split_idx = int(total_len * train_ratio)
        split_date = data.index[split_idx]

        # 전체 데이터에 대해 전략 실행
        handler = strategy_dispatch.get(method)
        if not handler:
            return

        _wf_result = handler(data, close_prices)
        buy_dates, sell_dates, profits = _wf_result[0], _wf_result[1], _wf_result[2]

        # 후처리 적용
        if stoploss is not None and profits:
            buy_dates, sell_dates, profits = _apply_stoploss(
                data, buy_dates, sell_dates, profits, stoploss)
        if trailing is not None and profits:
            buy_dates, sell_dates, profits = _apply_trailing_stop(
                data, buy_dates, sell_dates, profits, trailing[0], trailing[1])

        if not profits:
            messagebox.showinfo("알림", "워크포워드: 거래가 발생하지 않았습니다.")
            return

        # 분할: 매수일 기준으로 인샘플/아웃오브샘플 분리
        in_buy, in_sell, in_profits = [], [], []
        out_buy, out_sell, out_profits = [], [], []

        for i, bd in enumerate(buy_dates):
            if bd < split_date:
                in_buy.append(bd)
                if i < len(sell_dates):
                    in_sell.append(sell_dates[i])
                if i < len(profits):
                    in_profits.append(profits[i])
            else:
                out_buy.append(bd)
                if i < len(sell_dates):
                    out_sell.append(sell_dates[i])
                if i < len(profits):
                    out_profits.append(profits[i])

        # 워크포워드 결과 표시
        wf_frame = tk.LabelFrame(result_container, text="워크포워드 분석", font=("Arial", 10, "bold"))
        wf_frame.pack(fill=tk.X, padx=10, pady=5)

        def _wf_stats(profs, label):
            if not profs:
                return f"  {label}: 거래 없음"
            p_series = pd.Series(profs)
            total_ret = (1 + p_series).prod() - 1
            wins = p_series[p_series > 0]
            win_rate = len(wins) / len(p_series) if len(p_series) > 0 else 0
            if len(p_series) >= 2 and p_series.std() > 0:
                sharpe_val = p_series.mean() / p_series.std() * np.sqrt(252)
            else:
                sharpe_val = 0
            return (f"  {label}: 수익률 {total_ret:.2%} | "
                    f"거래 {len(profs)}회 | 승률 {win_rate:.1%} | 샤프 {sharpe_val:.2f}")

        in_text = _wf_stats(in_profits, f"인샘플 (~{split_date.strftime('%Y-%m-%d')})")
        out_text = _wf_stats(out_profits, f"아웃오브샘플 ({split_date.strftime('%Y-%m-%d')}~)")

        tk.Label(wf_frame, text=in_text, font=("Arial", 9), anchor="w").pack(fill=tk.X, padx=8, pady=1)
        tk.Label(wf_frame, text=out_text, font=("Arial", 9), anchor="w").pack(fill=tk.X, padx=8, pady=1)

        # 차트에 분할 경계선 표시
        split_label = tk.Label(wf_frame,
                               text=f"분할 기준일: {split_date.strftime('%Y-%m-%d')} (학습 {train_ratio:.0%} / 검증 {1-train_ratio:.0%})",
                               font=("Arial", 9, "bold"), fg="#8B5CF6")
        split_label.pack(padx=8, pady=2)

        return buy_dates, sell_dates, profits

    def run_backtest(ticker_sym, value, unit, method, stoploss=None, use_regime=False,
                     trailing=None, pos_sizing='full', use_walk_forward=False,
                     start_date=None, end_date=None):
        if start_date and end_date:
            # 절대 날짜 모드
            start_str = start_date
            end_str = end_date
        else:
            now = datetime.now()
            if unit == 'd':
                start = now - timedelta(days=value)
            elif unit == 'mo':
                start = now - timedelta(days=value * 30)
            elif unit == 'y':
                start = now - timedelta(days=value * 365)
            else:
                start = now
            start_str = start.strftime('%Y-%m-%d')
            end_str = now.strftime('%Y-%m-%d')

        # Phase 3-5: Exception handling for yf.download
        try:
            data = _retry_download(ticker_sym, start_str, end_str)
        except (ConnectionError, TimeoutError, OSError) as e:
            popup.after(0, lambda e=e: messagebox.showerror("네트워크 오류",
                                 f"데이터를 가져올 수 없습니다.\n네트워크 연결을 확인하세요.\n\n{e}"))
            return
        except Exception as e:
            popup.after(0, lambda e=e: messagebox.showerror("다운로드 오류", f"데이터 다운로드 실패: {e}"))
            return

        # Phase 3-7: Robust MultiIndex handling
        if isinstance(data.columns, pd.MultiIndex):
            try:
                data.columns = data.columns.get_level_values(0)
            except Exception:
                data.columns = ['_'.join(str(c) for c in col).strip('_') for col in data.columns]
        if isinstance(data.index, pd.MultiIndex):
            data = data.droplevel(0, axis=0)

        if data.empty:
            # Phase 11-7: Specific error messages
            popup.after(0, lambda: messagebox.showerror("데이터 없음",
                                 f"{ticker_sym}에 대한 데이터를 가져올 수 없습니다.\n"
                                 "티커가 올바른지 확인하세요."))
            return

        close_prices = data['Close']

        # 레짐 필터 마스크 사전 계산
        regime_mask = None
        if use_regime:
            regime_mask = _compute_regime_mask(start_str, end_str)

        # Phase 8-1: Dispatch

        # 워크포워드 모드
        if use_walk_forward:
            train_ratio = config.config["backtest"].get("walk_forward_train_ratio", 0.7)
            def _wf_ui():
                _clear_result_area()
                result = _run_walk_forward(data, close_prices, method, stoploss, trailing,
                                           pos_sizing, train_ratio)
                if result:
                    buy_dates, sell_dates, profits = result
                    if profits:
                        _show_result_summary(profits, buy_dates, sell_dates, close_prices)
            popup.after(0, _wf_ui)
            return

        handler = strategy_dispatch.get(method)
        if handler:
            result = handler(data, close_prices)
            buy_dates, sell_dates, profits = result[0], result[1], result[2]
            chart_info = result[3] if len(result) > 3 else None
            # 레짐 필터 적용
            if use_regime and profits:
                buy_dates, sell_dates, profits = _apply_regime_filter(
                    data, buy_dates, sell_dates, profits, regime_mask)
            # 손절 후처리 적용
            if stoploss is not None and profits:
                buy_dates, sell_dates, profits = _apply_stoploss(
                    data, buy_dates, sell_dates, profits, stoploss)
            # 추적 손절 적용
            if trailing is not None and profits:
                buy_dates, sell_dates, profits = _apply_trailing_stop(
                    data, buy_dates, sell_dates, profits, trailing[0], trailing[1])
            # 포지션 사이징 적용
            if pos_sizing != 'full' and profits:
                risk_pct = config.config["backtest"].get("risk_per_trade", 2.0)
                atr_mult = config.config["backtest"].get("atr_sizing_multiplier", 2.0)
                sizes = _calculate_position_sizes(profits, data, method=pos_sizing,
                                                   risk_pct=risk_pct, atr_mult=atr_mult)
                adjusted_profits = [p * s for p, s in zip(profits, sizes)]
                profits = adjusted_profits

            # UI 업데이트를 메인 스레드로 위임
            def _update_ui(profits=profits, buy_dates=buy_dates, sell_dates=sell_dates,
                           close_prices=close_prices, stoploss=stoploss, trailing=trailing,
                           use_regime=use_regime, pos_sizing=pos_sizing, chart_info=chart_info):
                _clear_result_area()
                # 차트를 result_container에 임베딩
                if chart_info and not _suppress_chart[0]:
                    fig, title, help_text = chart_info
                    _create_graph_popup(fig, title, help_text)
                if profits:
                    _show_result_summary(profits, buy_dates, sell_dates, close_prices)
                    # 적용된 필터 표시
                    filter_texts = []
                    if stoploss is not None:
                        filter_texts.append(f"손절: -{stoploss*100:.1f}%")
                    if trailing is not None:
                        t_type = "%" if trailing[0] == 'pct' else "ATR"
                        filter_texts.append(f"추적손절: {trailing[1]}{t_type}")
                    if use_regime:
                        filter_texts.append("레짐 필터 (SPY)")
                    if pos_sizing != 'full':
                        sizing_names = {'kelly': '켈리', 'atr': 'ATR', 'fixed': '고정비율'}
                        filter_texts.append(f"포지션: {sizing_names.get(pos_sizing, pos_sizing)}")
                    if filter_texts:
                        filter_label = tk.Label(result_container,
                                                text="적용: " + " | ".join(filter_texts),
                                                font=("Arial", 9, "bold"), fg="#4A90D9")
                        filter_label.pack(padx=10, anchor="w")
                    # 보유 종목이면 보유 vs 전략 비교 표시
                    _show_holdings_comparison(profits, buy_dates, sell_dates, close_prices)
                    # 몬테카를로 시뮬레이션
                    _show_monte_carlo(profits, close_prices)
            popup.after(0, _update_ui)
        else:
            popup.after(0, lambda: messagebox.showinfo("알림", f"{method} 전략은 아직 구현되지 않았습니다."))

    # --- Popup UI ---
    popup = tk.Toplevel()
    popup.title(f"{stock} 백테스트")
    popup.state('zoomed')
    popup.minsize(400, 450)

    # Phase 4-1: Cleanup figures on close
    popup.protocol("WM_DELETE_WINDOW", lambda: (cleanup_figures(), popup.destroy()))

    # ── 스크롤 가능한 메인 컨테이너 ──
    _scroll_outer = tk.Frame(popup)
    _scroll_outer.pack(fill=tk.BOTH, expand=True)

    _scroll_canvas = tk.Canvas(_scroll_outer, highlightthickness=0)
    _scroll_vsb = ttk.Scrollbar(_scroll_outer, orient="vertical", command=_scroll_canvas.yview)
    _scroll_inner = tk.Frame(_scroll_canvas)

    _scroll_inner.bind("<Configure>", lambda e: _scroll_canvas.configure(scrollregion=_scroll_canvas.bbox("all")))
    _scroll_canvas.create_window((0, 0), window=_scroll_inner, anchor="nw")
    _scroll_canvas.configure(yscrollcommand=_scroll_vsb.set)

    _scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    _scroll_vsb.pack(side=tk.RIGHT, fill=tk.Y)

    # 캔버스 너비에 맞게 inner 프레임 리사이즈
    def _on_canvas_configure(e):
        _scroll_canvas.itemconfig(_scroll_canvas.find_withtag("all")[0], width=e.width)
    _scroll_canvas.bind("<Configure>", _on_canvas_configure)

    # 마우스 휠 스크롤 (bind_all 대신 개별 위젯 바인딩으로 충돌 방지)
    def _on_popup_wheel(e):
        _scroll_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _bind_popup_wheel(e):
        popup.bind_all("<MouseWheel>", _on_popup_wheel)

    def _unbind_popup_wheel(e):
        popup.unbind_all("<MouseWheel>")

    _scroll_canvas.bind("<Enter>", _bind_popup_wheel)
    _scroll_canvas.bind("<Leave>", _unbind_popup_wheel)
    popup.bind("<Destroy>", lambda e: _unbind_popup_wheel(e) if e.widget == popup else None)

    # 결과 표시 전용 프레임 (매 실행 시 내용 교체)
    result_container = tk.Frame(_scroll_inner)
    result_container.pack(fill=tk.X, side=tk.BOTTOM)

    def _clear_result_area():
        for widget in result_container.winfo_children():
            widget.destroy()

    def _save_fig_png(fig):
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG files", "*.png"), ("All files", "*.*")],
            initialfile=f"{ticker_symbol}_backtest.png"
        )
        if path:
            fig.savefig(path, dpi=150, bbox_inches='tight')
            messagebox.showinfo("저장 완료", f"그래프가 저장되었습니다:\n{path}")

    # ── 핵심 지표 섹션 ──
    indicator_frame = tk.LabelFrame(_scroll_inner, text="핵심 지표", font=("Arial", 10, "bold"))
    indicator_frame.pack(fill=tk.X, padx=10, pady=(5, 0))
    indicator_loading = tk.Label(indicator_frame, text="지표 불러오는 중...", font=("Arial", 9), fg="#444444")
    indicator_loading.pack(pady=5)

    def _load_indicators():
        try:
            info = yf.Ticker(ticker_symbol).info
        except Exception:
            info = None

        def _update_ui():
            for w in indicator_frame.winfo_children():
                w.destroy()
            if not info:
                tk.Label(indicator_frame, text="지표 정보를 불러올 수 없습니다",
                         font=("Arial", 9), fg="#CC0000").pack(pady=5)
                return

            def _fmt(val, fmt=".2f", suffix="", prefix=""):
                if val is None:
                    return "N/A"
                try:
                    return f"{prefix}{val:{fmt}}{suffix}"
                except (ValueError, TypeError):
                    return "N/A"

            def _fmt_cap(val):
                if val is None:
                    return "N/A"
                if val >= 1e12:
                    return f"${val/1e12:.2f}T"
                if val >= 1e9:
                    return f"${val/1e9:.1f}B"
                if val >= 1e6:
                    return f"${val/1e6:.0f}M"
                return f"${val:,.0f}"

            # 공유 모듈에서 밸류에이션 계산
            fund = calculate_valuation_score(info)

            per = safe_get_float(info, "trailingPE")
            fwd_per = safe_get_float(info, "forwardPE")
            pbr = safe_get_float(info, "priceToBook")
            peg = safe_get_float(info, "pegRatio")
            eps_val = safe_get_float(info, "trailingEps")
            div_val = safe_get_float(info, "dividendYield")
            roe_val = safe_get_float(info, "returnOnEquity")
            om_val = safe_get_float(info, "operatingMargins")
            ev_ebitda = safe_get_float(info, "enterpriseToEbitda")
            debt_equity = safe_get_float(info, "debtToEquity")
            current_ratio = safe_get_float(info, "currentRatio")
            rev_growth = safe_get_float(info, "revenueGrowth")
            earn_growth = safe_get_float(info, "earningsGrowth")

            # PEG 직접 계산 fallback: PER ÷ (이익성장률 × 100)
            peg_calculated = False
            if peg is None and per is not None and earn_growth is not None:
                eg_pct = earn_growth * 100
                if eg_pct > 0:
                    peg = per / eg_pct
                    peg_calculated = True
            fcf = safe_get_float(info, "freeCashflow")
            current_price = safe_get_float(info, "currentPrice")
            hi = safe_get_float(info, "fiftyTwoWeekHigh")
            lo = safe_get_float(info, "fiftyTwoWeekLow")
            book_val = safe_get_float(info, "bookValue")

            # 지표별 툴팁 설명
            INDICATOR_TOOLTIPS = {
                "PER": "주가수익비율 (Price to Earnings Ratio)\n주가 ÷ 주당순이익(EPS).\n낮을수록 이익 대비 주가가 싸다는 뜻.\n일반적으로 15 이하면 저평가, 30 이상이면 고평가로 봅니다.\n업종마다 평균이 다르므로 같은 업종끼리 비교해야 합니다.",
                "Fwd PER": "선행 주가수익비율 (Forward PER)\n현재 주가 ÷ 향후 12개월 예상 EPS.\nPER은 과거 실적, Fwd PER은 미래 예상 실적 기준입니다.\n애널리스트 추정치 기반이므로 실제와 다를 수 있습니다.",
                "PBR": "주가순자산비율 (Price to Book Ratio)\n주가 ÷ 주당순자산(BPS).\n1 미만이면 회사 청산가치보다 싸게 거래된다는 뜻.\n1.5 이하면 저평가, 5 이상이면 고평가 기준을 적용합니다.\n기술주는 PBR이 높은 경우가 많습니다.",
                "PEG": "주가수익성장비율 (PEG Ratio)\nPER ÷ 이익성장률(%).\n성장성을 감안한 밸류에이션 지표입니다.\n1.0 이하면 성장 대비 저평가, 2.0 이상이면 고평가.\nPER이 높아도 성장률이 높으면 PEG는 낮을 수 있습니다.\n\nyfinance에서 제공되지 않는 경우\nPER ÷ (이익성장률×100)으로 직접 계산합니다.\n이 경우 '(추정)' 표시가 붙습니다.",
                "EPS": "주당순이익 (Earnings Per Share)\n순이익 ÷ 발행주식 수.\n회사가 주식 1주당 얼마를 벌었는지 나타냅니다.\nEPS가 꾸준히 증가하면 실적이 성장하고 있다는 신호입니다.",
                "시가총액": "시가총액 (Market Capitalization)\n현재 주가 × 발행주식 수.\n회사의 전체 시장 가치를 나타냅니다.\n대형주(Large Cap): $10B 이상\n중형주(Mid Cap): $2B~$10B\n소형주(Small Cap): $2B 미만",
                "배당": "배당수익률 (Dividend Yield)\n연간 배당금 ÷ 현재 주가 × 100.\n주가 대비 매년 받는 배당금 비율입니다.\n보통 2~4%면 양호한 배당주로 봅니다.\n0%면 배당을 하지 않는 성장주일 수 있습니다.",
                "FCF": "잉여현금흐름 (Free Cash Flow)\n영업활동 현금흐름 - 설비투자.\n회사가 실제로 자유롭게 쓸 수 있는 현금입니다.\n배당, 자사주 매입, 부채 상환 등에 활용됩니다.\nFCF가 꾸준히 양수이면 재무 건전성이 좋은 신호입니다.",
                "ROE": "자기자본이익률 (Return on Equity)\n순이익 ÷ 자기자본 × 100.\n주주가 투자한 돈으로 얼마나 효율적으로 벌었는지.\n15% 이상이면 우수, 5% 미만이면 부진으로 봅니다.\n워런 버핏이 중시하는 핵심 지표입니다.",
                "영업이익률": "영업이익률 (Operating Margin)\n영업이익 ÷ 매출 × 100.\n본업에서 얼마나 효율적으로 이익을 내는지.\n높을수록 원가 관리가 잘 되고 경쟁력이 있다는 뜻입니다.\n업종별 평균이 크게 다르므로 동종 업계와 비교하세요.",
                "부채비율": "부채비율 (Debt to Equity Ratio)\n총부채 ÷ 자기자본 × 100.\n100% 미만이면 자기자본이 부채보다 많아 안정적.\n200% 이상이면 부채 부담이 크다는 경고 신호입니다.\n금융업은 구조적으로 부채비율이 높습니다.",
                "유동비율": "유동비율 (Current Ratio)\n유동자산 ÷ 유동부채.\n1년 내 갚아야 할 빚을 감당할 능력입니다.\n1.0 이상이면 단기 채무 상환 능력 양호.\n2.0 이상이면 매우 안정적, 1.0 미만이면 유동성 위험.",
                "EV/EBITDA": "기업가치 대비 EBITDA\n(시가총액+순부채) ÷ (세전·이자·감가상각 전 이익).\nPER보다 기업의 실질 수익력을 잘 반영합니다.\n부채와 감가상각 영향을 제거해 기업 간 비교에 유리.\n10 이하면 저평가, 20 이상이면 고평가 기준을 적용합니다.",
                "매출성장률": "매출성장률 (Revenue Growth)\n전년 동기 대비 매출 증가율.\n회사의 사업이 얼마나 빠르게 성장하는지.\n양수면 매출이 늘고 있고, 음수면 줄고 있습니다.\n성장주는 보통 10% 이상의 매출성장률을 보입니다.",
                "이익성장률": "이익성장률 (Earnings Growth)\n전년 동기 대비 순이익 증가율.\n매출뿐 아니라 실제 이익이 늘고 있는지 확인합니다.\n이익성장률이 매출성장률보다 높으면\n수익성이 개선되고 있다는 긍정적 신호입니다.",
                "52주": "52주 최저/최고가\n최근 1년간 거래된 가격의 범위입니다.\n현재가가 52주 고점 근처면 강한 상승 추세이거나 고평가.\n52주 저점 근처면 약세이거나 저평가 매수 기회일 수 있습니다.\n고점 대비 20% 이상 하락 시 저평가 기준을 적용합니다.",
                "현재가": "현재 거래 가격 (Current Price)\n장중에는 실시간 가격, 장 마감 후에는 종가입니다.\n적정가격과 비교해 매수/매도 판단의 기준이 됩니다.",
            }

            def _make_indicator_label(parent, label, value, tooltip_key=None):
                """지표 라벨을 생성하고 툴팁을 붙입니다."""
                lbl = tk.Label(parent, text=f"{label}: {value}", font=("Arial", 9), cursor="question_arrow")
                lbl.pack(side=tk.LEFT, padx=(0, 16))
                key = tooltip_key or label
                if key in INDICATOR_TOOLTIPS:
                    HelpTooltip(lbl, INDICATOR_TOOLTIPS[key])
                return lbl

            # 섹터/산업/홈페이지 정보
            sector = info.get('sector', '')
            industry = info.get('industry', '')
            website = info.get('website', '')
            if sector or industry or website:
                info_row = tk.Frame(indicator_frame)
                info_row.pack(fill=tk.X, padx=10, pady=(5, 2))
                parts = []
                if sector:
                    parts.append(f"섹터: {sector}")
                if industry:
                    parts.append(f"산업: {industry}")
                if parts:
                    tk.Label(info_row, text=" | ".join(parts), font=("Arial", 9, "bold")).pack(side=tk.LEFT)
                if website:
                    link = tk.Label(info_row, text="홈페이지", font=("Arial", 9, "underline"), fg="blue", cursor="hand2")
                    link.pack(side=tk.LEFT, padx=(10, 0))
                    link.bind("<Button-1>", lambda e, url=website: webbrowser.open(url))

            # Row 1: PER, Fwd PER, PBR, PEG
            row1 = tk.Frame(indicator_frame)
            row1.pack(fill=tk.X, padx=8, pady=2)
            peg_display = _fmt(peg)
            if peg is not None and peg_calculated:
                peg_display += " (추정)"
            for label, value in [
                ("PER", _fmt(per)),
                ("Fwd PER", _fmt(fwd_per)),
                ("PBR", _fmt(pbr)),
                ("PEG", peg_display),
            ]:
                _make_indicator_label(row1, label, value)

            # Row 2: EPS, 시가총액, 배당, FCF
            row2 = tk.Frame(indicator_frame)
            row2.pack(fill=tk.X, padx=8, pady=2)
            for label, value in [
                ("EPS", _fmt(eps_val, prefix="$") if eps_val is not None else "N/A"),
                ("시가총액", _fmt_cap(info.get("marketCap"))),
                ("배당", _fmt(div_val * 100 if div_val else None, suffix="%")),
                ("FCF", _fmt_cap(fcf) if fcf is not None else "N/A"),
            ]:
                _make_indicator_label(row2, label, value)

            # Row 3: ROE, 영업이익률, 부채비율, 유동비율
            row3 = tk.Frame(indicator_frame)
            row3.pack(fill=tk.X, padx=8, pady=2)
            for label, value in [
                ("ROE", _fmt(roe_val * 100 if roe_val else None, suffix="%")),
                ("영업이익률", _fmt(om_val * 100 if om_val else None, suffix="%")),
                ("부채비율", _fmt(debt_equity, fmt=".1f", suffix="%") if debt_equity is not None else "N/A"),
                ("유동비율", _fmt(current_ratio)),
            ]:
                _make_indicator_label(row3, label, value)

            # Row 4: EV/EBITDA, 매출성장률, 이익성장률
            row4 = tk.Frame(indicator_frame)
            row4.pack(fill=tk.X, padx=8, pady=2)
            for label, value in [
                ("EV/EBITDA", _fmt(ev_ebitda)),
                ("매출성장률", _fmt(rev_growth * 100 if rev_growth is not None else None, fmt=".1f", suffix="%")),
                ("이익성장률", _fmt(earn_growth * 100 if earn_growth is not None else None, fmt=".1f", suffix="%")),
            ]:
                _make_indicator_label(row4, label, value)

            # Row 5: 52주, 현재가
            row5 = tk.Frame(indicator_frame)
            row5.pack(fill=tk.X, padx=8, pady=(2, 4))
            hi_s = f"${hi:.2f}" if hi else "N/A"
            lo_s = f"${lo:.2f}" if lo else "N/A"
            price_s = f"${current_price:.2f}" if current_price else "N/A"
            _make_indicator_label(row5, "52주", f"{lo_s} ~ {hi_s}")
            _make_indicator_label(row5, "현재가", price_s)

            # --- 공유 모듈 결과 사용 ---
            score = fund.score
            total_criteria = fund.total_criteria
            criteria = fund.criteria
            fair_price = fund.fair_price

            # --- 구분선 ---
            sep = ttk.Separator(indicator_frame, orient="horizontal")
            sep.pack(fill=tk.X, padx=8, pady=4)

            # --- Row 6: 종합 판단 + 적정가격 ---
            row6 = tk.Frame(indicator_frame)
            row6.pack(fill=tk.X, padx=8, pady=2)

            JUDGMENT_TOOLTIP = (
                "종합 판단 (점수제 밸류에이션)\n"
                "7가지 핵심 기준으로 저평가/고평가를 판단합니다.\n"
                "각 기준마다 저평가(+1), 고평가(-1), 중립(0)을 부여하고 합산합니다.\n\n"
                "판단 기준:\n"
                "  +3점 이상 → ★ 저평가 (매수 고려)\n"
                "  -3점 이하 → ★ 고평가 (매도 고려)\n"
                "  그 외 → 적정 (관망)\n\n"
                "주의: 단순 수치 기반 판단이므로\n"
                "업종 특성, 성장성, 시장 상황 등을\n"
                "종합적으로 고려하여 참고용으로 활용하세요."
            )

            FAIR_PRICE_TOOLTIP = (
                "적정 가격 (Fair Value Estimate)\n"
                "3가지 밸류에이션 모델의 평균값입니다.\n\n"
                "1) PER 기반: EPS × 업종평균PER\n"
                "   업종 PER을 구할 수 없으면 시장 평균 15를 사용합니다.\n"
                "   이익 기반으로 주가의 적정 수준을 추정합니다.\n\n"
                "2) PBR 기반: 주당순자산(BPS) × 1.0\n"
                "   회사의 청산가치를 기준으로 한 보수적 추정입니다.\n\n"
                "3) 간이 DCF: EPS × (1+이익성장률)^5 × 15 ÷ 1.1^5\n"
                "   향후 5년 이익 성장을 반영하고\n"
                "   할인율 10%로 현재가치를 구합니다.\n\n"
                "괴리율(%) = (적정가격 - 현재가) ÷ 현재가 × 100\n"
                "양수면 현재가가 적정가보다 싸다는 의미입니다.\n\n"
                "주의: 간이 추정치이므로 투자 판단의\n"
                "절대적 근거가 아닌 참고 자료로 활용하세요."
            )

            # 개별 판정 기준 툴팁
            CRITERIA_TOOLTIPS = {
                "PER": "PER 판정 기준\n  ✓ 저평가: PER ≤ 15 (이익 대비 주가가 싸다)\n  ✗ 고평가: PER ≥ 30 (이익 대비 주가가 비싸다)\n  △ 중립: 15 ~ 30 사이",
                "PBR": "PBR 판정 기준\n  ✓ 저평가: PBR ≤ 1.5 (순자산 대비 싸다)\n  ✗ 고평가: PBR ≥ 5.0 (순자산 대비 비싸다)\n  △ 중립: 1.5 ~ 5.0 사이",
                "PEG": "PEG 판정 기준\n  ✓ 저평가: PEG ≤ 1.0 (성장성 대비 싸다)\n  ✗ 고평가: PEG ≥ 2.0 (성장성 대비 비싸다)\n  △ 중립: 1.0 ~ 2.0 사이",
                "EV/EBITDA": "EV/EBITDA 판정 기준\n  ✓ 저평가: ≤ 10 (기업가치 대비 수익력 우수)\n  ✗ 고평가: ≥ 20 (기업가치 대비 수익력 부족)\n  △ 중립: 10 ~ 20 사이",
                "ROE": "ROE 판정 기준\n  ✓ 저평가: ROE ≥ 15% (자기자본 활용 우수)\n  ✗ 고평가: ROE < 5% (자기자본 활용 부진)\n  △ 중립: 5% ~ 15% 사이",
                "부채": "부채비율 판정 기준\n  ✓ 저평가: 부채비율 < 100% (재무 안정적)\n  ✗ 고평가: 부채비율 ≥ 200% (부채 부담 과다)\n  △ 중립: 100% ~ 200% 사이",
                "52주": "52주 고점 대비 하락률 판정 기준\n  ✓ 저평가: 고점 대비 20% 이상 하락 (바겐세일 가능)\n  ✗ 고평가: 고점 대비 5% 이내 (이미 많이 올랐다)\n  △ 중립: 5% ~ 20% 하락 구간",
            }

            if total_criteria > 0:
                if score >= 3:
                    judgment = "저평가"
                    j_color = "#008800"
                elif score <= -3:
                    judgment = "고평가"
                    j_color = "#CC0000"
                else:
                    judgment = "적정"
                    j_color = "#666666"
                j_label = tk.Label(row6, text=f"종합 판단: ★ {judgment} (점수: {'+' if score > 0 else ''}{score}/{total_criteria})",
                         font=("Arial", 10, "bold"), fg=j_color, cursor="question_arrow")
                j_label.pack(side=tk.LEFT, padx=(0, 20))
                HelpTooltip(j_label, JUDGMENT_TOOLTIP)
            else:
                tk.Label(row6, text="종합 판단: 데이터 부족",
                         font=("Arial", 10, "bold"), fg="#999999").pack(side=tk.LEFT, padx=(0, 20))

            if fair_price is not None and current_price is not None and current_price > 0:
                gap = (fair_price - current_price) / current_price * 100
                gap_sign = "+" if gap > 0 else ""
                gap_color = "#008800" if gap > 0 else "#CC0000" if gap < -10 else "#666666"
                fp_label = tk.Label(row6, text=f"적정가격: ${fair_price:.2f} (현재가 대비 {gap_sign}{gap:.1f}%)",
                         font=("Arial", 10, "bold"), fg=gap_color, cursor="question_arrow")
                fp_label.pack(side=tk.LEFT)
                HelpTooltip(fp_label, FAIR_PRICE_TOOLTIP)
            elif fair_price is not None:
                fp_label = tk.Label(row6, text=f"적정가격: ${fair_price:.2f}",
                         font=("Arial", 10, "bold"), fg="#666666", cursor="question_arrow")
                fp_label.pack(side=tk.LEFT)
                HelpTooltip(fp_label, FAIR_PRICE_TOOLTIP)

            # --- Row 7: 개별 기준 판정 표시 ---
            row7 = tk.Frame(indicator_frame)
            row7.pack(fill=tk.X, padx=8, pady=(2, 4))
            for name, val in criteria.items():
                if val is None:
                    sym = "-"
                    color = "#999999"
                elif val == 1:
                    sym = "\u2713"  # ✓
                    color = "#008800"
                elif val == -1:
                    sym = "\u2717"  # ✗
                    color = "#CC0000"
                else:
                    sym = "\u25B3"  # △
                    color = "#666666"
                c_label = tk.Label(row7, text=f"{name}{sym}", font=("Arial", 9), fg=color, cursor="question_arrow")
                c_label.pack(side=tk.LEFT, padx=(0, 10))
                if name in CRITERIA_TOOLTIPS:
                    HelpTooltip(c_label, CRITERIA_TOOLTIPS[name])

            # --- Row 8: 복합 팩터 모델 + F-Score ---
            sep2 = ttk.Separator(indicator_frame, orient="horizontal")
            sep2.pack(fill=tk.X, padx=8, pady=4)

            row8 = tk.Frame(indicator_frame)
            row8.pack(fill=tk.X, padx=8, pady=(2, 4))

            # 팩터 모델
            factor = calculate_factor_score(info)
            factor_color = "#008800" if factor["total"] >= 7 else "#E74C3C" if factor["total"] <= 2 else "#666666"
            factor_lbl = tk.Label(row8,
                                  text=f"팩터 점수: {factor['total']}/9 {factor['grade']} "
                                       f"(V:{factor['value']} M:{factor['momentum']} Q:{factor['quality']})",
                                  font=("Arial", 9, "bold"), fg=factor_color, cursor="question_arrow")
            factor_lbl.pack(side=tk.LEFT, padx=(0, 16))
            HelpTooltip(factor_lbl,
                        "복합 팩터 모델 (밸류+모멘텀+퀄리티)\n"
                        "각 팩터 0~3점, 총합 0~9점.\n\n"
                        "V(밸류): PER≤15 +1, PBR≤1.5 +1, PEG≤1.0 +1\n"
                        "M(모멘텀): 강력매수=3, 매수=2, 관망=1, 매도=0\n"
                        "Q(퀄리티): ROE≥15% +1, 부채<100% +1, 영업이익률≥15% +1\n\n"
                        "A(7~9): 우수 | B(5~6): 양호 | C(3~4): 보통 | D(0~2): 부진")

            # Piotroski F-Score
            fscore = calculate_piotroski_fscore(info)
            fs_color = "#008800" if fscore["score"] >= 7 else "#E74C3C" if fscore["score"] <= 2 else "#666666"
            fs_lbl = tk.Label(row8,
                              text=f"F-Score: {fscore['score']}/{fscore['max_score']}",
                              font=("Arial", 9, "bold"), fg=fs_color, cursor="question_arrow")
            fs_lbl.pack(side=tk.LEFT, padx=(0, 10))

            # F-Score 세부 항목 툴팁
            detail_lines = []
            for item_name, item_val in fscore["details"].items():
                if item_val is None:
                    detail_lines.append(f"  {item_name}: N/A")
                elif item_val == 1:
                    detail_lines.append(f"  {item_name}: ✓ 통과")
                else:
                    detail_lines.append(f"  {item_name}: ✗ 미달")
            HelpTooltip(fs_lbl,
                        "Piotroski F-Score (재무 퀄리티 9항목)\n"
                        "각 항목 통과 시 1점, 총합 0~9점.\n\n"
                        + "\n".join(detail_lines) + "\n\n"
                        "7~9점: 재무 우량 | 4~6점: 보통 | 0~3점: 취약")

        try:
            popup.after(0, _update_ui)
        except tk.TclError:
            pass

    threading.Thread(target=_load_indicators, daemon=True).start()

    now = datetime.now()
    one_year_ago = now - timedelta(days=365)

    period_range_label = tk.Label(
        _scroll_inner,
        text=f"분석 기간: {one_year_ago.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')} (1년)",
        font=("Arial", 10),
        fg="#333333"
    )
    period_range_label.pack(pady=5)

    # --- 기간 모드 선택 (상대/절대) ---
    bt_period_mode_var = tk.StringVar(value=config.config["backtest"].get("period_mode", "relative"))

    period_mode_frame = tk.Frame(_scroll_inner)
    period_mode_frame.pack(pady=(5, 0))

    frame = tk.Frame(_scroll_inner)
    frame.pack(pady=10)

    # 상대 기간 모드 라디오 + 입력
    relative_radio = tk.Radiobutton(frame, text="기간 지정:", variable=bt_period_mode_var, value="relative")
    relative_radio.grid(row=0, column=0, padx=5, sticky="w")

    period_value_entry = tk.Entry(frame, width=5)
    period_value_entry.grid(row=0, column=1, padx=5)
    period_value_entry.insert(0, config.config["backtest"].get("period", 12))
    period_value_entry.bind("<KeyRelease>", lambda event: update_dates(save=True))
    Tooltip(period_value_entry, BACKTEST_INPUT_HELP["기간 숫자"])

    period_unit_var = tk.StringVar()
    period_unit_menu = ttk.Combobox(frame, textvariable=period_unit_var, values=unit_display_options, width=5,
                                    state="readonly")
    period_unit_menu.grid(row=0, column=2, padx=5)
    saved_unit = config.config["backtest"].get("unit", "mo")
    period_unit_var.set(UNIT_DISPLAY_NAMES.get(saved_unit, saved_unit))
    period_unit_menu.bind("<<ComboboxSelected>>", lambda event: update_dates(save=True))
    Tooltip(period_unit_menu, BACKTEST_INPUT_HELP["단위"])

    # 절대 날짜 모드 라디오 + 입력
    absolute_radio = tk.Radiobutton(frame, text="날짜 지정:", variable=bt_period_mode_var, value="absolute")
    absolute_radio.grid(row=1, column=0, padx=5, sticky="w")

    saved_start = config.config["backtest"].get("start_date", one_year_ago.strftime('%Y-%m-%d'))
    saved_end = config.config["backtest"].get("end_date", now.strftime('%Y-%m-%d'))

    tk.Label(frame, text="시작").grid(row=1, column=1, padx=(5, 0), sticky="e")
    if _has_calendar:
        _s = datetime.strptime(saved_start, '%Y-%m-%d')
        bt_start_date_entry = _DateEntry(frame, width=10, date_pattern="yyyy-mm-dd",
                                         year=_s.year, month=_s.month, day=_s.day, locale="ko_KR")
    else:
        bt_start_date_entry = tk.Entry(frame, width=12)
        bt_start_date_entry.insert(0, saved_start)
    bt_start_date_entry.grid(row=1, column=2, padx=5)
    Tooltip(bt_start_date_entry, "시작 날짜 (YYYY-MM-DD)")

    tk.Label(frame, text="종료").grid(row=1, column=3, padx=(5, 0), sticky="e")
    if _has_calendar:
        _e = datetime.strptime(saved_end, '%Y-%m-%d')
        bt_end_date_entry = _DateEntry(frame, width=10, date_pattern="yyyy-mm-dd",
                                       year=_e.year, month=_e.month, day=_e.day, locale="ko_KR")
    else:
        bt_end_date_entry = tk.Entry(frame, width=12)
        bt_end_date_entry.insert(0, saved_end)
    bt_end_date_entry.grid(row=1, column=4, padx=5)
    Tooltip(bt_end_date_entry, "종료 날짜 (YYYY-MM-DD)")

    def _toggle_period_mode(*args):
        mode = bt_period_mode_var.get()
        if mode == "relative":
            period_value_entry.config(state=tk.NORMAL)
            period_unit_menu.config(state="readonly")
            bt_start_date_entry.config(state=tk.DISABLED)
            bt_end_date_entry.config(state=tk.DISABLED)
        else:
            period_value_entry.config(state=tk.DISABLED)
            period_unit_menu.config(state=tk.DISABLED)
            bt_start_date_entry.config(state=tk.NORMAL)
            bt_end_date_entry.config(state=tk.NORMAL)
        # 날짜 범위 표시 업데이트
        if mode == "absolute":
            start_str = bt_start_date_entry.get().strip()
            end_str = bt_end_date_entry.get().strip()
            period_range_label.config(text=f"분석 기간: {start_str} ~ {end_str} (사용자 지정)")
        else:
            update_dates()

    bt_period_mode_var.trace_add("write", _toggle_period_mode)
    _toggle_period_mode()  # 초기 상태 설정

    tk.Label(frame, text="전략 선택:").grid(row=2, column=0, padx=5)
    method_var = tk.StringVar()
    method_menu = ttk.Combobox(frame, textvariable=method_var, values=strategy_display_options, width=20, state="readonly")
    method_menu.grid(row=2, column=1, columnspan=3, padx=5, pady=5, sticky="w")
    saved_method = config.config["backtest"].get("method", "macd")
    method_var.set(STRATEGY_DISPLAY_NAMES.get(saved_method, saved_method))
    Tooltip(method_menu, BACKTEST_INPUT_HELP["전략 선택"])

    # 손절 설정
    stoploss_enabled_var = tk.BooleanVar(value=config.config["backtest"].get("stoploss_enabled", False))
    stoploss_pct_var = tk.StringVar(value=str(config.config["backtest"].get("stoploss_pct", 5)))

    stoploss_chk = tk.Checkbutton(frame, text="손절(%)", variable=stoploss_enabled_var)
    stoploss_chk.grid(row=3, column=0, padx=5, pady=3, sticky="w")
    stoploss_entry = tk.Entry(frame, textvariable=stoploss_pct_var, width=5)
    stoploss_entry.grid(row=3, column=1, padx=5, pady=3, sticky="w")
    Tooltip(stoploss_chk, "매수 후 설정한 %만큼 하락하면 자동 매도합니다.\n예: 5% → 100달러에 매수 시 95달러 이하로 떨어지면 손절.")

    # 레짐 필터
    regime_filter_var = tk.BooleanVar(value=config.config["backtest"].get("regime_filter", False))
    regime_chk = tk.Checkbutton(frame, text="레짐 필터", variable=regime_filter_var)
    regime_chk.grid(row=3, column=2, padx=5, pady=3, sticky="w")
    Tooltip(regime_chk, "시장(SPY) 하락장일 때 매수 시그널을 무시합니다.\nSPY MA20 < MA60이면 하락장으로 판단합니다.")

    # 추적 손절
    trailing_enabled_var = tk.BooleanVar(value=config.config["backtest"].get("trailing_enabled", False))
    trailing_type_var = tk.StringVar(value=config.config["backtest"].get("trailing_type", "pct"))
    trailing_param_var = tk.StringVar(value=str(config.config["backtest"].get("trailing_param", 5.0)))

    trailing_chk = tk.Checkbutton(frame, text="추적 손절", variable=trailing_enabled_var)
    trailing_chk.grid(row=4, column=0, padx=5, pady=3, sticky="w")
    Tooltip(trailing_chk, "최고가 대비 설정 비율/ATR만큼 하락 시 자동 매도.\n일반 손절과 달리 가격이 오를수록 손절선도 따라 올라갑니다.")

    trailing_type_menu = ttk.Combobox(frame, textvariable=trailing_type_var,
                                       values=["pct", "atr"], width=4, state="readonly")
    trailing_type_menu.grid(row=4, column=1, padx=5, pady=3, sticky="w")
    Tooltip(trailing_type_menu, "pct = 퍼센트 방식, atr = ATR 배수 방식")

    trailing_entry = tk.Entry(frame, textvariable=trailing_param_var, width=5)
    trailing_entry.grid(row=4, column=2, padx=5, pady=3, sticky="w")
    Tooltip(trailing_entry, "퍼센트: 5.0 → 최고가 대비 5% 하락 시 매도\nATR: 2.0 → 최고가 - ATR×2 이하 시 매도")

    # 포지션 사이징
    position_sizing_var = tk.StringVar(value=config.config["backtest"].get("position_sizing", "full"))
    tk.Label(frame, text="포지션:").grid(row=5, column=0, padx=5, pady=3, sticky="w")
    pos_menu = ttk.Combobox(frame, textvariable=position_sizing_var,
                             values=["full", "kelly", "atr", "fixed"], width=8, state="readonly")
    pos_menu.grid(row=5, column=1, padx=5, pady=3, sticky="w")
    Tooltip(pos_menu, "full: 전체 (100%)\nkelly: 켈리 공식 (최적 비율)\natr: ATR 기반 리스크 조절\nfixed: 고정 비율 (2%)")

    # 워크포워드 테스트
    walk_forward_var = tk.BooleanVar(value=config.config["backtest"].get("walk_forward_enabled", False))
    wf_chk = tk.Checkbutton(frame, text="워크포워드 (70/30)", variable=walk_forward_var)
    wf_chk.grid(row=5, column=2, padx=5, pady=3, sticky="w")
    Tooltip(wf_chk, "데이터를 70% 학습 / 30% 검증으로 분할하여\n전략의 과적합 여부를 확인합니다.")

    # 수수료 + 슬리피지 설정
    commission_var = tk.StringVar(value=str(config.config["backtest"].get("commission_rate", 0.001) * 100))
    slippage_var = tk.StringVar(value=str(config.config["backtest"].get("slippage_pct", 0.0005) * 100))

    tk.Label(frame, text="수수료(%):").grid(row=6, column=0, padx=5, pady=3, sticky="w")
    commission_entry = tk.Entry(frame, textvariable=commission_var, width=6)
    commission_entry.grid(row=6, column=1, padx=5, pady=3, sticky="w")
    Tooltip(commission_entry, "거래당 수수료율 (%).\n예: 0.1 → 매수/매도 각 0.1%, 왕복 0.2%")

    tk.Label(frame, text="슬리피지(%):").grid(row=6, column=2, padx=5, pady=3, sticky="w")
    slippage_entry = tk.Entry(frame, textvariable=slippage_var, width=6)
    slippage_entry.grid(row=6, column=3, padx=5, pady=3, sticky="w")
    Tooltip(slippage_entry, "체결 시 가격 미끄러짐 (%).\n예: 0.05 → 매수/매도 각 0.05%, 왕복 0.1%")

    # Phase 12-3: Strategy description label (STRATEGY_HELP 멀티라인)
    strategy_desc_label = tk.Label(_scroll_inner, text="", font=("Arial", 9), fg="#333333",
                                    justify=tk.LEFT, wraplength=450, anchor="w")
    strategy_desc_label.pack(pady=2, padx=10, fill=tk.X)

    def update_strategy_desc(*args):
        key = _get_method_key()
        desc = STRATEGY_HELP.get(key, STRATEGY_DESCRIPTIONS.get(key, ""))
        strategy_desc_label.config(text=desc)

    method_var.trace_add("write", update_strategy_desc)
    update_strategy_desc()

    btn_frame = tk.Frame(_scroll_inner)
    btn_frame.pack(pady=10)

    # ── 분석 드롭다운 메뉴 버튼 ──
    analysis_mb = tk.Menubutton(btn_frame, text="▼ 분석", font=("Arial", 10, "bold"),
                                relief=tk.RAISED, bd=2, padx=8, pady=2)
    analysis_menu = tk.Menu(analysis_mb, tearoff=0)
    analysis_mb["menu"] = analysis_menu
    analysis_mb.pack(side=tk.LEFT, padx=5)

    # search_btn은 기존 코드에서 state 제어용으로 참조됨 → analysis_mb에 위임
    search_btn = analysis_mb
    analysis_menu.add_command(label="검색 및 분석", command=save_and_search)

    def _parse_period():
        """UI 기간 설정을 파싱하여 (start_str, end_str) 반환. 실패 시 None."""
        mode = bt_period_mode_var.get()
        if mode == "absolute":
            start_str = bt_start_date_entry.get().strip()
            end_str = bt_end_date_entry.get().strip()
            try:
                datetime.strptime(start_str, '%Y-%m-%d')
                datetime.strptime(end_str, '%Y-%m-%d')
            except ValueError:
                messagebox.showerror("오류", "날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)")
                return None
            if start_str >= end_str:
                messagebox.showerror("오류", "시작 날짜가 종료 날짜보다 이전이어야 합니다.")
                return None
            return start_str, end_str
        else:
            value_text = period_value_entry.get().strip()
            if not value_text.isdigit():
                messagebox.showerror("오류", "기간 숫자를 입력하세요.")
                return None
            value = int(value_text)
            if value < 1 or value > 9999:
                messagebox.showerror("오류", "1~9999 범위의 숫자를 입력하세요.")
                return None
            unit = _get_unit_key()
            if unit not in ('d', 'mo', 'y'):
                messagebox.showerror("오류", "기간 단위를 일, 개월, 년 중 하나로 선택하세요.")
                return None
            now = datetime.now()
            if unit == 'd':
                start = now - timedelta(days=value)
            elif unit == 'mo':
                start = now - timedelta(days=value * 30)
            else:
                start = now - timedelta(days=value * 365)
            return start.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d')

    def _compare_all_strategies():
        """모든 전략을 동시 실행하여 비교 테이블과 에쿼티 커브 오버레이 표시."""
        parsed = _parse_period()
        if parsed is None:
            return
        start_str, end_str = parsed

        search_btn.config(state=tk.DISABLED)

        def _run_compare():
            try:
                data = _retry_download(ticker_symbol, start_str, end_str)
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                if isinstance(data.index, pd.MultiIndex):
                    data = data.droplevel(0, axis=0)
                if data.empty:
                    popup.after(0, lambda: messagebox.showerror("오류", "데이터가 없습니다."))
                    return

                close_prices = data['Close']
                results = {}
                # 비교 실행 중 개별 전략 차트 팝업 억제
                _suppress_chart[0] = True
                # 거래가 있는 전략만 비교 (macd, rsi는 시각화 전용이라 제외)
                compare_strategies = ["macd_rsi", "bollinger", "ma_cross", "momentum_signal", "momentum_return_ma", "ichimoku"]
                for strat_key in compare_strategies:
                    handler = strategy_dispatch.get(strat_key)
                    if not handler:
                        continue
                    try:
                        data_copy = data.copy()
                        _result = handler(data_copy, close_prices.copy())
                        bd, sd, profs = _result[0], _result[1], _result[2]
                        if profs:
                            p_series = pd.Series(profs)
                            total_ret = (1 + p_series).prod() - 1
                            wins = p_series[p_series > 0]
                            win_rate = len(wins) / len(p_series) if len(p_series) > 0 else 0
                            risk_free = config.get_risk_free_rate()
                            rfpt = risk_free / 252
                            sharpe_val = 0
                            if len(p_series) >= 2 and p_series.std() > 0:
                                sharpe_val = (p_series.mean() - rfpt) / p_series.std() * np.sqrt(252)
                            # MDD
                            equity = [1.0]
                            for p in profs:
                                equity.append(equity[-1] * (1 + p))
                            eq = pd.Series(equity)
                            mdd = ((eq - eq.cummax()) / eq.cummax()).min()
                            results[strat_key] = {
                                "total_return": total_ret,
                                "win_rate": win_rate,
                                "sharpe": sharpe_val,
                                "mdd": mdd,
                                "trades": len(profs),
                                "equity": equity,
                                "buy_dates": bd,
                                "sell_dates": sd,
                            }
                    except Exception as e:
                        logging.warning(f"[COMPARE] {strat_key} failed: {e}")

                _suppress_chart[0] = False

                def _show_compare():
                    search_btn.config(state=tk.NORMAL)
                    if not results:
                        messagebox.showinfo("알림", "비교 가능한 거래가 없습니다.")
                        return
                    _clear_result_area()

                    comp_frame = tk.LabelFrame(result_container, text=f"모든 전략 비교 ({start_str} ~ {end_str})",
                                                font=("Arial", 10, "bold"))
                    comp_frame.pack(fill=tk.X, padx=10, pady=5)

                    header = tk.Frame(comp_frame)
                    header.pack(fill=tk.X, padx=8, pady=2)
                    for col_text, col_w in [("전략", 14), ("수익률", 9), ("승률", 8),
                                             ("샤프", 7), ("MDD", 9), ("거래수", 7)]:
                        tk.Label(header, text=col_text, font=("Arial", 9, "bold"),
                                 width=col_w, anchor="center").pack(side=tk.LEFT)

                    best_key = max(results, key=lambda k: results[k]["sharpe"])
                    for key, res in sorted(results.items(), key=lambda x: -x[1]["sharpe"]):
                        row = tk.Frame(comp_frame)
                        row.pack(fill=tk.X, padx=8, pady=1)
                        name = STRATEGY_DISPLAY_NAMES.get(key, key)
                        is_best = key == best_key
                        font_w = ("Arial", 9, "bold") if is_best else ("Arial", 9)
                        ret_color = "#2E7D32" if res["total_return"] > 0 else "#E74C3C"
                        tk.Label(row, text=("★ " if is_best else "") + name,
                                 font=font_w, width=14, anchor="w").pack(side=tk.LEFT)
                        tk.Label(row, text=f"{res['total_return']:.1%}",
                                 font=font_w, width=9, fg=ret_color).pack(side=tk.LEFT)
                        tk.Label(row, text=f"{res['win_rate']:.0%}",
                                 font=font_w, width=8).pack(side=tk.LEFT)
                        tk.Label(row, text=f"{res['sharpe']:.2f}",
                                 font=font_w, width=7).pack(side=tk.LEFT)
                        tk.Label(row, text=f"{res['mdd']:.1%}",
                                 font=font_w, width=9, fg="#E74C3C").pack(side=tk.LEFT)
                        tk.Label(row, text=f"{res['trades']}",
                                 font=font_w, width=7).pack(side=tk.LEFT)

                    skipped = [k for k in compare_strategies if k not in results]
                    if skipped:
                        skipped_names = [STRATEGY_DISPLAY_NAMES.get(k, k) for k in skipped]
                        tk.Label(comp_frame, text=f"(생략: {', '.join(skipped_names)} — 거래 없음 또는 오류)",
                                 font=("Arial", 8), fg="#999").pack(padx=8, pady=(0, 5), anchor="w")

                    # 에쿼티 커브 오버레이 차트
                    fig = Figure(figsize=(10, 5)); ax = fig.add_subplot(111)
                    for key, res in results.items():
                        name = STRATEGY_DISPLAY_NAMES.get(key, key)
                        eq = res["equity"]
                        bd = res["buy_dates"]
                        sd = res["sell_dates"]
                        # 에쿼티: [1.0(첫 매수 시점), 매도1 후, 매도2 후, ...]
                        # 날짜: 첫 매수일 + 각 매도일
                        dates = []
                        if bd:
                            dates.append(pd.Timestamp(bd[0]))
                        for d in sd:
                            dates.append(pd.Timestamp(d))
                        # 미청산 포지션(profits에는 있지만 sell_dates에는 없음) 처리
                        while len(dates) < len(eq):
                            dates.append(close_prices.index[-1])
                        if len(dates) == len(eq):
                            ax.plot(dates, eq, label=f"{name} ({res['total_return']:.1%})",
                                    linewidth=1.5 + (0.5 if key == best_key else 0))
                        else:
                            # 최종 폴백: 실제 데이터 기간을 균등 분할
                            import numpy as np_local
                            t_start = close_prices.index[0]
                            t_end = close_prices.index[-1]
                            dates = pd.date_range(t_start, t_end, periods=len(eq))
                            ax.plot(dates, eq, label=f"{name} ({res['total_return']:.1%})",
                                    linewidth=1.5)
                    ax.axhline(y=1.0, color='gray', linewidth=0.5, linestyle=':')
                    ax.set_title(f"{stock_display} 전략별 에쿼티 커브 비교 ({start_str} ~ {end_str})", fontsize=12, fontweight="bold")
                    ax.set_ylabel("누적 수익률")
                    ax.legend(fontsize=8, loc="upper left")
                    ax.grid(alpha=0.3)
                    fig.tight_layout()
                    # 에쿼티 커브를 result_container 안에 임베딩
                    try:
                        plt.close(fig)
                    except Exception:
                        pass
                    open_figures.append(fig)
                    chart_frame = tk.LabelFrame(result_container, text="에쿼티 커브 비교",
                                                font=("Arial", 10, "bold"))
                    chart_frame.pack(fill=tk.X, padx=10, pady=5)
                    canvas = FigureCanvasTkAgg(fig, master=chart_frame)
                    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
                    canvas.draw()
                    tk.Button(chart_frame, text="PNG 저장",
                              command=lambda f=fig: _save_fig_png(f)).pack(pady=3)

                popup.after(0, _show_compare)
            except Exception as e:
                _suppress_chart[0] = False
                logging.error(f"[COMPARE] Error: {e}")
                popup.after(0, lambda: search_btn.config(state=tk.NORMAL))

        threading.Thread(target=_run_compare, daemon=True).start()

    analysis_menu.add_command(label="모든 전략 비교", command=_compare_all_strategies)

    def _run_sensitivity_analysis():
        """현재 전략의 핵심 파라미터를 그리드 탐색하여 수익률 히트맵 표시."""
        method = _get_method_key()
        parsed = _parse_period()
        if parsed is None:
            return
        start_str, end_str = parsed

        search_btn.config(state=tk.DISABLED)

        def _run():
            try:
                data = _retry_download(ticker_symbol, start_str, end_str)
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                if isinstance(data.index, pd.MultiIndex):
                    data = data.droplevel(0, axis=0)
                if data.empty:
                    popup.after(0, lambda: messagebox.showerror("오류", "데이터가 없습니다."))
                    return

                # 전략별 파라미터 그리드 정의
                param_grids = {
                    "rsi": {"lower": [20, 25, 30, 35], "upper": [65, 70, 75, 80]},
                    "macd_rsi": {"lower": [20, 25, 30, 35], "upper": [65, 70, 75, 80]},
                    "ma_cross": {"short": [5, 10, 15, 20], "long": [20, 50, 100, 200]},
                    "bollinger": {"period": [10, 15, 20, 30], "std_dev_multiplier": [1.5, 2.0, 2.5, 3.0]},
                    "momentum_signal": {"lower": [20, 25, 30, 35], "upper": [65, 70, 75, 80]},
                    "momentum_return_ma": {"short": [5, 10, 15, 20], "long": [20, 50, 100, 200]},
                }
                grid = param_grids.get(method)
                if not grid:
                    popup.after(0, lambda: messagebox.showinfo("알림",
                        f"{STRATEGY_DISPLAY_NAMES.get(method, method)} 전략은 민감도 분석을 지원하지 않습니다."))
                    return

                import copy as _copy
                keys = list(grid.keys())
                vals1 = grid[keys[0]]
                vals2 = grid[keys[1]]
                results_grid = np.zeros((len(vals1), len(vals2)))

                # 민감도 분석 중 개별 전략 차트 팝업 억제
                _suppress_chart[0] = True

                for i, v1 in enumerate(vals1):
                    for j, v2 in enumerate(vals2):
                        # MA 교차: short >= long 이면 skip
                        if keys[0] == "short" and keys[1] == "long" and v1 >= v2:
                            results_grid[i, j] = np.nan
                            continue
                        # RSI: lower >= upper 이면 skip
                        if keys[0] == "lower" and keys[1] == "upper" and v1 >= v2:
                            results_grid[i, j] = np.nan
                            continue

                        saved = _copy.deepcopy(config.config["current"])
                        try:
                            # 파라미터 설정
                            if method in ("rsi", "macd_rsi", "momentum_signal"):
                                config.config["current"]["rsi"][keys[0]] = v1
                                config.config["current"]["rsi"][keys[1]] = v2
                            elif method in ("ma_cross", "momentum_return_ma"):
                                config.config["current"]["ma_cross"][keys[0]] = v1
                                config.config["current"]["ma_cross"][keys[1]] = v2
                            elif method == "bollinger":
                                config.config["current"]["bollinger"][keys[0]] = v1
                                config.config["current"]["bollinger"][keys[1]] = v2

                            handler = strategy_dispatch.get(method)
                            if handler:
                                data_copy = data.copy()
                                _res = handler(data_copy, data['Close'].copy())
                                _, _, profs = _res[0], _res[1], _res[2]
                                if profs:
                                    total_ret = (1 + pd.Series(profs)).prod() - 1
                                    results_grid[i, j] = total_ret * 100
                                else:
                                    results_grid[i, j] = 0
                            else:
                                results_grid[i, j] = np.nan
                        except Exception:
                            results_grid[i, j] = np.nan
                        finally:
                            for k, v in saved.items():
                                config.config["current"][k] = v

                _suppress_chart[0] = False

                def _show_heatmap():
                    search_btn.config(state=tk.NORMAL)
                    fig = Figure(figsize=(8, 6)); ax = fig.add_subplot(111)
                    im = ax.imshow(results_grid, cmap='RdYlGn', aspect='auto')
                    ax.set_xticks(range(len(vals2)))
                    ax.set_yticks(range(len(vals1)))
                    ax.set_xticklabels([str(v) for v in vals2])
                    ax.set_yticklabels([str(v) for v in vals1])
                    ax.set_xlabel(keys[1])
                    ax.set_ylabel(keys[0])

                    for i in range(len(vals1)):
                        for j in range(len(vals2)):
                            val = results_grid[i, j]
                            if not np.isnan(val):
                                color = "white" if abs(val) > 15 else "black"
                                ax.text(j, i, f"{val:.1f}%", ha="center", va="center",
                                        fontsize=8, color=color, fontweight="bold")

                    fig.colorbar(im, ax=ax, label="수익률 (%)")
                    name = STRATEGY_DISPLAY_NAMES.get(method, method)
                    ax.set_title(f"{stock_display} {name} 파라미터 민감도", fontsize=12, fontweight="bold")
                    fig.tight_layout()
                    # 히트맵을 result_container 안에 임베딩
                    _clear_result_area()
                    try:
                        plt.close(fig)
                    except Exception:
                        pass
                    open_figures.append(fig)
                    heat_frame = tk.LabelFrame(result_container, text="파라미터 민감도 히트맵",
                                               font=("Arial", 10, "bold"))
                    heat_frame.pack(fill=tk.X, padx=10, pady=5)
                    canvas = FigureCanvasTkAgg(fig, master=heat_frame)
                    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
                    canvas.draw()
                    tk.Button(heat_frame, text="PNG 저장",
                              command=lambda f=fig: _save_fig_png(f)).pack(pady=3)

                popup.after(0, _show_heatmap)
            except Exception as e:
                _suppress_chart[0] = False
                logging.error(f"[SENSITIVITY] Error: {e}")
                popup.after(0, lambda: search_btn.config(state=tk.NORMAL))

        threading.Thread(target=_run, daemon=True).start()

    analysis_menu.add_command(label="민감도 분석", command=_run_sensitivity_analysis)

    # 기술 차트
    def _open_tech_chart():
        import stock_monitor_gui
        stock_monitor_gui.show_technical_chart(stock_display)

    analysis_menu.add_separator()
    analysis_menu.add_command(label="기술 차트", command=_open_tech_chart)

    # ── 종목 뉴스 버튼 ──
    def open_ticker_news_popup():
        from news_panel import fetch_ticker_news

        news_popup = tk.Toplevel(popup)
        news_popup.title(f"{stock_display} 뉴스")
        news_popup.state('zoomed')
        news_popup.transient(popup)

        header_label = tk.Label(
            news_popup, text=f"{ticker_symbol} 뉴스 | 현재가 로딩중...",
            font=("Arial", 11, "bold"), pady=8
        )
        header_label.pack(fill=tk.X)

        # 스크롤 영역
        scroll_frame = tk.Frame(news_popup)
        scroll_frame.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(scroll_frame, highlightthickness=0)
        scrollbar = tk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        _win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # inner 너비를 canvas에 맞추기 + 제목 wraplength 갱신
        _title_labels = []

        def _resize_inner(e):
            canvas.itemconfig(_win_id, width=e.width)
            wrap = max(100, e.width - 40)
            for lbl in _title_labels:
                try:
                    lbl.config(wraplength=wrap)
                except tk.TclError:
                    pass
        canvas.bind("<Configure>", _resize_inner)

        # 마우스 휠 스크롤 — news_popup 전체에 바인딩
        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            # 무한 스크롤: 바닥 도달 시 추가 로드
            if canvas.yview()[1] >= 0.95 and news_state["has_more"] and not news_state["loading"]:
                _load_more()

        def _bind_wheel(e):
            news_popup.bind_all("<MouseWheel>", _on_wheel)

        def _unbind_wheel(e):
            news_popup.unbind_all("<MouseWheel>")

        news_popup.bind("<Enter>", _bind_wheel)
        news_popup.bind("<Leave>", _unbind_wheel)
        news_popup.bind("<Destroy>", lambda e: _unbind_wheel(e) if e.widget == news_popup else None)

        loading_label = tk.Label(inner, text="뉴스를 불러오는 중...", font=("Arial", 10), fg="#444444", pady=20)
        loading_label.pack(anchor="w", padx=10)

        # 상태 표시 라벨 (하단)
        status_frame = tk.Frame(news_popup)
        status_frame.pack(fill=tk.X, pady=3)
        status_label = tk.Label(status_frame, text="", font=("Arial", 8), fg="#888888")
        status_label.pack(side=tk.LEFT, padx=10)
        tk.Button(status_frame, text="닫기", command=news_popup.destroy).pack(side=tk.RIGHT, padx=10)

        news_state = {"loaded_count": 0, "current_price": None, "loading": False, "has_more": True}

        def _add_news_items(news_list, start_idx=0):
            """뉴스 항목들을 inner 프레임 끝에 추가."""
            canvas_w = canvas.winfo_width()
            wrap = max(100, canvas_w - 40) if canvas_w > 1 else 560

            for item in news_list[start_idx:]:
                row = tk.Frame(inner)
                row.pack(fill=tk.X, padx=6, pady=2)

                # 제목 (클릭 → 브라우저)
                url = item.get("url", "")
                title_lbl = tk.Label(
                    row, text=item["title"],
                    font=("Arial", 10, "bold"), fg="#1A0DAB", anchor="w",
                    cursor="hand2" if url else "", wraplength=wrap, justify=tk.LEFT
                )
                title_lbl.pack(fill=tk.X, anchor="w")
                _title_labels.append(title_lbl)
                if url:
                    title_lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))

                # 언론사 | 날짜
                meta_parts = []
                if item.get("publisher"):
                    meta_parts.append(item["publisher"])
                if item.get("time"):
                    meta_parts.append(item["time"])
                if meta_parts:
                    tk.Label(row, text=" | ".join(meta_parts),
                             font=("Arial", 8), fg="#666666", anchor="w").pack(anchor="w")

                # 구분선
                ttk.Separator(inner, orient="horizontal").pack(fill=tk.X, padx=6, pady=2)

        def _fetch_news(count):
            news_state["loading"] = True
            try:
                status_label.config(text="불러오는 중...")
            except tk.TclError:
                return
            news_list, current_price = fetch_ticker_news(ticker_symbol, count=count)
            try:
                news_popup.after(0, lambda: _populate(news_list, current_price, count))
            except tk.TclError:
                pass

        def _populate(news_list, current_price, requested_count):
            news_state["loading"] = False

            # 헤더 업데이트
            if current_price is not None:
                news_state["current_price"] = current_price
                header_label.config(text=f"{ticker_symbol} 뉴스 | 현재가: ${current_price:.2f}")
            elif news_state["current_price"] is None:
                header_label.config(text=f"{ticker_symbol} 뉴스")

            prev_count = news_state["loaded_count"]

            if not news_list:
                if prev_count == 0:
                    for w in inner.winfo_children():
                        w.destroy()
                    tk.Label(inner, text="뉴스가 없거나 불러올 수 없습니다.",
                             font=("Arial", 10), fg="#444444", pady=20).pack(anchor="w", padx=10)
                news_state["has_more"] = False
                status_label.config(text=f"전체 {prev_count}건")
                return

            # 추가 로드인 경우: 새 항목만 추가
            if prev_count > 0 and len(news_list) > prev_count:
                _add_news_items(news_list, start_idx=prev_count)
            elif prev_count == 0:
                # 첫 로드: 로딩 텍스트 제거 후 전체 추가
                for w in inner.winfo_children():
                    w.destroy()
                _add_news_items(news_list)

            news_state["loaded_count"] = len(news_list)

            if len(news_list) < requested_count:
                news_state["has_more"] = False
                status_label.config(text=f"전체 {len(news_list)}건")
            else:
                news_state["has_more"] = True
                status_label.config(text=f"{len(news_list)}건 로드됨 — 스크롤하여 더 보기")

            if prev_count == 0:
                canvas.yview_moveto(0)

        def _load_more():
            if news_state["loading"] or not news_state["has_more"]:
                return
            new_count = news_state["loaded_count"] + 25
            threading.Thread(target=_fetch_news, args=(new_count,), daemon=True).start()

        threading.Thread(target=_fetch_news, args=(25,), daemon=True).start()

    news_btn = tk.Button(btn_frame, text="종목 뉴스", command=open_ticker_news_popup)
    news_btn.pack(side=tk.LEFT, padx=5)

    # ── 종목 추가/삭제 버튼 ──
    if app_state is not None:
        watchlist_btn = tk.Button(btn_frame, text="...", width=8)
        watchlist_btn.pack(side=tk.LEFT, padx=5)

        def _update_watchlist_btn():
            with app_state.watchlist_lock:
                in_wl = ticker_symbol in app_state.watchlist
            if in_wl:
                watchlist_btn.config(text="종목 삭제", fg="white", bg="#E74C3C",
                                    activebackground="#C0392B", activeforeground="white")
            else:
                watchlist_btn.config(text="종목 추가", fg="white", bg="#4A90D9",
                                    activebackground="#357ABD", activeforeground="white")

        def _toggle_watchlist():
            with app_state.watchlist_lock:
                in_wl = ticker_symbol in app_state.watchlist

            if in_wl:
                if not messagebox.askyesno("삭제 확인",
                                           f"{stock_display}을(를) 워치리스트에서 삭제하시겠습니까?",
                                           parent=popup):
                    return
                with app_state.watchlist_lock:
                    if ticker_symbol in app_state.watchlist:
                        app_state.watchlist.remove(ticker_symbol)
            else:
                if not messagebox.askyesno("추가 확인",
                                           f"{stock_display}을(를) 워치리스트에 추가하시겠습니까?",
                                           parent=popup):
                    return
                with app_state.watchlist_lock:
                    if ticker_symbol not in app_state.watchlist:
                        app_state.watchlist.append(ticker_symbol)

            if hasattr(app_state, 'save_watchlist') and app_state.save_watchlist:
                app_state.save_watchlist()
            if hasattr(app_state, 'refresh_table_once') and app_state.refresh_table_once:
                app_state.refresh_table_once()
            _update_watchlist_btn()

        watchlist_btn.config(command=_toggle_watchlist)
        _update_watchlist_btn()

    close_btn = tk.Button(btn_frame, text="닫기", command=lambda: (cleanup_figures(), popup.destroy()))
    close_btn.pack(side=tk.LEFT, padx=5)

    update_dates()
