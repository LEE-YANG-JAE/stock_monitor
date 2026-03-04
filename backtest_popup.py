import csv
import logging
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox, filedialog

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import yfinance as yf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import config
from help_texts import STRATEGY_HELP, BACKTEST_INPUT_HELP, CHART_HELP, RESULT_HELP
from ui_components import Tooltip, HelpTooltip

plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.family'] = 'Malgun Gothic'

strategy_options = ["ma_cross", "macd", "rsi", "macd_rsi", "bollinger", "momentum_signal", "momentum_return_ma"]

# 전략 한글 표시명 매핑
STRATEGY_DISPLAY_NAMES = {
    "ma_cross": "이동평균 교차",
    "macd": "MACD 교차",
    "rsi": "RSI 과매수/과매도",
    "macd_rsi": "MACD+RSI 복합",
    "bollinger": "볼린저 밴드",
    "momentum_signal": "종합 모멘텀",
    "momentum_return_ma": "수익률+MA 교차",
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
    """yf.download with retry logic."""
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


def _safe_division(exit_price, entry_price):
    """Phase 3-6: Safe profit calculation avoiding division by zero."""
    if entry_price > 0:
        return (exit_price - entry_price) / entry_price
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
        value_text = period_value_entry.get().strip()
        if not value_text.isdigit():
            messagebox.showerror("오류", "기간 숫자는 정수로 입력하세요.")
            return

        value = int(value_text)
        # Phase 5-3: Range validation with message
        if value < 1 or value > 9999:
            messagebox.showerror("오류", "1~9999 범위의 숫자를 입력하세요.")
            return

        unit = _get_unit_key()
        method = _get_method_key()

        if unit not in ('d', 'mo', 'y'):
            messagebox.showerror("오류", "기간 단위를 일, 개월, 년 중 하나로 선택하세요.")
            return

        config.config["backtest"]["period"] = value
        config.config["backtest"]["unit"] = unit
        config.config["backtest"]["method"] = method
        config.save_config(config.get_config())

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
                run_backtest(ticker_symbol, value, unit, method)
            finally:
                try:
                    popup.after(0, _finish)
                except tk.TclError:
                    pass

        threading.Thread(target=_run, daemon=True).start()

    # --- Plot functions ---

    def _create_graph_popup(fig, title, help_text=""):
        """Create a Toplevel window with matplotlib figure and save buttons."""
        # matplotlib 기본 figure manager 창이 뜨지 않도록 제거 (FigureCanvasTkAgg로 직접 임베딩)
        try:
            plt.close(fig)
        except Exception:
            pass
        open_figures.append(fig)
        graph_popup = tk.Toplevel()
        graph_popup.title(title)
        # Phase 9-5: Responsive window size
        sw = graph_popup.winfo_screenwidth()
        sh = graph_popup.winfo_screenheight()
        w = int(sw * 0.8)
        h = int(sh * 0.8)
        graph_popup.geometry(f"{w}x{h}")
        graph_popup.minsize(600, 400)

        canvas = FigureCanvasTkAgg(fig, master=graph_popup)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        canvas.draw()

        # 차트 읽는 법
        if help_text:
            help_frame = tk.LabelFrame(graph_popup, text="차트 읽는 법", font=("Arial", 10, "bold"))
            help_frame.pack(fill=tk.X, padx=8, pady=(0, 2))
            tk.Label(help_frame, text=help_text, font=("Arial", 9), justify=tk.LEFT,
                     wraplength=700, anchor="w").pack(padx=8, pady=4, anchor="w")

        # Phase 12-1: Save buttons
        btn_frame = tk.Frame(graph_popup)
        btn_frame.pack(fill=tk.X, pady=5)

        def save_png():
            path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG files", "*.png"), ("All files", "*.*")],
                initialfile=f"{ticker_symbol}_backtest.png"
            )
            if path:
                fig.savefig(path, dpi=150, bbox_inches='tight')
                messagebox.showinfo("저장 완료", f"그래프가 저장되었습니다:\n{path}")

        tk.Button(btn_frame, text="그래프 저장 (PNG)", command=save_png).pack(side=tk.RIGHT, padx=5)

        def on_graph_close():
            if fig in open_figures:
                open_figures.remove(fig)
            plt.close(fig)
            graph_popup.destroy()

        graph_popup.protocol("WM_DELETE_WINDOW", on_graph_close)
        return graph_popup

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

        summary_frame = tk.LabelFrame(result_container, text="백테스트 결과 요약", font=("Arial", 10, "bold"))
        summary_frame.pack(fill=tk.X, padx=10, pady=5)

        rows = [
            ("총 수익률", f"{total_return:.2%}"),
            ("연환산 수익률", f"{annual_return:.2%}"),
            ("최대 낙폭 (MDD)", f"{mdd:.2%}  ({mdd_date})" if mdd_date else f"{mdd:.2%}"),
            ("최대 수익 거래", f"{best_trade:.2%}  ({best_date})"),
            ("최대 손실 거래", f"{worst_trade:.2%}  ({worst_date})"),
        ]
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
        fig, ax1 = plt.subplots(figsize=(10, 6))

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

        _create_graph_popup(fig, f"{stock_display} 백테스트 결과", CHART_HELP.get("macd", ""))

    def plot_rsi_backtest(ticker_symbol, close_prices, rsi, buy_signals, sell_signals):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

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

        _create_graph_popup(fig, f"{stock_display} 백테스트 결과", CHART_HELP.get("rsi", ""))

    def plot_macd_rsi_backtest(data, buy_dates, sell_dates, ticker_name):
        fig = plt.figure(figsize=(14, 10))

        ax1 = plt.subplot(3, 1, 1)
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

        ax2 = plt.subplot(3, 1, 2)
        ax2.plot(data["RSI"], label=f'RSI ({rsi_period})', color="purple")
        ax2.axhline(rsi_upper, linestyle="--", color="red", alpha=0.5)
        ax2.axhline(rsi_lower, linestyle="--", color="green", alpha=0.5)
        ax2.set_ylabel("RSI 값")
        ax2.legend(loc="upper left")

        ax3 = plt.subplot(3, 1, 3)
        ax3.plot(data["MACD"], label="MACD", color="blue")
        ax3.plot(data["Signal"], label="시그널 선", color="red")
        ax3.axhline(0, linestyle="--", color="black", alpha=0.3)
        ax3.set_ylabel("MACD 지표")
        ax3.legend(loc="upper left")

        plt.tight_layout()
        _create_graph_popup(fig, f"{ticker_name} MACD+RSI 백테스트", CHART_HELP.get("macd_rsi", ""))

    def plot_bollinger(data, buy_dates, sell_dates, ticker_name):
        fig, ax = plt.subplots(figsize=(12, 6))
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
        _create_graph_popup(fig, f"{ticker_name} 볼린저밴드 백테스트", CHART_HELP.get("bollinger", ""))

    def plot_ma_cross(data, buy_dates, sell_dates, ticker_name, chart_key="ma_cross"):
        fig, ax = plt.subplots(figsize=(12, 6))
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
        _create_graph_popup(fig, f"{ticker_name} MA교차 백테스트", CHART_HELP.get(chart_key, ""))

    def plot_momentum_with_indicators(data, short_ma, long_ma, upper_band, lower_band, buy_dates, sell_dates, rsi, macd,
                                      signal, ticker_name):
        fig, (ax_price, ax_rsi, ax_macd) = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                                                        gridspec_kw={'height_ratios': [2, 1, 1]})

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

        plt.xlabel("날짜")
        plt.tight_layout()
        _create_graph_popup(fig, f"{ticker_name} 모멘텀 백테스트", CHART_HELP.get("momentum_signal", ""))

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

        plot_macd_backtest(stock_display, close_prices, macd_line, signal_line, buy_signals, sell_signals)
        return [], [], []  # No profit tracking for simple signal display

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

        plot_rsi_backtest(stock_display, close_prices, rsi, buy_signals, sell_signals)
        return [], [], []

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

        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[MACD+RSI] Total return: {total_return:.2%}")
            plot_macd_rsi_backtest(data, buy_dates, sell_dates, stock_display)
        else:
            messagebox.showinfo("알림", f"[{ticker_symbol}] MACD+RSI 전략으로 거래 없음")

        return buy_dates, sell_dates, profits

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
            return [], [], []

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

        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[Bollinger] Total return: {total_return:.2%}")
            plot_bollinger(data, buy_dates, sell_dates, stock_display)
        else:
            logging.info("[Bollinger] No trades")
            messagebox.showerror("데이터 없음", "[볼린저 밴드]를 확인할 수 없습니다. 기간을 더 늘려주세요.")

        return buy_dates, sell_dates, profits

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

        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[MA Cross] Total return: {total_return:.2%}")
            plot_ma_cross(data, buy_dates, sell_dates, stock_display)
        else:
            messagebox.showerror("데이터 없음", "[이동평균 교차]를 확인할 수 없습니다. 기간을 더 늘려주세요.")

        return buy_dates, sell_dates, profits

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

        plot_momentum_with_indicators(data, short_ma, long_ma, upper_band, lower_band, buy_dates, sell_dates,
                                      rsi, macd, signal, stock_display)
        return buy_dates, sell_dates, profits

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

        if profits:
            total_return = (1 + pd.Series(profits)).prod() - 1
            logging.info(f"[Momentum Return + MA] Total return: {total_return:.2%}")
            plot_ma_cross(data, buy_dates, sell_dates, stock_display, chart_key="momentum_return_ma")
        else:
            messagebox.showerror("데이터 없음", "[모멘텀 수익률 + MA 교차] 거래 없음")

        return buy_dates, sell_dates, profits

    # Phase 8-1: Strategy dispatch dictionary
    strategy_dispatch = {
        "macd": _run_macd,
        "rsi": _run_rsi,
        "macd_rsi": _run_macd_rsi,
        "bollinger": _run_bollinger,
        "ma_cross": _run_ma_cross,
        "momentum_signal": _run_momentum_signal,
        "momentum_return_ma": _run_momentum_return_ma,
    }

    def run_backtest(ticker_sym, value, unit, method):
        now = datetime.now()
        if unit == 'd':
            start = now - timedelta(days=value)
        elif unit == 'mo':
            start = now - timedelta(days=value * 30)
        elif unit == 'y':
            start = now - timedelta(days=value * 365)
        else:
            start = now

        # Phase 3-5: Exception handling for yf.download
        try:
            data = _retry_download(ticker_sym, start.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'))
        except (ConnectionError, TimeoutError, OSError) as e:
            messagebox.showerror("네트워크 오류",
                                 f"데이터를 가져올 수 없습니다.\n네트워크 연결을 확인하세요.\n\n{e}")
            return
        except Exception as e:
            messagebox.showerror("다운로드 오류", f"데이터 다운로드 실패: {e}")
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
            messagebox.showerror("데이터 없음",
                                 f"{ticker_sym}에 대한 데이터를 가져올 수 없습니다.\n"
                                 "티커가 올바른지 확인하세요.")
            return

        close_prices = data['Close']

        # Phase 8-1: Dispatch
        _clear_result_area()
        handler = strategy_dispatch.get(method)
        if handler:
            buy_dates, sell_dates, profits = handler(data, close_prices)
            if profits:
                _show_result_summary(profits, buy_dates, sell_dates, close_prices)
        else:
            messagebox.showinfo("알림", f"{method} 전략은 아직 구현되지 않았습니다.")

    # --- Popup UI ---
    popup = tk.Toplevel()
    popup.title(f"{stock} 백테스트")
    # Phase 9-5: Responsive size
    sw = popup.winfo_screenwidth()
    sh = popup.winfo_screenheight()
    pw = min(500, int(sw * 0.35))
    ph = min(450, int(sh * 0.45))
    popup.geometry(f"{pw}x{ph}")
    popup.minsize(400, 350)

    # 결과 표시 전용 프레임 (매 실행 시 내용 교체)
    result_container = tk.Frame(popup)
    result_container.pack(fill=tk.X, side=tk.BOTTOM)

    def _clear_result_area():
        for widget in result_container.winfo_children():
            widget.destroy()

    # Phase 4-1: Cleanup figures on close
    popup.protocol("WM_DELETE_WINDOW", lambda: (cleanup_figures(), popup.destroy()))

    # 팝업 높이 확장 (지표 섹션 추가)
    popup.geometry(f"{pw}x{max(ph, 550)}")
    popup.minsize(400, 450)

    # ── 핵심 지표 섹션 ──
    indicator_frame = tk.LabelFrame(popup, text="핵심 지표", font=("Arial", 10, "bold"))
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

            row1 = tk.Frame(indicator_frame)
            row1.pack(fill=tk.X, padx=8, pady=2)
            for label, value in [
                ("PER", _fmt(info.get("trailingPE"))),
                ("Fwd PER", _fmt(info.get("forwardPE"))),
                ("PBR", _fmt(info.get("priceToBook"))),
            ]:
                tk.Label(row1, text=f"{label}: {value}", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 16))

            row2 = tk.Frame(indicator_frame)
            row2.pack(fill=tk.X, padx=8, pady=2)
            eps_val = info.get("trailingEps")
            div_val = info.get("dividendYield")
            for label, value in [
                ("EPS", _fmt(eps_val, prefix="$") if eps_val is not None else "N/A"),
                ("시가총액", _fmt_cap(info.get("marketCap"))),
                ("배당", _fmt(div_val * 100 if div_val else None, suffix="%")),
            ]:
                tk.Label(row2, text=f"{label}: {value}", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 16))

            row3 = tk.Frame(indicator_frame)
            row3.pack(fill=tk.X, padx=8, pady=2)
            roe_val = info.get("returnOnEquity")
            om_val = info.get("operatingMargins")
            for label, value in [
                ("ROE", _fmt(roe_val * 100 if roe_val else None, suffix="%")),
                ("영업이익률", _fmt(om_val * 100 if om_val else None, suffix="%")),
            ]:
                tk.Label(row3, text=f"{label}: {value}", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 16))

            row4 = tk.Frame(indicator_frame)
            row4.pack(fill=tk.X, padx=8, pady=(2, 4))
            hi = info.get("fiftyTwoWeekHigh")
            lo = info.get("fiftyTwoWeekLow")
            hi_s = f"${hi:.2f}" if hi else "N/A"
            lo_s = f"${lo:.2f}" if lo else "N/A"
            tk.Label(row4, text=f"52주: {lo_s} ~ {hi_s}", font=("Arial", 9)).pack(side=tk.LEFT)

        try:
            popup.after(0, _update_ui)
        except tk.TclError:
            pass

    threading.Thread(target=_load_indicators, daemon=True).start()

    now = datetime.now()
    one_year_ago = now - timedelta(days=365)

    period_range_label = tk.Label(
        popup,
        text=f"분석 기간: {one_year_ago.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')} (1년)",
        font=("Arial", 10),
        fg="#333333"
    )
    period_range_label.pack(pady=5)

    frame = tk.Frame(popup)
    frame.pack(pady=10)

    tk.Label(frame, text="기간 숫자:").grid(row=0, column=0, padx=5)
    period_value_entry = tk.Entry(frame, width=5)
    period_value_entry.grid(row=0, column=1, padx=5)
    period_value_entry.insert(0, config.config["backtest"].get("period", 12))
    period_value_entry.bind("<KeyRelease>", lambda event: update_dates(save=True))
    Tooltip(period_value_entry, BACKTEST_INPUT_HELP["기간 숫자"])

    tk.Label(frame, text="단위:").grid(row=0, column=2, padx=5)
    period_unit_var = tk.StringVar()
    period_unit_menu = ttk.Combobox(frame, textvariable=period_unit_var, values=unit_display_options, width=5,
                                    state="readonly")
    period_unit_menu.grid(row=0, column=3, padx=5)
    saved_unit = config.config["backtest"].get("unit", "mo")
    period_unit_var.set(UNIT_DISPLAY_NAMES.get(saved_unit, saved_unit))
    period_unit_menu.bind("<<ComboboxSelected>>", lambda event: update_dates(save=True))
    Tooltip(period_unit_menu, BACKTEST_INPUT_HELP["단위"])

    tk.Label(frame, text="전략 선택:").grid(row=1, column=0, padx=5)
    method_var = tk.StringVar()
    method_menu = ttk.Combobox(frame, textvariable=method_var, values=strategy_display_options, width=20, state="readonly")
    method_menu.grid(row=1, column=1, columnspan=3, padx=5, pady=5, sticky="w")
    saved_method = config.config["backtest"].get("method", "macd")
    method_var.set(STRATEGY_DISPLAY_NAMES.get(saved_method, saved_method))
    Tooltip(method_menu, BACKTEST_INPUT_HELP["전략 선택"])

    # Phase 12-3: Strategy description label (STRATEGY_HELP 멀티라인)
    strategy_desc_label = tk.Label(popup, text="", font=("Arial", 9), fg="#333333",
                                    justify=tk.LEFT, wraplength=450, anchor="w")
    strategy_desc_label.pack(pady=2, padx=10, fill=tk.X)

    def update_strategy_desc(*args):
        key = _get_method_key()
        desc = STRATEGY_HELP.get(key, STRATEGY_DESCRIPTIONS.get(key, ""))
        strategy_desc_label.config(text=desc)

    method_var.trace_add("write", update_strategy_desc)
    update_strategy_desc()

    btn_frame = tk.Frame(popup)
    btn_frame.pack(pady=10)

    search_btn = tk.Button(btn_frame, text="검색 및 분석", command=save_and_search)
    search_btn.pack(side=tk.LEFT, padx=5)

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
