import tkinter as tk
from datetime import datetime
from tkinter import simpledialog, messagebox, ttk
import threading
import time
import json
import os
import numpy as np
import pytz
import yfinance as yf
from stock_score import fetch_stock_data, calculate_rsi, calculate_moving_average, is_market_open
import re
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt

# 다중 종목 감시용 GUI
watchlist = []
SAVE_FILE = "watchlist.json"


# 종목 추가 함수 (티커 입력 기반, yfinance 검색)
def add_ticker():
    name_or_ticker = simpledialog.askstring("종목 추가", "추가할 종목 티커를 입력하세요 (예: NVDA, TSLA)")
    if name_or_ticker:
        name_or_ticker = name_or_ticker.upper()
        try:
            ticker_info = yf.Ticker(name_or_ticker).info
            company_name = ticker_info.get('shortName')
            if company_name:
                if name_or_ticker not in watchlist:
                    watchlist.append(name_or_ticker.strip())
                    save_watchlist()  # watchlist를 파일에 저장
                    messagebox.showinfo("추가 완료", f"{company_name} ({name_or_ticker}) 추가되었습니다.")
                    refresh_table_once()  # 추가된 종목을 반영한 테이블 새로고침
                else:
                    messagebox.showinfo("중복", f"{name_or_ticker} 는 이미 감시 중입니다.")
            else:
                messagebox.showwarning("검색 실패", f"{name_or_ticker}에 대한 정보를 찾을 수 없습니다.")
        except Exception as e:
            print(f"add_ticker error: {e}")
            messagebox.showwarning("검색 실패", f"{name_or_ticker} 정보를 가져오는 중 오류가 발생했습니다.")


# 종목 삭제 함수
def remove_ticker():
    selected_item = table.selection()
    if selected_item:
        for item in selected_item:
            company_name_with_ticker = table.item(item)["values"][0]  # 회사명은 첫 번째 컬럼에 있음
            match = re.search(r'\((.*?)\)', company_name_with_ticker)
            if match:
                ticker = match.group(1)  # 티커 추출
                if ticker in watchlist:
                    watchlist.remove(ticker)
                    save_watchlist()
                    messagebox.showinfo("삭제 완료", f"{company_name_with_ticker} 삭제되었습니다.")
                    refresh_table_once()
                else:
                    messagebox.showwarning("없음", f"{ticker} 은 감시 리스트에 없습니다.")
            else:
                messagebox.showwarning("형식 오류", f"티커를 추출할 수 없습니다: {company_name_with_ticker}")
    else:
        messagebox.showwarning("선택 오류", "올바른 항목을 선택해주세요.")


# 감시 리스트 저장 함수
def save_watchlist():
    try:
        with open(SAVE_FILE, "w") as f:
            json.dump(watchlist, f)  # watchlist를 JSON 파일로 저장
    except Exception as e:
        print(f"Error saving watchlist: {e}")


# 감시 리스트 로드 함수
def load_watchlist():
    global watchlist
    try:
        if os.path.exists(SAVE_FILE):
            with open(SAVE_FILE, "r") as f:
                watchlist = json.load(f)
    except Exception as e:
        print(f"Error loading watchlist: {e}")


# 테이블 즉시 새로고침 함수
def refresh_table_once():
    try:
        results = []
        for ticker in watchlist:
            result = fetch_stock_data(ticker)
            if result:  # None이 아닌 데이터만 추가
                results.append(result)
        update_table(results)  # 테이블을 갱신
    except Exception as e:
        print(f"refresh_table_once error: {e}")


# 주식 데이터 주기적으로 갱신
def monitor_stocks(update_table_func):
    while True:
        if is_market_open():
            # 장중일 경우 1분 간격으로 데이터 갱신
            print("시장 열림 - 데이터 갱신 중...")
            # 데이터 갱신 코드 삽입 (예: `fetch_stock_data` 호출)
        else:
            # 장 종료 후에는 데이터 갱신을 하지 않음
            print("시장 종료 - 데이터 갱신 중지")
            break  # 장 종료 후에는 데이터 갱신을 멈추도록 종료

        time.sleep(60)  # 1분 간격으로 실행

# 주식 시장 상태를 표시할 라벨 추가
def update_market_status():
    # Get current times
    korea_timezone = pytz.timezone('Asia/Seoul')
    new_york_timezone = pytz.timezone('America/New_York')

    korea_time = datetime.now(korea_timezone).strftime("%Y-%m-%d %H:%M:%S")
    new_york_time = datetime.now(new_york_timezone).strftime("%Y-%m-%d %H:%M:%S")

    # Get market status
    if is_market_open():
        status = "주식장 중"
    else:
        status = "주식장 종료"

    # Construct the full text
    full_text = f"{status}\n한국 시간: {korea_time}\n미국 시간: {new_york_time}"

    # Update the market status label with color (only change market status color)
    market_status_label.config(
        text=full_text,
    )
    # Update every 1000 milliseconds (1 second)
    root.after(1000, update_market_status)  # Update every 1 second


def on_item_double_click(event):
    selected_item = table.selection()[0]
    ticker = table.item(selected_item)['values'][0].split('(')[-1].split(')')[0]  # Extract ticker

    # Fetch stock data for the selected ticker with a longer period
    data = fetch_stock_data(ticker)  # Fetch data for 1 year to ensure sufficient historical data
    if data is None:
        return

    # Unpack the data
    _, _, _, _, _, _, _, macd_signal, signal_line, macd_histogram, upper_band, lower_band, middle_band = data

    # Debugging output
    print(f"MACD Signal: {macd_signal}")
    print(f"Signal Line: {signal_line}")
    print(f"MACD Histogram: {macd_histogram}")
    print(f"Upper Band: {upper_band}")
    print(f"Lower Band: {lower_band}")
    print(f"Middle Band: {middle_band}")

    # Check if upper_band and lower_band are scalar values (numpy.float64)
    if isinstance(upper_band, (float, int, np.float64)):
        upper_band = [upper_band]  # Wrap in a list if it's a scalar value

    if isinstance(lower_band, (float, int, np.float64)):
        lower_band = [lower_band]  # Wrap in a list if it's a scalar value

    # MACD Histogram should be an array of values, not a single float
    if isinstance(macd_histogram, float):
        macd_histogram = [macd_histogram]  # Convert to list if it's a single float

    # Create a new window to show the graphs
    graph_window = tk.Toplevel()
    graph_window.title(f"{ticker} MACD and Bollinger Bands")

    # Create the figure and axes
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))

    # MACD plot
    ax1.plot(macd_signal, label='MACD Line', color='blue')
    ax1.plot(signal_line, label='Signal Line', color='orange')
    ax1.bar(range(len(macd_histogram)), macd_histogram, label='MACD Histogram', color='gray', alpha=0.5)
    ax1.set_title(f"{ticker} - MACD")
    ax1.legend()

    # Bollinger Bands plot
    ax2.plot(upper_band, label='Upper Band', color='red', linestyle='--')
    ax2.plot(lower_band, label='Lower Band', color='green', linestyle='--')
    ax2.plot(middle_band, label='Middle Band (Moving Average)', color='blue')
    ax2.fill_between(range(len(upper_band)), upper_band, lower_band, color='yellow', alpha=0.2)
    ax2.set_title(f"{ticker} - Bollinger Bands")
    ax2.legend()

    # Convert Matplotlib figure to Tkinter canvas
    canvas = FigureCanvasTkAgg(fig, master=graph_window)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)  # Ensure it's packed to fill the window

    # Display the graphs
    canvas.draw()


# 테이블에서 매수/매도/보류 신호 표시 및 그래프 표시 추가
def update_table(data):
    try:
        for row in table.get_children():
            table.delete(row)

        for record in data:
            if record:  # Ensure we have 13 values in the record
                # Unpack the data record
                (
                    name,
                    ticker,
                    price,
                    trend,
                    rsi,
                    rate,
                    rate_color,
                    macd_signal,
                    bb_signal
                ) = record

                # Prepare the data for display
                trend_display = trend
                rsi_value = float(rsi.replace('%', ''))  # RSI 값 처리
                if rsi_value > 70:
                    rsi_display = f"{rsi} (과매수)"
                elif rsi_value < 30:
                    rsi_display = f"{rsi} (과매도)"
                else:
                    rsi_display = f"{rsi} (중립)"

                # Insert the data into the table
                row_id = table.insert("", "end", values=(
                    f"{name} ({ticker})",  # Stock name and ticker
                    price,  # Current price
                    trend_display,  # Trend signal (BUY, SELL, HOLD)
                    rsi_display,  # RSI signal
                    rate,  # Rate of change
                    macd_signal,  # MACD signal
                    bb_signal  # Signal line value
                ))

                # Set color for the rate
                table.tag_configure(f"rate_{row_id}", foreground=rate_color)
                table.item(row_id, tags=(f"rate_{row_id}",))

        # Dynamically adjust column width
        min_widths = {
            "종목명": 150,
            "현재가": 100,
            "추세 신호": 200,
            "RSI 신호": 150,
            "수익률": 100,
            "MACD 신호": 150,
            "BB 신호": 150
        }

        for col, width in min_widths.items():
            table.column(col, width=width, minwidth=width)

    except Exception as e:
        print(f"update_table error: {e}")



# 테이블 및 기타 UI 요소
# 테이블 및 기타 UI 요소
def main():
    global root, table, market_status_label, time_label

    root = tk.Tk()
    root.title("미국 주식 실시간 모니터링(매 1분)")

    market_status_label = tk.Label(root, text="주식장 종료\n한국 시간: 2025-04-27 11:54:29\n미국 시간: 2025-04-26 22:54:29",
                                   font=("Arial", 14))
    market_status_label.pack(pady=10)

    # Button frame for adding/removing tickers
    button_frame = tk.Frame(root)
    button_frame.pack(pady=10)

    add_button = tk.Button(button_frame, text="종목 추가", command=add_ticker)
    add_button.pack(side=tk.LEFT, padx=10)

    remove_button = tk.Button(button_frame, text="종목 삭제", command=remove_ticker)
    remove_button.pack(side=tk.LEFT, padx=10)

    # UI 초기화
    table_frame = tk.Frame(root)
    table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    columns = ("종목명", "현재가", "추세 신호", "RSI 신호", "수익률", "MACD 신호", "BB 신호")
    table = ttk.Treeview(table_frame, columns=columns, show="headings")
    vsb = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
    hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=table.xview)
    table.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    table.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")

    min_widths = {
        "종목명": 150,
        "현재가": 100,
        "추세 신호": 200,
        "RSI 신호": 150,
        "수익률": 100,
        "MACD 신호": 150,
        "BB 신호": 150
    }

    for col in columns:
        table.heading(col, text=col)
        table.column(col, width=min_widths[col], minwidth=min_widths[col], anchor="center")

    table.tag_configure("buy", background="#e0ffe0")
    table.tag_configure("sell", background="#ffe0e0")
    table.tag_configure("hold", background="#f0f0f0")

    # 데이터 로드 및 테이블 갱신
    load_watchlist()  # watchlist 로드
    refresh_table_once()  # 테이블 한 번 갱신

    # 주식 감시 목록을 계속 모니터링
    threading.Thread(target=monitor_stocks, args=(update_table,), daemon=True).start()

    # 장 상태 갱신 시작
    update_market_status()

    # Bind double-click event to table for opening graph
    table.bind("<Double-1>", on_item_double_click)

    root.mainloop()


if __name__ == "__main__":
    main()
