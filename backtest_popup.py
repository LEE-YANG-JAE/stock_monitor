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

            def _safe_get(key):
                v = info.get(key)
                if v is None:
                    return None
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return None

            per = _safe_get("trailingPE")
            fwd_per = _safe_get("forwardPE")
            pbr = _safe_get("priceToBook")
            peg = _safe_get("pegRatio")
            eps_val = _safe_get("trailingEps")
            div_val = _safe_get("dividendYield")
            roe_val = _safe_get("returnOnEquity")
            om_val = _safe_get("operatingMargins")
            ev_ebitda = _safe_get("enterpriseToEbitda")
            debt_equity = _safe_get("debtToEquity")
            current_ratio = _safe_get("currentRatio")
            rev_growth = _safe_get("revenueGrowth")
            earn_growth = _safe_get("earningsGrowth")

            # PEG 직접 계산 fallback: PER ÷ (이익성장률 × 100)
            peg_calculated = False
            if peg is None and per is not None and earn_growth is not None:
                eg_pct = earn_growth * 100
                if eg_pct > 0:
                    peg = per / eg_pct
                    peg_calculated = True
            fcf = _safe_get("freeCashflow")
            current_price = _safe_get("currentPrice")
            hi = _safe_get("fiftyTwoWeekHigh")
            lo = _safe_get("fiftyTwoWeekLow")
            book_val = _safe_get("bookValue")

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

            # --- 저평가/고평가 판단 (점수제) ---
            score = 0
            criteria = {}  # name -> +1(저평가), -1(고평가), 0(중립)

            if per is not None:
                if per <= 15:
                    criteria["PER"] = 1
                elif per >= 30:
                    criteria["PER"] = -1
                else:
                    criteria["PER"] = 0
            else:
                criteria["PER"] = None

            if pbr is not None:
                if pbr <= 1.5:
                    criteria["PBR"] = 1
                elif pbr >= 5:
                    criteria["PBR"] = -1
                else:
                    criteria["PBR"] = 0
            else:
                criteria["PBR"] = None

            if peg is not None:
                if peg <= 1.0:
                    criteria["PEG"] = 1
                elif peg >= 2.0:
                    criteria["PEG"] = -1
                else:
                    criteria["PEG"] = 0
            else:
                criteria["PEG"] = None

            if ev_ebitda is not None:
                if ev_ebitda <= 10:
                    criteria["EV/EBITDA"] = 1
                elif ev_ebitda >= 20:
                    criteria["EV/EBITDA"] = -1
                else:
                    criteria["EV/EBITDA"] = 0
            else:
                criteria["EV/EBITDA"] = None

            if roe_val is not None:
                roe_pct = roe_val * 100
                if roe_pct >= 15:
                    criteria["ROE"] = 1
                elif roe_pct < 5:
                    criteria["ROE"] = -1
                else:
                    criteria["ROE"] = 0
            else:
                criteria["ROE"] = None

            if debt_equity is not None:
                if debt_equity < 100:
                    criteria["부채"] = 1
                elif debt_equity >= 200:
                    criteria["부채"] = -1
                else:
                    criteria["부채"] = 0
            else:
                criteria["부채"] = None

            if hi is not None and current_price is not None and hi > 0:
                drop_pct = (hi - current_price) / hi * 100
                if drop_pct >= 20:
                    criteria["52주"] = 1
                elif drop_pct <= 5:
                    criteria["52주"] = -1
                else:
                    criteria["52주"] = 0
            else:
                criteria["52주"] = None

            valid_scores = [v for v in criteria.values() if v is not None]
            score = sum(valid_scores)
            total_criteria = len(valid_scores)

            # --- 적정 가격 계산 ---
            fair_prices = []
            # 1) PER 기반: EPS × 업종평균PER (없으면 15)
            if eps_val is not None and eps_val > 0:
                sector_per = _safe_get("sectorPE") or _safe_get("industryPE") or 15
                fair_prices.append(eps_val * sector_per)

            # 2) PBR 기반: bookValue × 1.0
            if book_val is not None and book_val > 0:
                fair_prices.append(book_val * 1.0)

            # 3) DCF 간이: EPS × (1+earningsGrowth)^5 × 15 / (1.1^5)
            if eps_val is not None and eps_val > 0 and earn_growth is not None:
                growth = max(earn_growth, -0.5)  # 성장률 하한
                future_eps = eps_val * ((1 + growth) ** 5)
                dcf_price = future_eps * 15 / (1.1 ** 5)
                if dcf_price > 0:
                    fair_prices.append(dcf_price)

            fair_price = sum(fair_prices) / len(fair_prices) if fair_prices else None

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

    # ── 종목 뉴스 버튼 ──
    def open_ticker_news_popup():
        from news_panel import fetch_ticker_news

        news_popup = tk.Toplevel(popup)
        news_popup.title(f"{stock_display} 뉴스")
        news_popup.geometry("620x500")
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
