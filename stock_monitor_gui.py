import json
import os
import re
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from tkinter import simpledialog, messagebox, ttk

import pytz
import yfinance as yf
import copy

import config
from backtest_popup import open_backtest_popup
from market_trend_manager import guess_market_session
from stock_score import fetch_stock_data

# 다중 종목 감시용 GUI
watchlist = []
SAVE_FILE = "watchlist.json"


# 설정 다시 불러오기 버튼 추가 함수
def add_reload_button(parent_frame):
    top_bar_frame = tk.Frame(parent_frame)
    top_bar_frame.pack(fill=tk.X, pady=5, padx=10)
    reload_btn = tk.Button(top_bar_frame, text="↻ 설정 다시 불러오기", command=reload_config, font=("Arial", 10))
    reload_btn.pack(side=tk.RIGHT, padx=10, anchor="ne")


def reload_config():
    config.config = config.load_config()
    refresh_table()
    messagebox.showinfo("설정 불러오기", "설정이 다시 불러와졌습니다.")


def on_radio_select():
    selected_value = radio_var.get()

    # 선택된 값에 맞는 데이터 요청 방식 변경
    if selected_value == "short":
        config.config["current"]["period"] = config.config["settings"]["short"]["period"]
        config.config["current"]["rsi"] = config.config["settings"]["short"]["rsi"]
        config.config["current"]["macd"] = copy.deepcopy(config.config["settings"]["short"]["macd"])
        config.config["current"]["bollinger"] = copy.deepcopy(config.config["settings"]["short"]["bollinger"])
        print("단기 데이터가 선택되었습니다.")

    elif selected_value == "long":
        config.config["current"]["period"] = config.config["settings"]["long"]["period"]
        config.config["current"]["rsi"] = config.config["settings"]["long"]["rsi"]
        config.config["current"]["macd"] = copy.deepcopy(config.config["settings"]["long"]["macd"])
        config.config["current"]["bollinger"] = copy.deepcopy(config.config["settings"]["long"]["bollinger"])
        print("장기 데이터가 선택되었습니다.")

    # 설정을 저장
    config.config["view_mode"] = selected_value  # 선택된 데이터 유형을 저장
    config.save_config(config.config)

    # 주식 데이터를 다시 불러오고 테이블 갱신
    refresh_table()


def refresh_table():
    # 테이블의 내용을 지우고 새로 데이터를 갱신합니다.
    for row in table.get_children():
        table.delete(row)

    refresh_table_once()


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

        def fetch_and_collect(ticker):
            result = fetch_stock_data(ticker)
            if result:
                results.append(result)

        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(fetch_and_collect, watchlist)

        update_table(results)  # 테이블을 갱신
    except Exception as e:
        print(f"refresh_table_once error: {e}")


# 주식 데이터 주기적으로 갱신
def monitor_stocks():
    time.sleep(60)
    while True:
        try:
            refresh_table_once()
        except Exception as e:
            print(f"monitor_stocks error: {e}")

        session = guess_market_session()
        if session != "주식장 종료":
            # 장중일 경우 1분 간격으로 데이터 갱신
            print(f'{session} - 데이터 갱신 중...')
            time.sleep(60)  # 1분 간격으로 실행
        else:
            print("시장 종료 - 데이터 갱신 중단...")
            break


# 주식 시장 상태를 표시할 라벨 추가
def update_market_status():
    # Get current times
    korea_timezone = pytz.timezone('Asia/Seoul')
    new_york_timezone = pytz.timezone('America/New_York')

    korea_time = datetime.now(korea_timezone).strftime("%Y-%m-%d %H:%M:%S")
    new_york_time = datetime.now(new_york_timezone).strftime("%Y-%m-%d %H:%M:%S")

    status = guess_market_session()
    # Construct the full text
    full_text = f"{status}\n한국 시간: {korea_time}\n미국 시간: {new_york_time}"

    # Update the market status label with color (only change market status color)
    market_status_label.config(
        text=full_text,
    )
    # Update every 1000 milliseconds (1 second)
    root.after(1000, update_market_status)  # Update every 1 second


def on_item_double_click(event):
    selected_item = table.selection()[0]  # Extract ticker
    open_backtest_popup(table.item(selected_item)['values'][0])


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
                    bb_signal,
                    momentum_signal
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
                    bb_signal,  # Signal line value
                    momentum_signal  # Momentum_Signal (BUY/SELL/HOLD)
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
            "BB 신호": 150,
            "모멘텀 신호": 150
        }

        for col, width in min_widths.items():
            table.column(col, width=width, minwidth=width)

    except Exception as e:
        print(f"update_table error: {e}")


def show_splash(root):
    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    width, height = 300, 150
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    splash.geometry(f"{width}x{height}+{x}+{y}")
    splash_label = tk.Label(splash, text="프로그램 로딩 중...", font=("Arial", 14))
    splash_label.pack(expand=True)
    splash.update()
    return splash


# 테이블 및 기타 UI 요소
def main():
    global root, table, market_status_label, time_label, radio_var  # 전역 변수로 radio_var 사용

    root = tk.Tk()
    root.withdraw()  # ✅ 먼저 숨긴다 (root 안보이게)
    splash = show_splash(root)  # 1. 로딩 화면 먼저 띄움
    root.title("미국 주식 실시간 모니터링(매 1분)")

    add_reload_button(root)
    market_status_label = tk.Label(root, text="주식장 종료\n한국 시간:\n미국 시간:",
                                   font=("Arial", 14))
    market_status_label.pack(pady=10)

    # Button frame for adding/removing tickers
    button_frame = tk.Frame(root)
    button_frame.pack(pady=10)

    add_button = tk.Button(button_frame, text="종목 추가", command=add_ticker)
    add_button.pack(side=tk.LEFT, padx=10)

    remove_button = tk.Button(button_frame, text="종목 삭제", command=remove_ticker)
    remove_button.pack(side=tk.LEFT, padx=10)

    # radio_var를 GUI에서 사용하기 위한 변수로 설정 (루트 윈도우가 생성된 후에 선언)
    radio_var = tk.StringVar(value=config.config["view_mode"])  # config에서 불러온 값을 기반으로 기본값 설정

    # 라디오 버튼
    radio_frame = tk.Frame(root)
    radio_frame.pack(pady=10)

    short_term_radio = tk.Radiobutton(radio_frame, text="단기 데이터", variable=radio_var, value="short",
                                      command=on_radio_select)
    long_term_radio = tk.Radiobutton(radio_frame, text="장기 데이터", variable=radio_var, value="long",
                                     command=on_radio_select)

    short_term_radio.pack(side=tk.LEFT, padx=10)
    long_term_radio.pack(side=tk.LEFT, padx=10)

    # UI 초기화
    table_frame = tk.Frame(root)
    table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    columns = ("종목명", "현재가", "추세 신호", "RSI 신호", "수익률", "MACD 신호", "BB 신호", "모멘텀 신호")
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
        "BB 신호": 150,
        "모멘텀 신호": 150,
    }

    for col in columns:
        table.heading(col, text=col)
        table.column(col, width=min_widths[col], minwidth=min_widths[col], anchor="center")

    table.tag_configure("buy", background="#e0ffe0")
    table.tag_configure("sell", background="#ffe0e0")
    table.tag_configure("hold", background="#f0f0f0")

    # 데이터 로드 및 테이블 갱신
    load_watchlist()  # watchlist 로드
    refresh_table_once()

    # 주식 감시 목록을 계속 모니터링
    threading.Thread(target=monitor_stocks, daemon=True).start()

    # 장 상태 갱신 시작
    update_market_status()

    # Bind double-click event to table for opening graph
    table.bind("<Double-1>", on_item_double_click)
    splash.destroy()  # 2. 초기화 끝나면 로딩창 닫기
    root.deiconify()  # ✅ root 메인 윈도우 보여줌
    root.mainloop()


if __name__ == "__main__":
    main()
