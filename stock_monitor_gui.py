import tkinter as tk
from tkinter import simpledialog, messagebox, ttk
import threading
import time
import json
import os
from datetime import datetime
import pytz
import yfinance as yf
from stock_score import fetch_stock_data, calculate_rsi, calculate_moving_average, is_market_open
import re

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
        try:
            results = []
            for ticker in watchlist:
                result = fetch_stock_data(ticker)
                if result:  # None이 아닌 데이터만 추가
                    results.append(result)
            update_table_func(results)
        except Exception as e:
            print(f"monitor_stocks error: {e}")
        time.sleep(60)  # 1분마다 갱신


# 주식 시장 상태를 표시할 라벨 추가
def update_market_status():
    status = "장 중" if is_market_open() else "장 종료"
    market_status_label.config(text=f"현재 시장 상태: {status}")

    # 30초마다 상태 갱신
    root.after(30000, update_market_status)  # 30초마다 갱신


# GUI 업데이트 함수
def update_table(data):
    try:
        for row in table.get_children():
            table.delete(row)

        for record in data:
            if record:  # None 데이터는 추가하지 않음
                if len(record) == 7:
                    name, ticker, price, trend, rsi, rate, rate_color = record
                else:
                    name, ticker, price, trend, rsi, rate = record
                    rate_color = "black"

                trend_display = trend  # 추세 신호 표시

                rsi_value = float(rsi.replace('%', ''))  # RSI 값 처리
                if rsi_value > 70:
                    rsi_display = f"{rsi} (과매수)"
                elif rsi_value < 30:
                    rsi_display = f"{rsi} (과매도)"
                else:
                    rsi_display = f"{rsi} (중립)"

                row_id = table.insert("", "end", values=(f"{name} ({ticker})", price, trend_display, rsi_display, rate))
                if "BUY" in trend:
                    table.item(row_id, tags=("buy",))
                elif "SELL" in trend:
                    table.item(row_id, tags=("sell",))
                else:
                    table.item(row_id, tags=("hold",))
                table.tag_configure(f"rate_{row_id}", foreground=rate_color)
                table.item(row_id, tags=(f"rate_{row_id}",))

        # Keep minimum column widths even when empty
        min_widths = {
            "종목명": 150,
            "현재가": 100,
            "추세 신호": 200,
            "RSI 신호": 150,
            "수익률": 100
        }

        for col, width in min_widths.items():
            table.column(col, width=width, minwidth=width)

        # Dynamically adjust column width based on content if there's data
        if data:
            max_width = 0
            for item in table.get_children():
                name_value = table.item(item)["values"][0]
                trend_value = table.item(item)["values"][2]
                max_width = max(max_width, len(str(name_value)), len(str(trend_value)))

            if max_width * 8 > min_widths["종목명"]:
                table.column("종목명", width=max_width * 8)
            if max_width * 8 > min_widths["추세 신호"]:
                table.column("추세 신호", width=max_width * 8)

    except Exception as e:
        print(f"update_table error: {e}")


# 테이블 및 기타 UI 요소
def main():
    global root, table, market_status_label

    root = tk.Tk()
    root.title("주식 실시간 모니터링")

    # 시장 상태를 표시할 라벨
    market_status_label = tk.Label(root, text="현재 시장 상태: 장 종료", font=("Arial", 14))
    market_status_label.pack(pady=10)

    frame = tk.Frame(root)
    frame.pack(pady=10)
    add_btn = tk.Button(frame, text="종목 추가", command=add_ticker, width=10, height=2)
    add_btn.grid(row=0, column=0, padx=5)
    remove_btn = tk.Button(frame, text="종목 삭제", command=remove_ticker, width=10, height=2)
    remove_btn.grid(row=0, column=1, padx=5)

    table_frame = tk.Frame(root)
    table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    columns = ("종목명", "현재가", "추세 신호", "RSI 신호", "수익률")
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
        "수익률": 100
    }

    for col in columns:
        table.heading(col, text=col)
        table.column(col, width=min_widths[col], minwidth=min_widths[col], anchor="center")

    table.tag_configure("buy", background="#e0ffe0")
    table.tag_configure("sell", background="#ffe0e0")
    table.tag_configure("hold", background="#f0f0f0")

    status_bar = tk.Label(root, text="데이터 로딩 중...", bd=1, relief=tk.SUNKEN, anchor=tk.W)
    status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    load_watchlist()
    refresh_table_once()

    if not watchlist:
        status_bar.config(text="종목 추가 버튼을 눌러 감시할 종목을 추가하세요.")
    else:
        status_bar.config(text=f"{len(watchlist)}개 종목 감시 중...")

    threading.Thread(target=monitor_stocks, args=(update_table,), daemon=True).start()

    # 장 상태 갱신 시작
    update_market_status()

    root.mainloop()


if __name__ == "__main__":
    main()
