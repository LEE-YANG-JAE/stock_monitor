import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import yfinance as yf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import config
from stock_score import calculate_bollinger_bands

plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.family'] = 'Malgun Gothic'

strategy_options = ["macd", "rsi", "bollinger", "ma_cross", "momentum"]

def calculate_rsi_for_backtest(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

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

    def plot_rsi_backtest(ticker_symbol, close_prices, rsi, buy_signals, sell_signals):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

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

        ax2.plot(close_prices.index, rsi, label='RSI', color='purple')
        ax2.axhline(70, color='red', linestyle='--', label='과매수 (70)')
        ax2.axhline(30, color='green', linestyle='--', label='과매도 (30)')
        ax2.set_ylabel('RSI 값')
        ax2.set_ylim(0, 100)
        ax2.grid()

        ax1.set_title(f"{ticker_symbol} 백테스트 결과 (RSI 기반)")

        lines_labels = [ax.get_legend_handles_labels() for ax in [ax1, ax2]]
        lines, labels = [sum(lol, []) for lol in zip(*lines_labels)]
        ax1.legend(lines, labels, loc='upper left')

        graph_popup = tk.Toplevel()
        graph_popup.title(f"{ticker_symbol} 백테스트 결과")
        canvas = FigureCanvasTkAgg(fig, master=graph_popup)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        canvas.draw()

    def plot_bollinger(data, buy_dates, sell_dates, ticker_symbol):
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(data.index, data['Close'], label='Close Price', color='black')
        ax.plot(data.index, data['UpperBand'], label='Upper Band', linestyle='--')
        ax.plot(data.index, data['LowerBand'], label='Lower Band', linestyle='--')
        ax.scatter(buy_dates, data.loc[buy_dates]['Close'], marker='^', color='green', label='Buy Signal', s=100)
        ax.scatter(sell_dates, data.loc[sell_dates]['Close'], marker='v', color='red', label='Sell Signal', s=100)
        ax.set_title(f"{ticker_symbol} Bollinger Band Backtest")
        ax.set_xlabel("Date")
        ax.set_ylabel("Price")
        ax.legend()
        ax.grid()
        plt.show()

    def plot_ma_cross(data, buy_dates, sell_dates, ticker_symbol):
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(data.index, data['Close'], label='Close Price', color='black')
        ax.plot(data.index, data['Short_MA'], label='Short MA (5)', linestyle='--')
        ax.plot(data.index, data['Long_MA'], label='Long MA (20)', linestyle='--')
        ax.scatter(buy_dates, data.loc[buy_dates]['Close'], marker='^', color='green', label='Buy Signal', s=100)
        ax.scatter(sell_dates, data.loc[sell_dates]['Close'], marker='v', color='red', label='Sell Signal', s=100)
        ax.set_title(f"{ticker_symbol} Moving Average Cross Backtest")
        ax.set_xlabel("Date")
        ax.set_ylabel("Price")
        ax.legend()
        ax.grid()
        plt.show()

    def plot_momentum_with_indicators(data, short_ma, long_ma, upper_band, lower_band, buy_dates, sell_dates, rsi, macd,
                                      signal, ticker_symbol):
        fig, (ax_price, ax_rsi, ax_macd) = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                                                        gridspec_kw={'height_ratios': [2, 1, 1]})

        # 가격 차트
        ax_price.plot(data.index, data['Close'], label='Close Price', color='black', linewidth=1.5)
        ax_price.plot(data.index, short_ma, label='Short MA (5)', linestyle='--', color='blue', linewidth=1.5)
        ax_price.plot(data.index, long_ma, label='Long MA (20)', linestyle='--', color='orange', linewidth=1.5)
        ax_price.fill_between(data.index, lower_band, upper_band, color='lightgray', alpha=0.3,
                              label='Bollinger Band Area')

        if buy_dates:
            ax_price.scatter(buy_dates, data.loc[buy_dates, 'Close'], marker='^', color='green', label='Buy Signal',
                             s=100, edgecolor='black')
        if sell_dates:
            ax_price.scatter(sell_dates, data.loc[sell_dates, 'Close'], marker='v', color='red', label='Sell Signal',
                             s=100, edgecolor='black')

        # 매수-매도 구간 음영 표시
        for buy, sell in zip(buy_dates, sell_dates):
            ax_price.axvspan(buy, sell, color='lightgreen', alpha=0.3)

        ax_price.set_title(f"{ticker_symbol} Momentum Strategy Backtest", fontsize=16)
        ax_price.set_ylabel("Price")
        ax_price.legend()
        ax_price.grid(linestyle='--', alpha=0.7)

        # RSI 차트
        ax_rsi.plot(data.index, rsi, label='RSI (14)', color='purple')
        ax_rsi.axhline(70, linestyle='--', color='red', alpha=0.5)
        ax_rsi.axhline(30, linestyle='--', color='green', alpha=0.5)
        ax_rsi.set_ylabel("RSI")
        ax_rsi.legend()
        ax_rsi.grid(linestyle='--', alpha=0.7)

        # MACD 차트
        ax_macd.plot(data.index, macd, label='MACD', color='blue')
        ax_macd.plot(data.index, signal, label='Signal Line', color='red')
        ax_macd.set_ylabel("MACD")
        ax_macd.legend()
        ax_macd.grid(linestyle='--', alpha=0.7)

        plt.xlabel("Date")
        plt.tight_layout()
        plt.show()

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

        # MultiIndex 방지: 컬럼과 인덱스 모두 평탄화
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        if isinstance(data.index, pd.MultiIndex):
            data = data.droplevel(0, axis=0)

        if data.empty:
            messagebox.showerror("데이터 없음", f"{ticker_symbol}에 대한 데이터를 가져올 수 없습니다.")
            return
        print("[디버그] 현재 data.columns:", data.columns.tolist())

        close_prices = data['Close']
        print("[디버그] Close 데이터:", close_prices.head())

        match method:
            case "macd":
                macd_short = close_prices.ewm(span=config.config["current_macd"][0], adjust=False).mean()
                macd_long = close_prices.ewm(span=config.config["current_macd"][1], adjust=False).mean()
                macd_line = macd_short - macd_long
                signal_line = macd_line.ewm(span=config.config["current_macd"][2], adjust=False).mean()

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
            case "rsi":
                period = config.config.get("current_rsi", 14)
                rsi = calculate_rsi_for_backtest(close_prices, period)

                buy_signals = []
                sell_signals = []

                for i in range(1, len(rsi)):
                    if rsi.iloc[i].item() < 30:
                        buy_signals.append(i)
                    elif rsi.iloc[i].item() > 70:
                        sell_signals.append(i)

                plot_rsi_backtest(ticker_symbol, close_prices, rsi, buy_signals, sell_signals)
            case "bollinger":
                window = config.config.get("current_bollinger", 20)
                num_std = config.config.get("current_bollinger_window", 2.0)

                ma = close_prices.rolling(window=window).mean()
                std = close_prices.rolling(window=window).std()
                upper_band = ma + (std * num_std)
                lower_band = ma - (std * num_std)

                upper_band_df = pd.DataFrame({'UpperBand': upper_band})
                lower_band_df = pd.DataFrame({'LowerBand': lower_band})
                print(pd.concat([upper_band_df, lower_band_df], axis=1).head())

                data['MA'] = ma
                data['STD'] = std
                data['UpperBand'] = upper_band
                data['LowerBand'] = lower_band

                expected_cols = ['LowerBand', 'UpperBand']
                if all(col in data.columns for col in expected_cols):
                    data = data.dropna(subset=expected_cols)
                else:
                    print(f"[경고] {expected_cols} 컬럼이 존재하지 않습니다. 현재 컬럼들: {data.columns.tolist()}")

                use_rebound_confirmation = config.config.get("current_bollinger_use_rebound", False)
                buy_dates = []
                sell_dates = []
                in_position = False
                entry_price = 0
                profits = []

                if use_rebound_confirmation:
                    # 반등 검증 모드
                    for i in range(len(data) - 2):
                        if not in_position:
                            if data['Close'].iloc[i] < data['LowerBand'].iloc[i]:
                                # 다음날 종가가 상승했는지만 확인
                                if data['Close'].iloc[i + 1] > data['Close'].iloc[i]:
                                    in_position = True
                                    entry_price = data['Close'].iloc[i + 1]
                                    buy_dates.append(data.index[i + 1])
                        else:
                            if data['Close'].iloc[i] > data['UpperBand'].iloc[i]:
                                if data['Close'].iloc[i + 1] < data['Close'].iloc[i]:
                                    exit_price = data['Close'].iloc[i + 1]
                                    profit = (exit_price - entry_price) / entry_price
                                    profits.append(profit)
                                    sell_dates.append(data.index[i + 1])
                                    in_position = False
                    if in_position:
                        exit_price = data['Close'].iloc[-1]
                        profit = (exit_price - entry_price) / entry_price
                        profits.append(profit)
                else:
                    # 기존 터치 방식
                    buy_signal = data['Close'] < data['LowerBand']
                    sell_signal = data['Close'] > data['UpperBand']

                    for i in range(len(data)):
                        if not in_position and buy_signal.iloc[i]:
                            in_position = True
                            entry_price = data['Close'].iloc[i]
                            buy_dates.append(data.index[i])
                        elif in_position and sell_signal.iloc[i]:
                            exit_price = data['Close'].iloc[i]
                            profit = (exit_price - entry_price) / entry_price
                            profits.append(profit)
                            sell_dates.append(data.index[i])
                            in_position = False
                    if in_position:
                        exit_price = data['Close'].iloc[-1]
                        profit = (exit_price - entry_price) / entry_price
                        profits.append(profit)

                # 수익률 계산 및 출력
                if profits:
                    total_return = (1 + pd.Series(profits)).prod() - 1
                    print(f"[볼린저 밴드] 총 수익률: {total_return:.2%}")
                    plot_bollinger(data, buy_dates, sell_dates, ticker_symbol)
                else:
                    print("[볼린저 밴드] 거래 없음")
                    messagebox.showerror("데이터 없음", f"[볼린저 밴드]를 확인할 수 없습니다. 기간을 더 늘려주세요.")
            case "ma_cross":
                # 단기 이동평균선(5일)과 장기 이동평균선(20일)을 계산
                short_window = 5
                long_window = 20

                short_ma = data['Close'].rolling(window=short_window).mean()
                long_ma = data['Close'].rolling(window=long_window).mean()

                print("[디버그] 단기/장기 이동평균 생성 완료:", short_ma.head(), long_ma.head())

                # Short_MA, Long_MA 컬럼 데이터프레임에 추가
                data['Short_MA'] = short_ma
                data['Long_MA'] = long_ma

                # 매수 조건: 단기 이동평균선이 장기 이동평균선을 상향 돌파
                buy_signal = short_ma > long_ma
                sell_signal = short_ma < long_ma

                in_position = False
                entry_price = 0
                profits = []
                buy_dates = []
                sell_dates = []

                # 거래 진행
                for i in range(len(data)):
                    if not in_position and buy_signal.iloc[i]:
                        in_position = True
                        entry_price = data['Close'].iloc[i]
                        buy_dates.append(data.index[i])
                    elif in_position and sell_signal.iloc[i]:
                        exit_price = data['Close'].iloc[i]
                        profit = (exit_price - entry_price) / entry_price
                        profits.append(profit)
                        sell_dates.append(data.index[i])
                        in_position = False

                # 종료 시 남아있는 포지션 처리
                if in_position:
                    exit_price = data['Close'].iloc[-1]
                    profit = (exit_price - entry_price) / entry_price
                    profits.append(profit)

                # 수익률 출력
                if profits:
                    total_return = (1 + pd.Series(profits)).prod() - 1
                    print(f"[이동평균 교차] 총 수익률: {total_return:.2%}")

                    # 그래프 표시
                    plot_ma_cross(data, buy_dates, sell_dates, ticker_symbol)

                else:
                    messagebox.showerror("데이터 없음", f"[이동평균 교차]를 확인할 수 없습니다. 기간을 더 늘려주세요.")
            case 'momentum':
                # 사용자 config 값 가져오기
                macd_short_span = config.config['current_macd'][0]
                macd_long_span = config.config['current_macd'][1]
                macd_signal_span = config.config['current_macd'][2]
                rsi_period = config.config.get('current_rsi', 14)
                bb_window = config.config.get('current_bollinger', 20)
                bb_num_std = config.config.get('current_bollinger_window', 2.0)
                use_rebound_confirmation = config.config.get("current_bollinger_use_rebound", False)

                # RSI 계산
                rsi = calculate_rsi_for_backtest(data['Close'], period=rsi_period)

                # 볼린저 밴드 계산
                rolling_mean = data['Close'].rolling(window=bb_window).mean()
                rolling_std = data['Close'].rolling(window=bb_window).std()
                upper_band = rolling_mean + (rolling_std * bb_num_std)
                lower_band = rolling_mean - (rolling_std * bb_num_std)

                # MACD 계산
                ema_short = data['Close'].ewm(span=macd_short_span, adjust=False).mean()
                ema_long = data['Close'].ewm(span=macd_long_span, adjust=False).mean()
                macd = ema_short - ema_long
                signal = macd.ewm(span=macd_signal_span, adjust=False).mean()

                # 단기/장기 이동평균선
                short_ma = data['Close'].rolling(window=5).mean()
                long_ma = data['Close'].rolling(window=20).mean()

                # 시그널 생성
                macd_signal_series = pd.Series(np.where(macd > signal, "BUY", "SELL"), index=data.index)
                ma_signal_series = pd.Series(np.where(short_ma > long_ma, "BUY", "SELL"), index=data.index)

                # 볼린저밴드 시그널 생성 (반등 검증 반영)
                bb_signal_list = []
                for i in range(len(data) - 1):
                    if use_rebound_confirmation:
                        if data['Close'].iloc[i] < lower_band.iloc[i]:
                            if data['Close'].iloc[i + 1] > data['Close'].iloc[i]:  # 1일 반등 검증
                                bb_signal_list.append("BUY")
                            else:
                                bb_signal_list.append("HOLD")
                        elif data['Close'].iloc[i] > upper_band.iloc[i]:
                            if data['Close'].iloc[i + 1] < data['Close'].iloc[i]:  # 1일 하락 검증
                                bb_signal_list.append("SELL")
                            else:
                                bb_signal_list.append("HOLD")
                        else:
                            bb_signal_list.append("HOLD")
                    else:
                        if data['Close'].iloc[i] < lower_band.iloc[i]:
                            bb_signal_list.append("BUY")
                        elif data['Close'].iloc[i] > upper_band.iloc[i]:
                            bb_signal_list.append("SELL")
                        else:
                            bb_signal_list.append("HOLD")
                bb_signal_list.append("HOLD")  # 마지막 행 추가 (index error 방지)
                bb_signal_series = pd.Series(bb_signal_list, index=data.index)

                rsi_signal_series = pd.Series(np.where(rsi < 30, "BUY",
                                                       np.where(rsi > 70, "SELL", "HOLD")),
                                              index=data.index)

                # 종합 시그널 계산
                combined_signal = []
                for i in range(len(data)):
                    score = 0
                    if macd_signal_series.iloc[i] == "BUY":
                        score += 2
                    elif macd_signal_series.iloc[i] == "SELL":
                        score -= 2
                    if ma_signal_series.iloc[i] == "BUY":
                        score += 1
                    elif ma_signal_series.iloc[i] == "SELL":
                        score -= 1
                    if bb_signal_series.iloc[i] == "BUY":
                        score += 1
                    elif bb_signal_series.iloc[i] == "SELL":
                        score -= 1
                    if rsi_signal_series.iloc[i] == "BUY":
                        score += 1
                    elif rsi_signal_series.iloc[i] == "SELL":
                        score -= 1

                    if score >= 4:
                        combined_signal.append("STRONG BUY")
                    elif score >= 2:
                        combined_signal.append("BUY")
                    elif score <= -4:
                        combined_signal.append("STRONG SELL")
                    elif score <= -2:
                        combined_signal.append("SELL")
                    else:
                        combined_signal.append("HOLD")

                combined_signal_series = pd.Series(combined_signal, index=data.index)

                # 결과 저장용
                in_position = False
                entry_price = 0
                profits = []
                buy_dates = []
                sell_dates = []

                for i in range(len(data)):
                    signal_now = combined_signal_series.iloc[i]
                    if not in_position and (signal_now == "BUY" or signal_now == "STRONG BUY"):
                        in_position = True
                        entry_price = data['Close'].iloc[i]
                        buy_dates.append(data.index[i])

                    elif in_position and (signal_now == "SELL" or signal_now == "STRONG SELL"):
                        exit_price = data['Close'].iloc[i]
                        profit = (exit_price - entry_price) / entry_price
                        profits.append(profit)
                        sell_dates.append(data.index[i])
                        in_position = False

                if in_position:
                    exit_price = data['Close'].iloc[-1]
                    profit = (exit_price - entry_price) / entry_price
                    profits.append(profit)

                if profits:
                    total_return = (1 + pd.Series(profits)).prod() - 1
                    print(f"[모멘텀] 총 수익률: {total_return:.2%}")
                else:
                    print("[모멘텀] 거래 없음")

                # 그래프 출력
                plot_momentum_with_indicators(data, short_ma, long_ma, upper_band, lower_band, buy_dates, sell_dates,
                                              rsi, macd, signal, ticker_symbol)
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
