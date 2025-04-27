import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import yfinance as yf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import config

plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.family'] = 'Malgun Gothic'

strategy_options = ["macd", "rsi", "bollinger", "ma_cross", "momentum"]

def open_backtest_popup(stock, on_search_callback=None):
    ticker_symbol = stock.split('(')[-1].split(')')[0]

    def update_dates(save=False):
        try:
            value_text = period_value_entry.get()
            if value_text.isdigit():
                value = int(value_text)
            else:
                return

            unit = period_unit_var.get()
            now = datetime.now()
            if unit == 'd':
                start = now - timedelta(days=value)
            elif unit == 'mo':
                start = now - timedelta(days=value * 30)
            elif unit == 'y':
                start = now - timedelta(days=value * 365)
            else:
                start = now
            start_label.config(text=f"시작일: {start.strftime('%Y-%m-%d')}")
            end_label.config(text=f"종료일: {now.strftime('%Y-%m-%d')}")

            if save:
                config.config["backtest"]["period_value"] = value
                config.config["backtest"]["period_unit"] = unit
                config.save_config(config.config)

        except ValueError:
            pass

    def save_and_search():
        value_text = period_value_entry.get().strip()
        if not value_text.isdigit():
            messagebox.showerror("오류", "기간 숫자는 정수로 입력하세요.")
            return

        value = int(value_text)
        unit = period_unit_var.get()
        method = method_var.get()

        if unit not in ('d', 'mo', 'y'):
            messagebox.showerror("오류", "기간 단위를 d, mo, y 중 하나로 선택하세요.")
            return

        config.config["backtest"]["period_value"] = value
        config.config["backtest"]["period_unit"] = unit
        config.config["backtest"]["method"] = method
        config.save_config(config.config)

        run_backtest(ticker_symbol, value, unit, method)

    def plot_macd_backtest(ticker_symbol, close_prices, macd_line, signal_line, buy_signals, sell_signals):
        fig, ax1 = plt.subplots(figsize=(10, 6))

        ax1.plot(close_prices.index, close_prices, label='주가 (Close Price)', color='black')
        ax1.set_ylabel('가격 ($)')
        ax1.yaxis.set_major_formatter(ticker.FormatStrFormatter('$%.2f'))
        ax1.grid()

        for idx, buy in enumerate(buy_signals):
            ax1.scatter(close_prices.index[buy], close_prices.iloc[buy], marker='^', color='green',
                        label='Buy Signal' if idx == 0 else "")
        for idx, sell in enumerate(sell_signals):
            ax1.scatter(close_prices.index[sell], close_prices.iloc[sell], marker='v', color='red',
                        label='Sell Signal' if idx == 0 else "")

        ax2 = ax1.twinx()
        ax2.plot(close_prices.index, macd_line, label='MACD Line', color='blue')
        ax2.plot(close_prices.index, signal_line, label='Signal Line', color='orange')
        ax2.set_ylabel('MACD 지표')

        ax1.set_title(f"{ticker_symbol} 백테스트 결과 (MACD 교차 기반)")

        lines_labels = [ax.get_legend_handles_labels() for ax in [ax1, ax2]]
        lines, labels = [sum(lol, []) for lol in zip(*lines_labels)]
        ax1.legend(lines, labels, loc='upper left')

        graph_popup = tk.Toplevel()
        graph_popup.title(f"{ticker_symbol} 백테스트 결과")
        canvas = FigureCanvasTkAgg(fig, master=graph_popup)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        canvas.draw()

    def run_backtest(ticker_symbol, value, unit, method):
        now = datetime.now()
        if unit == 'd':
            start = now - timedelta(days=value)
        elif unit == 'mo':
            start = now - timedelta(days=value * 30)
        elif unit == 'y':
            start = now - timedelta(days=value * 365)
        else:
            start = now

        data = yf.download(ticker_symbol, start=start.strftime('%Y-%m-%d'), end=now.strftime('%Y-%m-%d'))
        if data.empty:
            messagebox.showerror("데이터 없음", f"{ticker_symbol}에 대한 데이터를 가져올 수 없습니다.")
            return

        close_prices = data['Close']

        match method:
            case "macd":
                macd_short = close_prices.ewm(span=12, adjust=False).mean()
                macd_long = close_prices.ewm(span=26, adjust=False).mean()
                macd_line = macd_short - macd_long
                signal_line = macd_line.ewm(span=9, adjust=False).mean()

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

                plot_macd_backtest(ticker_symbol, close_prices, macd_line, signal_line, buy_signals, sell_signals)
            case _:
                messagebox.showinfo("알림", f"{method} 전략은 아직 구현되지 않았습니다.")

    popup = tk.Toplevel()
    popup.title(f"{stock} 백테스트")
    popup.geometry("450x350")

    now = datetime.now()
    one_year_ago = now - timedelta(days=365)

    start_label = tk.Label(popup, text=f"시작일: {one_year_ago.strftime('%Y-%m-%d')}")
    start_label.pack(pady=5)
    end_label = tk.Label(popup, text=f"종료일: {now.strftime('%Y-%m-%d')}")
    end_label.pack(pady=5)

    frame = tk.Frame(popup)
    frame.pack(pady=10)

    tk.Label(frame, text="기간 숫자:").grid(row=0, column=0, padx=5)
    period_value_entry = tk.Entry(frame, width=5)
    period_value_entry.grid(row=0, column=1, padx=5)
    period_value_entry.insert(0, config.config["backtest"].get("period_value", 12))
    period_value_entry.bind("<KeyRelease>", lambda event: update_dates(save=True))

    tk.Label(frame, text="단위:").grid(row=0, column=2, padx=5)
    period_unit_var = tk.StringVar()
    period_unit_menu = ttk.Combobox(frame, textvariable=period_unit_var, values=["d", "mo", "y"], width=5,
                                    state="readonly")
    period_unit_menu.grid(row=0, column=3, padx=5)
    period_unit_var.set(config.config["backtest"].get("period_unit", "mo"))
    period_unit_menu.bind("<<ComboboxSelected>>", lambda event: update_dates(save=True))

    tk.Label(frame, text="전략 선택:").grid(row=1, column=0, padx=5)
    method_var = tk.StringVar()
    method_menu = ttk.Combobox(frame, textvariable=method_var, values=strategy_options, width=10, state="readonly")
    method_menu.grid(row=1, column=1, columnspan=3, padx=5, pady=5, sticky="w")
    method_var.set(config.config["backtest"].get("method", "macd"))

    search_btn = tk.Button(popup, text="검색 및 분석", command=save_and_search)
    search_btn.pack(pady=10)

    close_btn = tk.Button(popup, text="닫기", command=popup.destroy)
    close_btn.pack(pady=5)

    update_dates()

# 이 파일은 stock_monitor_gui.py에서 import 해서 사용하게 됨
