import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'modules'))

import glob
import json
import logging
import re
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from tkinter import simpledialog, messagebox, ttk

import pytz
import copy

import config
from backtest_popup import open_backtest_popup
from help_texts import COLUMN_HELP, SIGNAL_HELP, QUANT_GUIDE
from market_trend_manager import guess_market_session, get_volatility_regime
from stock_score import fetch_stock_data
from ui_components import Tooltip, HelpTooltip
from news_panel import NewsPanel, start_news_refresh
import holdings_manager

# ============================================================
# Phase 9-1: Design constants
# ============================================================
PADDING_SM = 5
PADDING_MD = 10
PADDING_LG = 20

FONTS = {
    "title": ("Arial", 16, "bold"),
    "heading": ("Arial", 12, "bold"),
    "body": ("Arial", 10),
    "small": ("Arial", 9),
}

COLORS = {
    "buy": "#90EE90",
    "sell": "#FF6B6B",
    "hold": "#f0f0f0",
    "strong_buy": "#2ECC71",
    "strong_sell": "#E74C3C",
    "bg": "#ffffff",
    "fg": "#000000",
    "accent": "#4A90D9",
    "highlight": "#FFFACD",
}

# ============================================================
# Logging setup
# ============================================================
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "app.log")
MAX_BYTES = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 5
RETENTION_DAYS = 30
SAVE_FILE = "watchlist.json"

os.makedirs(LOG_DIR, exist_ok=True)

# Phase 7-4: Log cleanup in background thread
def _cleanup_old_logs():
    now = time.time()
    for log_file in glob.glob(os.path.join(LOG_DIR, "*.log*")):
        if os.path.isfile(log_file):
            try:
                mtime = os.path.getmtime(log_file)
                age_days = (now - mtime) / (60 * 60 * 24)
                if age_days > RETENTION_DAYS:
                    os.remove(log_file)
                    logging.info(f"[CLEANUP] Deleted {log_file} (age: {age_days:.1f} days)")
            except Exception as e:
                logging.error(f"[CLEANUP] Failed to delete {log_file}: {e}")

threading.Thread(target=_cleanup_old_logs, daemon=True).start()

# Logging handlers
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


# ============================================================
# Phase 8-5: AppState class for global state management
# ============================================================
class AppState:
    def __init__(self):
        self.watchlist = []
        self.watchlist_lock = threading.Lock()  # Phase 2-1
        self.shutdown_event = threading.Event()  # Phase 2-4
        self.monitor_thread = None
        self.executor = ThreadPoolExecutor(max_workers=10)  # Phase 4-2: Reuse executor
        self.root = None
        self.table = None
        self.market_status_label = None
        self.status_bar = None
        self.radio_var = None
        self.period_info_label = None
        self.last_refresh_time = None
        self.previous_data = {}  # Phase 11-6: Track previous prices
        self.undo_ticker = None  # Phase 11-5: Undo delete
        self.undo_timer = None
        self._sort_col = None
        self._sort_reverse = False
        self._column_help_tip = None  # 컬럼 헤더 호버 툴팁
        self.news_panel = None
        self.cached_news_list = []
        self.news_lock = threading.Lock()
        self.holdings = {}            # {ticker: {quantity, avg_price, purchase_date, notes}}
        self.holdings_lock = threading.Lock()


app = AppState()


# ============================================================
# Config & watchlist functions
# ============================================================
def add_reload_button(parent_frame):
    top_bar_frame = tk.Frame(parent_frame)
    top_bar_frame.pack(fill=tk.X, pady=PADDING_SM, padx=PADDING_MD)
    reload_btn = tk.Button(top_bar_frame, text="설정 다시 불러오기", command=reload_config, font=FONTS["body"])
    reload_btn.pack(side=tk.RIGHT, padx=PADDING_MD, anchor="ne")
    Tooltip(reload_btn, "설정 파일을 다시 불러옵니다 (F5)")


def reload_config():
    config.set_config(config.load_config())
    refresh_table()
    if app.news_panel:
        app.news_panel._on_refresh_click()
    update_status_bar("설정이 다시 불러와졌습니다")
    messagebox.showinfo("설정 불러오기", "설정이 다시 불러와졌습니다.")


def _calc_period_date_range(period_str):
    """period 문자열('30d', '3mo', '1y')을 파싱하여 (시작일, 종료일) 문자열 반환.
    연도가 다르면 YYYY-MM-DD, 같으면 MM-DD 포맷."""
    match = re.match(r"(\d+)([a-zA-Z]+)", period_str)
    if not match:
        return None, None
    value = int(match.group(1))
    unit = match.group(2)
    now = datetime.now()
    if unit == 'd':
        start = now - timedelta(days=value)
    elif unit == 'mo':
        start = now - timedelta(days=value * 30)
    elif unit == 'y':
        start = now - timedelta(days=value * 365)
    else:
        return None, None
    return start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")


def _format_date_range_text(period):
    """날짜 범위 표시 전용 문자열 반환."""
    start, end = _calc_period_date_range(period)
    if start and end:
        return f"{start} ~ {end}"
    return ""


def _format_preset_tooltip(preset_key):
    """프리셋 설정값에서 tooltip 텍스트 생성."""
    label = {"short": "단기", "middle": "중기", "long": "장기"}.get(preset_key, preset_key)
    s = config.config["settings"].get(preset_key, {})
    period = s.get("period", "?")
    interval = s.get("interval", "?")
    return f"{label}: {period}, {interval}"


def _update_radio_tooltips():
    """라디오 버튼 tooltip을 현재 설정값으로 갱신."""
    if hasattr(app, '_radio_tooltips'):
        for key, tt in app._radio_tooltips.items():
            tt.text = _format_preset_tooltip(key)


def on_radio_select():
    selected_value = app.radio_var.get()

    # 사용자 지정 날짜 프레임 표시/숨김
    if hasattr(app, '_custom_date_frame'):
        if selected_value == "custom":
            app._custom_date_frame.pack(after=app._radio_label_frame, pady=(0, PADDING_SM), padx=PADDING_MD)
        else:
            app._custom_date_frame.pack_forget()

    if selected_value == "custom":
        # custom 모드: start/end 날짜 사용
        config.config["current"]["custom_mode"] = True
        start_str = app._custom_start_entry.get().strip()
        end_str = app._custom_end_entry.get().strip()
        try:
            datetime.strptime(start_str, '%Y-%m-%d')
            datetime.strptime(end_str, '%Y-%m-%d')
        except ValueError:
            messagebox.showerror("날짜 오류", "날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)")
            return
        if start_str >= end_str:
            messagebox.showerror("날짜 오류", "시작 날짜가 종료 날짜보다 이전이어야 합니다.")
            return
        config.config["current"]["start_date"] = start_str
        config.config["current"]["end_date"] = end_str
        config.config["view_mode"] = selected_value
        config.save_config(config.get_config())

        if app.period_info_label:
            app.period_info_label.config(text="사용자 지정")
            app.date_range_label.config(text=f"{start_str} ~ {end_str}")

        refresh_table()
        return

    try:
        config.config["current"]["custom_mode"] = False
        settings = config.config["settings"][selected_value]
        config.config["current"]["period"] = settings["period"]
        config.config["current"]["interval"] = settings["interval"]
        config.config["current"]["rsi"] = copy.deepcopy(settings["rsi"])
        config.config["current"]["ma_cross"] = copy.deepcopy(settings["ma_cross"])
        config.config["current"]["macd"] = copy.deepcopy(settings["macd"])
        config.config["current"]["bollinger"] = copy.deepcopy(settings["bollinger"])
        config.config["current"]["momentum_return"] = copy.deepcopy(settings["momentum_return"])

        # Phase 3-10: try-except for split_period_string
        try:
            backtest = split_period_string(config.config["current"]["period"])
            config.config["backtest"]['period'] = backtest[0]
            config.config["backtest"]['unit'] = backtest[1]
        except ValueError as e:
            logging.warning(f"[CONFIG] Failed to parse period: {e}")

        config.config["view_mode"] = selected_value
        config.save_config(config.get_config())

        # Phase 9-4: Update period/interval display
        if app.period_info_label:
            p = config.config["current"]["period"]
            i = config.config["current"]["interval"]
            app.period_info_label.config(text=f"{p} / {i}")
            app.date_range_label.config(text=_format_date_range_text(p))

        refresh_table()
    except (KeyError, TypeError) as e:
        logging.error(f"[CONFIG] Radio select error: {e}")
        messagebox.showerror("설정 오류", f"설정 전환 오류: {e}")


def refresh_table():
    """Clear and refresh the table."""
    for row in app.table.get_children():
        app.table.delete(row)
    refresh_table_once()


def split_period_string(period_str):
    match = re.match(r"(\d+)([a-zA-Z]+)", period_str)
    if match:
        number = int(match.group(1))
        unit = match.group(2)
        # Phase 5-2: Range validation
        if number < 1 or number > 9999:
            raise ValueError(f"Period number out of range: {number}")
        return number, unit
    else:
        raise ValueError(f"Invalid period format: {period_str}")


# ============================================================
# Phase 5-1: Ticker input validation
# ============================================================
def validate_ticker(ticker_str):
    """Validate ticker input: 1~10 chars, alphanumeric + dot only."""
    if not ticker_str or not ticker_str.strip():
        return False, "티커를 입력하세요."
    ticker_str = ticker_str.strip()
    if len(ticker_str) < 1 or len(ticker_str) > 10:
        return False, "티커는 1~10자 사이여야 합니다."
    if not re.match(r'^[A-Za-z0-9.]+$', ticker_str):
        return False, "티커는 영문, 숫자, 점(.)만 허용됩니다."
    return True, ""


def add_ticker():
    name_or_ticker = simpledialog.askstring("종목 추가", "추가할 종목 티커를 입력하세요 (예: NVDA, TSLA)")
    if not name_or_ticker:
        return

    name_or_ticker = name_or_ticker.upper().strip()
    valid, msg = validate_ticker(name_or_ticker)
    if not valid:
        messagebox.showwarning("입력 오류", msg)
        return

    update_status_bar("종목 추가 중...")

    def _do_add():
        try:
            import yfinance as yf
            ticker_info = yf.Ticker(name_or_ticker).info
            company_name = ticker_info.get('shortName')

            def _on_result():
                if company_name:
                    with app.watchlist_lock:
                        if name_or_ticker not in app.watchlist:
                            app.watchlist.append(name_or_ticker)
                            save_watchlist()
                            logging.info(f"[STOCK] {company_name} ({name_or_ticker}) added")
                            messagebox.showinfo("추가 완료", f"{company_name} ({name_or_ticker}) 추가되었습니다.")
                            threading.Thread(target=refresh_table_once, daemon=True).start()
                            if messagebox.askyesno("보유 정보", f"{company_name}의 보유 정보를 입력하시겠습니까?"):
                                open_holdings_edit_dialog(name_or_ticker, company_name)
                        else:
                            messagebox.showinfo("중복", f"{company_name} ({name_or_ticker})는 이미 감시 중입니다.")
                else:
                    messagebox.showwarning("검색 실패", f"{name_or_ticker}에 대한 정보를 찾을 수 없습니다.")
                update_status_bar()

            app.root.after(0, _on_result)

        except (ConnectionError, TimeoutError) as e:
            logging.error(f"[STOCK] Network error adding {name_or_ticker}: {e}")
            app.root.after(0, lambda: [
                messagebox.showwarning("네트워크 오류", f"네트워크 연결을 확인하세요.\n{e}"),
                update_status_bar()
            ])
        except Exception as e:
            logging.error(f"[STOCK] Error adding {name_or_ticker}: {e}")
            app.root.after(0, lambda: [
                messagebox.showwarning("검색 실패", f"{name_or_ticker} 정보를 가져오는 중 오류가 발생했습니다."),
                update_status_bar()
            ])

    threading.Thread(target=_do_add, daemon=True).start()


def remove_ticker():
    selected_item = app.table.selection()
    if not selected_item:
        messagebox.showwarning("선택 오류", "삭제할 종목을 선택해주세요.")
        return

    for item in selected_item:
        company_name_with_ticker = app.table.item(item)["values"][0]
        match = re.search(r'\((.*?)\)', str(company_name_with_ticker))
        if match:
            ticker_to_remove = match.group(1)
            # Phase 11-5: Confirmation dialog
            if not messagebox.askyesno("삭제 확인", f"{company_name_with_ticker}을(를) 삭제하시겠습니까?"):
                return
            removed = False
            with app.watchlist_lock:
                if ticker_to_remove in app.watchlist:
                    app.watchlist.remove(ticker_to_remove)
                    save_watchlist()
                    with app.holdings_lock:
                        holdings_manager.remove_holding(app.holdings, ticker_to_remove)
                        holdings_manager.save_holdings(app.holdings)
                    logging.info(f"[STOCK] {company_name_with_ticker} removed")
                    app.undo_ticker = ticker_to_remove
                    removed = True
                else:
                    messagebox.showwarning("없음", f"{ticker_to_remove}은 감시 리스트에 없습니다.")
            if removed:
                update_status_bar(f"{ticker_to_remove} 삭제됨", undo=True)
                refresh_table_once()
        else:
            messagebox.showwarning("형식 오류", f"티커를 추출할 수 없습니다: {company_name_with_ticker}")


def undo_delete():
    """Phase 11-5: Undo last delete."""
    if app.undo_ticker:
        should_refresh = False
        with app.watchlist_lock:
            if app.undo_ticker not in app.watchlist:
                app.watchlist.append(app.undo_ticker)
                save_watchlist()
                logging.info(f"[STOCK] Undo delete: {app.undo_ticker}")
                should_refresh = True
        if should_refresh:
            update_status_bar(f"{app.undo_ticker} 복원됨")
            refresh_table_once()
        app.undo_ticker = None


def save_watchlist():
    """Save watchlist to file (assumes caller holds lock if needed)."""
    try:
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(app.watchlist, f)
    except Exception as e:
        logging.error(f"[WATCHLIST] Error saving: {e}")


def open_holdings_edit_dialog(ticker, company_name=""):
    """보유 정보 편집 다이얼로그 (거래 내역 기반)."""
    try:
        from tkcalendar import DateEntry as _DateEntry
        _has_calendar = True
    except ImportError:
        _has_calendar = False

    popup = tk.Toplevel(app.root)
    title = f"보유 정보 - {ticker}"
    if company_name:
        title += f" ({company_name})"
    popup.title(title)
    popup.state('zoomed')
    popup.grab_set()
    popup.resizable(True, True)
    popup.minsize(550, 400)

    # --- 요약 라벨 ---
    summary_var = tk.StringVar()

    def _update_summary():
        holding = holdings_manager.get_holding(app.holdings, ticker)
        if holding and holding["quantity"] > 0:
            rpnl = holding["total_realized_pnl"]
            rpnl_sign = "+" if rpnl >= 0 else ""
            summary_var.set(
                f"현재 보유: {holding['quantity']:g}주 | "
                f"평균단가: ${holding['avg_price']:,.2f} | "
                f"실현손익: {rpnl_sign}${rpnl:,.0f}"
            )
        elif holding and holding["total_realized_pnl"] != 0:
            rpnl = holding["total_realized_pnl"]
            rpnl_sign = "+" if rpnl >= 0 else ""
            summary_var.set(f"보유 없음 | 실현손익: {rpnl_sign}${rpnl:,.0f}")
        else:
            summary_var.set("보유 없음")

    _update_summary()
    summary_label = tk.Label(popup, textvariable=summary_var, font=("Arial", 11, "bold"),
                              fg="#1A5276", pady=8)
    summary_label.pack(fill=tk.X, padx=10)

    # --- 거래 추가 프레임 ---
    add_frame = tk.LabelFrame(popup, text="거래 추가", font=FONTS["body"])
    add_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

    add_inner = tk.Frame(add_frame, padx=8, pady=5)
    add_inner.pack(fill=tk.X)

    # Row 1: 유형, 수량, 단가
    row1 = tk.Frame(add_inner)
    row1.pack(fill=tk.X, pady=2)

    tx_type_var = tk.StringVar(value="매수")
    tx_type_combo = ttk.Combobox(row1, textvariable=tx_type_var, values=["매수", "매도"],
                                  width=5, state="readonly", font=FONTS["body"])
    tx_type_combo.pack(side=tk.LEFT, padx=(0, 5))

    tk.Label(row1, text="수량:", font=FONTS["body"]).pack(side=tk.LEFT)
    qty_entry = tk.Entry(row1, width=10, font=FONTS["body"])
    qty_entry.pack(side=tk.LEFT, padx=(2, 8))

    tk.Label(row1, text="단가($):", font=FONTS["body"]).pack(side=tk.LEFT)
    price_entry = tk.Entry(row1, width=10, font=FONTS["body"])
    price_entry.pack(side=tk.LEFT, padx=(2, 0))

    # Row 2: 날짜, 메모, 추가 버튼
    row2 = tk.Frame(add_inner)
    row2.pack(fill=tk.X, pady=2)

    tk.Label(row2, text="날짜:", font=FONTS["body"]).pack(side=tk.LEFT)
    _now = datetime.now()
    if _has_calendar:
        date_entry = _DateEntry(row2, width=10, font=FONTS["body"], date_pattern="yyyy-mm-dd",
                                year=_now.year, month=_now.month, day=_now.day, locale="ko_KR")
        date_entry.pack(side=tk.LEFT, padx=(2, 8))
    else:
        date_entry = tk.Entry(row2, width=12, font=FONTS["body"])
        date_entry.pack(side=tk.LEFT, padx=(2, 8))
        date_entry.insert(0, _now.strftime("%Y-%m-%d"))

    tk.Label(row2, text="메모:", font=FONTS["body"]).pack(side=tk.LEFT)
    notes_entry = tk.Entry(row2, width=15, font=FONTS["body"])
    notes_entry.pack(side=tk.LEFT, padx=(2, 8))

    def _add_transaction():
        try:
            qty_str = qty_entry.get().strip()
            price_str = price_entry.get().strip()
            if not qty_str or not price_str:
                messagebox.showwarning("입력 오류", "수량과 단가를 입력하세요.", parent=popup)
                return
            qty = float(qty_str)
            price = float(price_str)
            if qty <= 0:
                messagebox.showwarning("입력 오류", "수량은 0보다 커야 합니다.", parent=popup)
                return
            if price <= 0:
                messagebox.showwarning("입력 오류", "단가는 0보다 커야 합니다.", parent=popup)
                return

            tx_type = "buy" if tx_type_var.get() == "매수" else "sell"
            if _has_calendar:
                tx_date = date_entry.get_date().strftime("%Y-%m-%d")
            else:
                tx_date = date_entry.get().strip()
            notes = notes_entry.get().strip()

            with app.holdings_lock:
                ok, err = holdings_manager.add_transaction(
                    app.holdings, ticker, tx_type, qty, price, tx_date, notes)
                if not ok:
                    messagebox.showwarning("매도 오류", err, parent=popup)
                    return
                holdings_manager.save_holdings(app.holdings)

            # 입력 필드 초기화
            qty_entry.delete(0, tk.END)
            price_entry.delete(0, tk.END)
            notes_entry.delete(0, tk.END)
            _update_summary()
            _refresh_tx_tree()
        except ValueError:
            messagebox.showwarning("입력 오류", "수량과 단가는 숫자여야 합니다.", parent=popup)

    tk.Button(row2, text="추가", command=_add_transaction, font=FONTS["body"],
              width=6, bg="#4A90D9", fg="white").pack(side=tk.LEFT)

    # --- 거래 내역 Treeview ---
    tx_frame = tk.LabelFrame(popup, text="거래 내역", font=FONTS["body"])
    tx_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

    tx_cols = ("유형", "수량", "단가", "금액", "날짜", "메모")
    tx_tree = ttk.Treeview(tx_frame, columns=tx_cols, show="headings", height=8)
    tx_vsb = ttk.Scrollbar(tx_frame, orient="vertical", command=tx_tree.yview)
    tx_tree.configure(yscrollcommand=tx_vsb.set)

    col_widths = {"유형": 50, "수량": 60, "단가": 80, "금액": 90, "날짜": 85, "메모": 120}
    for col in tx_cols:
        tx_tree.heading(col, text=col)
        anchor = "e" if col in ("수량", "단가", "금액") else ("center" if col == "유형" else "w")
        tx_tree.column(col, width=col_widths[col], anchor=anchor, minwidth=40)

    tx_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0), pady=5)
    tx_vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=5, padx=(0, 5))

    # 매수/매도 색상 태그
    tx_tree.tag_configure("buy_tx", foreground="#2E7D32")
    tx_tree.tag_configure("sell_tx", foreground="#E74C3C")

    def _refresh_tx_tree():
        for row in tx_tree.get_children():
            tx_tree.delete(row)
        info = app.holdings.get(ticker)
        if not info:
            return
        transactions = info.get("transactions", [])
        for tx in transactions:
            tx_type_display = "매수" if tx["type"] == "buy" else "매도"
            amount = tx["quantity"] * tx["price"]
            tag = "buy_tx" if tx["type"] == "buy" else "sell_tx"
            tx_tree.insert("", "end", values=(
                tx_type_display,
                f"{tx['quantity']:g}",
                f"${tx['price']:,.2f}",
                f"${amount:,.0f}",
                tx.get("date", ""),
                tx.get("notes", ""),
            ), tags=(tag,))

    _refresh_tx_tree()

    def _edit_selected_notes(event=None):
        selection = tx_tree.selection()
        if not selection:
            if event is None:
                messagebox.showwarning("선택 오류", "메모를 수정할 거래를 선택하세요.", parent=popup)
            return
        idx = tx_tree.index(selection[0])
        info = app.holdings.get(ticker)
        if not info:
            return
        transactions = info.get("transactions", [])
        if idx < 0 or idx >= len(transactions):
            return
        old_notes = transactions[idx].get("notes", "")

        edit_win = tk.Toplevel(popup)
        edit_win.title("메모 수정")
        edit_win.geometry("350x120")
        edit_win.grab_set()
        edit_win.transient(popup)
        edit_win.resizable(False, False)

        tk.Label(edit_win, text="메모:", font=FONTS["body"]).pack(anchor="w", padx=10, pady=(10, 2))
        memo_entry = tk.Entry(edit_win, width=40, font=FONTS["body"])
        memo_entry.pack(padx=10, fill=tk.X)
        memo_entry.insert(0, old_notes)
        memo_entry.focus_set()
        memo_entry.select_range(0, tk.END)

        def _save_notes():
            new_notes = memo_entry.get().strip()
            with app.holdings_lock:
                transactions[idx]["notes"] = new_notes
                holdings_manager.save_holdings(app.holdings)
            _refresh_tx_tree()
            edit_win.destroy()

        memo_entry.bind("<Return>", lambda e: _save_notes())

        btn_row = tk.Frame(edit_win)
        btn_row.pack(pady=8)
        tk.Button(btn_row, text="저장", command=_save_notes, font=FONTS["body"],
                  width=8, bg="#4A90D9", fg="white").pack(side=tk.LEFT, padx=5)
        tk.Button(btn_row, text="취소", command=edit_win.destroy, font=FONTS["body"],
                  width=8).pack(side=tk.LEFT, padx=5)

    tx_tree.bind("<Double-1>", _edit_selected_notes)

    # --- 하단 버튼 ---
    btn_frame = tk.Frame(popup)
    btn_frame.pack(fill=tk.X, padx=10, pady=(0, 8))

    def _delete_selected_tx():
        selection = tx_tree.selection()
        if not selection:
            messagebox.showwarning("선택 오류", "삭제할 거래를 선택하세요.", parent=popup)
            return
        idx = tx_tree.index(selection[0])
        with app.holdings_lock:
            ok, err = holdings_manager.remove_transaction(app.holdings, ticker, idx)
            if not ok:
                messagebox.showwarning("삭제 오류", err, parent=popup)
                return
            holdings_manager.save_holdings(app.holdings)
        _update_summary()
        _refresh_tx_tree()

    def _clear_all():
        if not messagebox.askyesno("확인", f"{ticker}의 모든 거래 내역을 삭제하시겠습니까?", parent=popup):
            return
        with app.holdings_lock:
            holdings_manager.remove_holding(app.holdings, ticker)
            holdings_manager.save_holdings(app.holdings)
        _update_summary()
        _refresh_tx_tree()

    def _on_close():
        popup.destroy()
        refresh_table_once()

    tk.Button(btn_frame, text="메모 수정", command=_edit_selected_notes,
              font=FONTS["body"]).pack(side=tk.LEFT, padx=3)
    tk.Button(btn_frame, text="선택 거래 삭제", command=_delete_selected_tx,
              font=FONTS["body"]).pack(side=tk.LEFT, padx=3)
    tk.Button(btn_frame, text="전체 초기화", command=_clear_all,
              font=FONTS["body"], fg="#E74C3C").pack(side=tk.LEFT, padx=3)
    tk.Button(btn_frame, text="닫기", command=_on_close,
              font=FONTS["body"], width=8).pack(side=tk.RIGHT, padx=3)


def edit_holding_for_selected():
    """선택된 종목의 보유 정보 편집."""
    selection = app.table.selection()
    if not selection:
        messagebox.showwarning("선택 오류", "종목을 선택해주세요.")
        return
    item = selection[0]
    company_name_with_ticker = str(app.table.item(item)["values"][0])
    match = re.search(r'\((.*?)\)', company_name_with_ticker)
    if match:
        ticker = match.group(1)
        company_name = company_name_with_ticker.split("(")[0].strip()
        open_holdings_edit_dialog(ticker, company_name)


def load_watchlist():
    with app.watchlist_lock:
        try:
            if os.path.exists(SAVE_FILE):
                with open(SAVE_FILE, "r", encoding="utf-8") as f:
                    app.watchlist = json.load(f)
        except Exception as e:
            logging.error(f"[WATCHLIST] Error loading: {e}")


# ============================================================
# Data refresh
# ============================================================
def refresh_table_once():
    """Fetch data for all tickers and update table."""
    try:
        def _status(msg=None, **kwargs):
            """Thread-safe status bar update."""
            if threading.current_thread() is threading.main_thread():
                update_status_bar(msg, **kwargs)
            else:
                app.root.after(0, lambda: update_status_bar(msg, **kwargs))

        results = []
        completed = [0]  # mutable counter for closure

        with app.watchlist_lock:
            tickers = list(app.watchlist)
        total = len(tickers)

        def _combined_status():
            news_status = getattr(app, '_news_loading_status', '')
            stock_part = f"주식 데이터: {completed[0]}/{total}"
            combined = f"{stock_part} | {news_status}" if news_status else stock_part
            _status(combined)

        _combined_status() if total else _status("워치리스트가 비어 있습니다")

        def fetch_and_collect(t):
            result = fetch_stock_data(t)
            if result:
                results.append(result)

        # Phase 4-2: Reuse executor
        futures = [app.executor.submit(fetch_and_collect, t) for t in tickers]
        for f in futures:
            try:
                f.result(timeout=30)
            except Exception as e:
                logging.error(f"[FETCH] Thread error: {e}")
            completed[0] += 1
            _combined_status()

        def _do_ui_update():
            update_table(results)
            app.last_refresh_time = datetime.now()
            update_status_bar()

        if threading.current_thread() is threading.main_thread():
            _do_ui_update()
        else:
            app.root.after(0, _do_ui_update)
    except Exception as e:
        logging.error(f"[REFRESH] refresh_table_once error: {e}")
        _status(f"갱신 오류: {e}")


def monitor_stocks():
    """Phase 2-4: Monitor thread with shutdown event."""
    time.sleep(60)
    while not app.shutdown_event.is_set():
        try:
            refresh_table_once()
        except Exception as e:
            logging.error(f"[MONITOR] Error: {e}")

        session = guess_market_session()
        if session != "주식장 종료":
            logging.info(f'[MONITOR] {session} - refreshing...')
            # Wait with shutdown check
            if app.shutdown_event.wait(timeout=60):
                break
        else:
            logging.info("[MONITOR] Market closed, stopping refresh")
            break


# ============================================================
# Market status
# ============================================================
def update_market_status():
    if app.shutdown_event.is_set():
        return

    korea_timezone = pytz.timezone('Asia/Seoul')
    new_york_timezone = pytz.timezone('America/New_York')

    korea_time = datetime.now(korea_timezone).strftime("%Y-%m-%d %H:%M:%S")
    new_york_time = datetime.now(new_york_timezone).strftime("%Y-%m-%d %H:%M:%S")

    status = guess_market_session()
    period = config.config["current"]["period"]
    interval = config.config["current"]["interval"]
    start, end = _calc_period_date_range(period)
    period_display = f"{period} ({start} ~ {end})" if start and end else period
    # 변동성 레짐
    vol_regime, vix_val = get_volatility_regime()
    regime_labels = {"Low": "저변동", "Normal": "보통", "High": "고변동"}
    regime_text = regime_labels.get(vol_regime, vol_regime)
    if vix_val is not None:
        regime_text = f"VIX {vix_val:.1f} ({regime_text})"

    full_text = f"{status} | {regime_text}\n분석기간: {period_display}, 간격: {interval}\n한국 시간: {korea_time}\n미국 시간: {new_york_time}"

    app.market_status_label.config(text=full_text)
    app.root.after(1000, update_market_status)


# ============================================================
# Phase 3-9: Safe double-click handler
# ============================================================
def on_item_double_click(event):
    selection = app.table.selection()
    if not selection:
        return
    selected_item = selection[0]
    open_backtest_popup(str(app.table.item(selected_item)['values'][0]), app_state=app)


# ============================================================
# Table update with all UI improvements
# ============================================================
def update_table(data):
    try:
        # 사용자가 조절한 컬럼 너비 보존
        saved_widths = {}
        for col in app.table["columns"]:
            saved_widths[col] = app.table.column(col, "width")

        for row in app.table.get_children():
            app.table.delete(row)

        # Phase 11-4: Empty watchlist hint
        if not data:
            with app.watchlist_lock:
                if not app.watchlist:
                    update_status_bar("종목을 추가하세요 (Ctrl+A)")
            return

        for record in data:
            if not record:
                continue

            # Phase 8-3: Works with both NamedTuple and tuple
            if hasattr(record, 'company_name'):
                name = record.company_name
                t = record.ticker
                price = record.price
                trend = record.trend_signal
                rsi = record.rsi_signal
                rate = record.rate
                rate_color = record.rate_color
                macd_signal = record.macd_signal
                bb_signal = record.bb_signal
                momentum_signal = record.momentum_signal
                value_score = getattr(record, 'value_score', 'N/A')
                value_judgment = getattr(record, 'value_judgment', 'N/A')
                per_value = getattr(record, 'per_value', None)
                roe_value = getattr(record, 'roe_value', None)
                week52_pct = getattr(record, 'week52_pct', None)
                volume_ratio = getattr(record, 'volume_ratio', None)
                atr_pct = getattr(record, 'atr_pct', None)
                divergence_signal = getattr(record, 'divergence_signal', '')
                liquidity_warning = getattr(record, 'liquidity_warning', '')
                adx_value = getattr(record, 'adx_value', None)
                adx_signal = getattr(record, 'adx_signal', '')
                vwap_signal = getattr(record, 'vwap_signal', '')
                obv_signal = getattr(record, 'obv_signal', '')
                stoch_signal = getattr(record, 'stoch_signal', '')
                earnings_dday = getattr(record, 'earnings_dday', '')
                short_float = getattr(record, 'short_float', None)
                insider_held = getattr(record, 'insider_held', None)
                ichimoku_signal = getattr(record, 'ichimoku_signal', '')
                pattern_signal = getattr(record, 'pattern_signal', '-')
            else:
                (name, t, price, trend, rsi, rate, rate_color, macd_signal, bb_signal, momentum_signal) = record[:10]
                value_score = 'N/A'
                value_judgment = 'N/A'
                per_value = None
                roe_value = None
                week52_pct = None
                volume_ratio = None
                atr_pct = None
                divergence_signal = ''
                liquidity_warning = ''
                adx_value = None
                adx_signal = ''
                vwap_signal = ''
                obv_signal = ''
                stoch_signal = ''
                earnings_dday = ''
                short_float = None
                insider_held = None
                ichimoku_signal = ''
                pattern_signal = '-'

            rsi_value = float(rsi.replace('%', ''))
            if rsi_value > config.config['current']['rsi']['upper']:
                rsi_display = f"{rsi} (과매수)"
            elif rsi_value < config.config['current']['rsi']['lower']:
                rsi_display = f"{rsi} (과매도)"
            else:
                rsi_display = f"{rsi} (중립)"

            # 가치 점수 표시 문자열
            if value_judgment and value_judgment != 'N/A':
                value_display = f"{value_judgment} ({value_score})"
            else:
                value_display = "N/A"

            per_display = f"{per_value:.1f}" if per_value is not None else "N/A"
            roe_display = f"{roe_value:.1f}%" if roe_value is not None else "N/A"
            week52_display = f"{week52_pct:.0f}%" if week52_pct is not None else "N/A"

            # 거래량 비율 표시: 평균 대비 배수 + 상태
            if volume_ratio is not None:
                if volume_ratio >= 2.0:
                    vol_display = f"×{volume_ratio:.1f} 폭증"
                elif volume_ratio >= 1.5:
                    vol_display = f"×{volume_ratio:.1f} 증가"
                elif volume_ratio <= 0.5:
                    vol_display = f"×{volume_ratio:.1f} 부족"
                else:
                    vol_display = f"×{volume_ratio:.1f}"
            else:
                vol_display = "N/A"

            # ATR 변동성 표시
            if atr_pct is not None:
                if atr_pct >= 5:
                    atr_display = f"{atr_pct:.1f}% 고"
                elif atr_pct >= 3:
                    atr_display = f"{atr_pct:.1f}% 중"
                else:
                    atr_display = f"{atr_pct:.1f}% 저"
            else:
                atr_display = "N/A"

            # 유동성 경고 표시
            if liquidity_warning:
                vol_display = f"{vol_display} [{liquidity_warning}]"

            div_display = divergence_signal if divergence_signal else "-"

            # VWAP 표시
            vwap_display = vwap_signal if vwap_signal else "-"

            # OBV 표시
            obv_display = obv_signal if obv_signal else "-"

            # Stochastic 표시
            stoch_display = stoch_signal if stoch_signal else "-"

            # 실적발표 표시
            earnings_display = earnings_dday if earnings_dday else "-"

            # 공매도 비율 표시
            if short_float is not None:
                sf_pct = short_float * 100 if short_float < 1 else short_float
                short_display = f"{sf_pct:.1f}%"
            else:
                short_display = "-"

            # 내부자 보유 비율 표시
            if insider_held is not None:
                ih_pct = insider_held * 100 if insider_held < 1 else insider_held
                insider_display = f"{ih_pct:.1f}%"
            else:
                insider_display = "-"

            # ADX 표시
            if adx_value is not None:
                adx_display = f"{adx_value} {adx_signal}"
            else:
                adx_display = "N/A"

            # 일목균형표 표시
            ichimoku_display = ichimoku_signal if ichimoku_signal else "-"

            # 차트 패턴 표시
            pattern_display = pattern_signal if pattern_signal else "-"

            # 보유 정보 표시
            holding = holdings_manager.get_holding(app.holdings, t)
            is_held = holding is not None and holding.get("quantity", 0) > 0
            if is_held:
                h_qty = holding["quantity"]
                h_avg = holding["avg_price"]
                qty_display = f"{h_qty:g}"
                avg_display = f"${h_avg:,.2f}"
                try:
                    current_price_num = float(str(price).replace('$', '').replace(',', ''))
                    pnl_val = (current_price_num - h_avg) * h_qty
                    pnl_pct = (current_price_num - h_avg) / h_avg * 100 if h_avg > 0 else 0
                    sign = "+" if pnl_val >= 0 else ""
                    pnl_display = f"{sign}${pnl_val:,.0f} ({sign}{pnl_pct:.1f}%)"
                except (ValueError, TypeError):
                    pnl_display = "-"
            else:
                qty_display = "-"
                avg_display = "-"
                pnl_display = "-"

            # 보유 종목 표시: 종목명 앞에 ★ 추가
            display_name = f"★ {name} ({t})" if is_held else f"{name} ({t})"

            row_id = app.table.insert("", "end", values=(
                display_name,
                price,
                rate,
                momentum_signal,
                adx_display,
                trend,
                macd_signal,
                rsi_display,
                bb_signal,
                stoch_display,
                vwap_display,
                obv_display,
                vol_display,
                div_display,
                atr_display,
                value_display,
                per_display,
                roe_display,
                week52_display,
                earnings_display,
                short_display,
                insider_display,
                ichimoku_display,
                pattern_display,
                qty_display,
                avg_display,
                pnl_display
            ))

            # Phase 9-3: Row-level color based on momentum signal
            if "강력 매수" in str(momentum_signal):
                tag = "strong_buy"
            elif "매수" in str(momentum_signal):
                tag = "buy"
            elif "강력 매도" in str(momentum_signal):
                tag = "strong_sell"
            elif "매도" in str(momentum_signal):
                tag = "sell"
            else:
                tag = "hold"

            # 보유 종목이면 has_holding 태그 병합 (hold 상태일 때만 배경색 적용)
            if is_held and tag == "hold":
                app.table.item(row_id, tags=(tag, "has_holding"))
            else:
                app.table.item(row_id, tags=(tag,))

            # Phase 11-6: Price change highlight
            prev_price = app.previous_data.get(t)
            if prev_price and prev_price != price:
                tags = [tag, "price_changed"]
                if is_held and tag == "hold":
                    tags.append("has_holding")
                app.table.item(row_id, tags=tuple(tags))
                # Schedule removal of highlight after 3 seconds
                app.root.after(3000, lambda rid=row_id, tg=tag: _remove_highlight(rid, tg))

            app.previous_data[t] = price

        # Phase 11-3: BUY/SELL alert
        if config.config.get("alert_enabled", True):
            for record in data:
                if not record:
                    continue
                ms = record.momentum_signal if hasattr(record, 'momentum_signal') else record[-1]
                if "매수" in str(ms) or "매도" in str(ms):
                    try:
                        import winsound
                        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    except Exception:
                        pass
                    break  # One beep per refresh cycle

        # 저장해둔 컬럼 너비 복원
        for col, w in saved_widths.items():
            app.table.column(col, width=w)

    except Exception as e:
        logging.error(f"[TABLE] update_table error: {e}")


def _remove_highlight(row_id, original_tag):
    """Remove price change highlight."""
    try:
        if app.table.exists(row_id):
            app.table.item(row_id, tags=(original_tag,))
    except Exception:
        pass


# ============================================================
# Phase 10-4: Column sorting
# ============================================================
def sort_by_column(col):
    """Sort treeview by column header click."""
    try:
        items = [(app.table.set(k, col), k) for k in app.table.get_children('')]

        if app._sort_col == col:
            app._sort_reverse = not app._sort_reverse
        else:
            app._sort_col = col
            app._sort_reverse = False

        # Try numeric sort
        try:
            items.sort(key=lambda t: float(t[0].replace('$', '').replace('%', '').replace(',', '')),
                       reverse=app._sort_reverse)
        except (ValueError, TypeError):
            items.sort(key=lambda t: t[0], reverse=app._sort_reverse)

        for index, (val, k) in enumerate(items):
            app.table.move(k, '', index)

        # Update heading with sort indicator
        for c in app.table["columns"]:
            indicator = ""
            if c == col:
                indicator = " ▼" if app._sort_reverse else " ▲"
            app.table.heading(c, text=c + indicator)
    except Exception as e:
        logging.error(f"[TABLE] Sort error: {e}")


# ============================================================
# Technical Chart Popup (Ichimoku Cloud + Chart Patterns)
# ============================================================
def show_technical_chart(stock_name):
    """일목균형표와 차트패턴을 시각화하는 팝업을 엽니다."""
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    import matplotlib.font_manager as fm
    import yfinance as yf
    import numpy as np
    from stock_score import calculate_ichimoku
    from pattern_recognition import detect_patterns

    # 티커 추출
    ticker = stock_name.split('(')[-1].split(')')[0].strip()
    if not ticker:
        return

    popup = tk.Toplevel(app.root)
    popup.title(f"기술 차트 — {stock_name}")
    popup.state('zoomed')
    popup.minsize(900, 600)

    # 한글 폰트
    try:
        font_path = fm.findfont(fm.FontProperties(family="Malgun Gothic"))
        if font_path:
            plt.rcParams['font.family'] = fm.FontProperties(fname=font_path).get_name()
    except Exception:
        pass
    plt.rcParams['axes.unicode_minus'] = False

    open_figs = []

    def _on_close():
        for f in open_figs:
            try:
                plt.close(f)
            except Exception:
                pass
        popup.destroy()

    popup.protocol("WM_DELETE_WINDOW", _on_close)

    # 상단: 기간 선택 컨트롤
    ctrl_frame = tk.LabelFrame(popup, text="기간 설정", font=FONTS["body"])
    ctrl_frame.pack(fill=tk.X, padx=10, pady=5)

    ctrl_inner = tk.Frame(ctrl_frame)
    ctrl_inner.pack(fill=tk.X, padx=8, pady=5)

    # 모드 선택: 프리셋 vs 사용자 지정
    mode_var = tk.StringVar(value="preset")

    # --- 프리셋 모드 ---
    preset_frame = tk.Frame(ctrl_inner)
    preset_frame.pack(side=tk.LEFT)

    tk.Radiobutton(preset_frame, text="프리셋", variable=mode_var, value="preset",
                   font=FONTS["body"], command=lambda: _on_mode_change()
                   ).pack(side=tk.LEFT)

    period_var = tk.StringVar(value="6mo")
    period_combo = ttk.Combobox(preset_frame, textvariable=period_var, width=6, state="readonly",
                                 values=["1mo", "3mo", "6mo", "1y", "2y", "5y"])
    period_combo.pack(side=tk.LEFT, padx=(5, 0))

    ttk.Separator(ctrl_inner, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=12)

    # --- 사용자 지정 기간 ---
    custom_period_frame = tk.Frame(ctrl_inner)
    custom_period_frame.pack(side=tk.LEFT)

    tk.Radiobutton(custom_period_frame, text="기간 지정", variable=mode_var, value="custom_period",
                   font=FONTS["body"], command=lambda: _on_mode_change()
                   ).pack(side=tk.LEFT)

    period_num_var = tk.StringVar(value="6")
    period_num_entry = tk.Entry(custom_period_frame, textvariable=period_num_var, width=4, font=FONTS["body"])
    period_num_entry.pack(side=tk.LEFT, padx=(5, 2))

    period_unit_var = tk.StringVar(value="mo")
    period_unit_combo = ttk.Combobox(custom_period_frame, textvariable=period_unit_var, width=4, state="readonly",
                                      values=["d", "mo", "y"])
    period_unit_combo.pack(side=tk.LEFT)

    ttk.Separator(ctrl_inner, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=12)

    # --- 날짜 범위 지정 ---
    date_frame = tk.Frame(ctrl_inner)
    date_frame.pack(side=tk.LEFT)

    tk.Radiobutton(date_frame, text="날짜 범위", variable=mode_var, value="date_range",
                   font=FONTS["body"], command=lambda: _on_mode_change()
                   ).pack(side=tk.LEFT)

    from datetime import date as _date
    _today = _date.today()
    _6mo_ago = _today.replace(month=_today.month - 6) if _today.month > 6 else \
               _today.replace(year=_today.year - 1, month=_today.month + 6)

    tk.Label(date_frame, text="시작:", font=FONTS["small"]).pack(side=tk.LEFT, padx=(5, 2))
    start_var = tk.StringVar(value=_6mo_ago.strftime("%Y-%m-%d"))
    start_entry = tk.Entry(date_frame, textvariable=start_var, width=11, font=FONTS["body"])
    start_entry.pack(side=tk.LEFT)

    tk.Label(date_frame, text="종료:", font=FONTS["small"]).pack(side=tk.LEFT, padx=(8, 2))
    end_var = tk.StringVar(value=_today.strftime("%Y-%m-%d"))
    end_entry = tk.Entry(date_frame, textvariable=end_var, width=11, font=FONTS["body"])
    end_entry.pack(side=tk.LEFT)

    ttk.Separator(ctrl_inner, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=12)

    # --- 조회 버튼 + 상태 ---
    load_btn = tk.Button(ctrl_inner, text="조회", font=FONTS["body"],
                          command=lambda: _load_and_draw(), width=6)
    load_btn.pack(side=tk.LEFT, padx=(0, 10))

    status_label = tk.Label(ctrl_inner, text="", font=FONTS["small"], fg="gray")
    status_label.pack(side=tk.LEFT)

    def _on_mode_change():
        mode = mode_var.get()
        # 프리셋
        period_combo.config(state="readonly" if mode == "preset" else "disabled")
        # 사용자 지정 기간
        state_cp = "normal" if mode == "custom_period" else "disabled"
        period_num_entry.config(state=state_cp)
        period_unit_combo.config(state="readonly" if mode == "custom_period" else "disabled")
        # 날짜 범위
        state_dr = "normal" if mode == "date_range" else "disabled"
        start_entry.config(state=state_dr)
        end_entry.config(state=state_dr)

    _on_mode_change()

    # 탭
    notebook = ttk.Notebook(popup)
    notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    ichimoku_frame = tk.Frame(notebook)
    pattern_frame = tk.Frame(notebook)
    notebook.add(ichimoku_frame, text="일목균형표")
    notebook.add(pattern_frame, text="차트패턴")

    def _clear_frame(frame):
        for w in frame.winfo_children():
            w.destroy()

    def _load_and_draw():
        status_label.config(text="데이터 로딩 중...")
        popup.update_idletasks()

        mode = mode_var.get()

        # 다운로드 파라미터 결정
        dl_kwargs = {"progress": False, "interval": "1d"}
        if mode == "preset":
            dl_kwargs["period"] = period_var.get()
        elif mode == "custom_period":
            try:
                num = int(period_num_var.get())
                unit = period_unit_var.get()
                dl_kwargs["period"] = f"{num}{unit}"
            except ValueError:
                status_label.config(text="기간 숫자를 확인하세요")
                return
        elif mode == "date_range":
            dl_kwargs["start"] = start_var.get()
            dl_kwargs["end"] = end_var.get()

        def _work():
            try:
                data = yf.download(ticker, **dl_kwargs)
                if data is None or len(data) < 30:
                    popup.after(0, lambda: status_label.config(text="데이터 부족 (최소 30일 필요)"))
                    return
                # 멀티레벨 컬럼 처리
                if hasattr(data.columns, 'levels') and len(data.columns.levels) > 1:
                    data.columns = data.columns.get_level_values(0)
                popup.after(0, lambda: _draw_charts(data))
            except Exception as e:
                popup.after(0, lambda: status_label.config(text=f"오류: {e}"))

        threading.Thread(target=_work, daemon=True).start()

    def _draw_charts(data):
        # 기존 figure 정리
        for f in open_figs:
            try:
                plt.close(f)
            except Exception:
                pass
        open_figs.clear()

        _draw_ichimoku(data)
        _draw_patterns(data)
        status_label.config(text=f"{ticker} | {len(data)}일 데이터")

    def _bind_scroll_zoom(fig, ax, canvas):
        """마우스 스크롤로 차트 줌 인/아웃 + 우클릭 드래그로 팬."""
        import matplotlib.dates as mdates

        def _on_scroll(event):
            if event.inaxes != ax:
                return
            scale = 0.8 if event.button == 'up' else 1.25
            xlim = mdates.date2num(ax.get_xlim())
            ylim = ax.get_ylim()
            xdata = event.xdata  # 이미 float (날짜 숫자)
            ydata = event.ydata

            new_xmin = xdata - (xdata - xlim[0]) * scale
            new_xmax = xdata + (xlim[1] - xdata) * scale
            new_ymin = ydata - (ydata - ylim[0]) * scale
            new_ymax = ydata + (ylim[1] - ydata) * scale

            ax.set_xlim(mdates.num2date(new_xmin), mdates.num2date(new_xmax))
            ax.set_ylim(new_ymin, new_ymax)
            canvas.draw_idle()

        fig.canvas.mpl_connect('scroll_event', _on_scroll)

    def _draw_ichimoku(data):
        _clear_frame(ichimoku_frame)

        ichimoku = calculate_ichimoku(data, tenkan=9, kijun=26, senkou_b=52)
        if ichimoku is None:
            tk.Label(ichimoku_frame, text="일목균형표 계산에 충분한 데이터가 없습니다.\n기간을 늘려주세요.",
                     font=FONTS["body"]).pack(expand=True)
            return

        fig, ax = plt.subplots(figsize=(11, 5.5))
        open_figs.append(fig)

        dates = data.index
        close = data['Close'].values
        tenkan = ichimoku['tenkan_sen'].values
        kijun = ichimoku['kijun_sen'].values
        senkou_a = ichimoku['senkou_a'].values
        senkou_b = ichimoku['senkou_b'].values

        ax.plot(dates, close, label='종가', color='black', linewidth=1.2)
        ax.plot(dates, tenkan, label='전환선 (9)', color='#2196F3', linewidth=0.9, linestyle='--')
        ax.plot(dates, kijun, label='기준선 (26)', color='#F44336', linewidth=0.9, linestyle='--')

        # 구름 (양운/음운)
        sa = ichimoku['senkou_a']
        sb = ichimoku['senkou_b']
        ax.fill_between(dates, sa, sb, where=(sa >= sb),
                         color='#4CAF50', alpha=0.12, label='양운 (상승)')
        ax.fill_between(dates, sa, sb, where=(sa < sb),
                         color='#F44336', alpha=0.12, label='음운 (하락)')

        # 후행스팬
        chikou = ichimoku['chikou']
        ax.plot(dates, chikou, label='후행스팬', color='#9C27B0', linewidth=0.7, alpha=0.6)

        # 현재 신호 표시
        signal = ichimoku.get('signal', '')
        signal_color = '#4CAF50' if '강세' in signal or '구름위' in signal else \
                       '#F44336' if '약세' in signal or '구름아래' in signal else '#FF9800'
        ax.set_title(f"{stock_name} 일목균형표  [{signal}]",
                     fontsize=13, fontweight='bold', color=signal_color)

        ax.legend(fontsize=8, loc='upper left', ncol=3)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8)
        fig.autofmt_xdate(rotation=30)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=ichimoku_frame)
        canvas.draw()
        toolbar = NavigationToolbar2Tk(canvas, ichimoku_frame)
        toolbar.update()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        _bind_scroll_zoom(fig, ax, canvas)

    def _draw_patterns(data):
        _clear_frame(pattern_frame)

        patterns = detect_patterns(data, order=10, tolerance=0.03)

        fig, ax = plt.subplots(figsize=(11, 5.5))
        open_figs.append(fig)

        dates = data.index
        close = data['Close'].values
        highs = data['High'].values
        lows = data['Low'].values

        # 캔들스틱 스타일 (간이 OHLC바)
        opens = data['Open'].values
        colors_bar = ['#4CAF50' if c >= o else '#F44336' for c, o in zip(close, opens)]
        ax.bar(dates, close - opens, bottom=opens, color=colors_bar, width=0.6, alpha=0.6)
        ax.vlines(dates, lows, highs, colors='gray', linewidth=0.4, alpha=0.5)

        # 종가선
        ax.plot(dates, close, color='black', linewidth=0.8, alpha=0.5, label='종가')

        # 패턴 시각화
        pattern_colors = {
            '더블탑': '#F44336', '더블바텀': '#4CAF50',
            '헤드앤숄더': '#E91E63', '역헤드앤숄더': '#00BCD4',
            '상승삼각형': '#8BC34A', '하락삼각형': '#FF5722',
        }
        pattern_markers = {
            '매수': ('^', '#4CAF50'), '매도': ('v', '#F44336'),
        }

        if patterns:
            # 패턴 영역 하이라이트 + 주석
            for i, p in enumerate(patterns[:4]):  # 최대 4개 표시
                s_idx = p['start_idx']
                e_idx = min(p['end_idx'], len(dates) - 1)
                color = pattern_colors.get(p['pattern'], '#FF9800')
                conf_pct = int(p['confidence'] * 100)

                # 패턴 범위 배경 하이라이트
                ax.axvspan(dates[s_idx], dates[e_idx], alpha=0.08, color=color)

                # 패턴 핵심 포인트 마커
                marker, mcolor = pattern_markers.get(p['signal'], ('o', '#FF9800'))

                # 패턴 이름 + 신뢰도 주석
                mid_idx = (s_idx + e_idx) // 2
                y_pos = highs[s_idx:e_idx + 1].max() if e_idx > s_idx else highs[s_idx]
                arrow = '↑' if p['signal'] == '매수' else '↓'
                label_text = f"{p['pattern']}{arrow} ({conf_pct}%)"

                ax.annotate(label_text,
                            xy=(dates[mid_idx], y_pos),
                            xytext=(0, 15 + i * 18),
                            textcoords='offset points',
                            fontsize=9, fontweight='bold', color=color,
                            ha='center',
                            arrowprops=dict(arrowstyle='->', color=color, lw=1.2),
                            bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                                      edgecolor=color, alpha=0.85))

                # 패턴 시작/끝점 마커
                ax.scatter([dates[s_idx], dates[e_idx]],
                           [close[s_idx], close[e_idx]],
                           marker=marker, color=mcolor, s=80, zorder=5,
                           edgecolors='white', linewidths=0.8)

            title_text = f"{stock_name} 차트패턴 — {len(patterns)}개 감지"
        else:
            title_text = f"{stock_name} 차트패턴 — 감지된 패턴 없음"

        # 전체 범위 저장 (선택 해제 시 복원용)
        full_xlim = (dates[0], dates[-1])
        price_min = lows.min()
        price_max = highs.max()
        price_margin = (price_max - price_min) * 0.05
        full_ylim = (price_min - price_margin, price_max + price_margin)

        ax.set_title(title_text, fontsize=13, fontweight='bold')
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=8)
        fig.autofmt_xdate(rotation=30)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=pattern_frame)
        canvas.draw()
        toolbar = NavigationToolbar2Tk(canvas, pattern_frame)
        toolbar.update()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        _bind_scroll_zoom(fig, ax, canvas)

        # 선택 하이라이트용 아티스트 추적
        _highlight_artists = []

        def _clear_highlight():
            for artist in _highlight_artists:
                try:
                    artist.remove()
                except Exception:
                    pass
            _highlight_artists.clear()

        def _focus_pattern(p_idx):
            """테이블에서 클릭한 패턴 영역으로 차트 줌 + 강조."""
            _clear_highlight()
            p = patterns[p_idx]
            s_idx = p['start_idx']
            e_idx = min(p['end_idx'], len(dates) - 1)
            color = pattern_colors.get(p['pattern'], '#FF9800')

            # 패턴 범위 전후로 여유 (전체의 15%)
            margin = max(int((e_idx - s_idx) * 0.5), 10)
            view_start = max(0, s_idx - margin)
            view_end = min(len(dates) - 1, e_idx + margin)

            # X축 줌
            import matplotlib.dates as mdates
            ax.set_xlim(dates[view_start], dates[view_end])

            # Y축 맞춤
            view_lows = lows[view_start:view_end + 1]
            view_highs = highs[view_start:view_end + 1]
            y_min = view_lows.min()
            y_max = view_highs.max()
            y_margin = (y_max - y_min) * 0.12
            ax.set_ylim(y_min - y_margin, y_max + y_margin)

            # 패턴 영역 강조 (진한 배경)
            span = ax.axvspan(dates[s_idx], dates[e_idx], alpha=0.25, color=color, zorder=0)
            _highlight_artists.append(span)

            # 시작/끝 수직선
            for idx in [s_idx, e_idx]:
                vl = ax.axvline(dates[idx], color=color, linewidth=1.5, linestyle='--', alpha=0.7)
                _highlight_artists.append(vl)

            # 시작/끝 날짜 라벨
            s_label = ax.text(dates[s_idx], y_max + y_margin * 0.3,
                              dates[s_idx].strftime('%Y-%m-%d'),
                              fontsize=8, color=color, ha='center', fontweight='bold',
                              bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                                        edgecolor=color, alpha=0.9))
            e_label = ax.text(dates[e_idx], y_max + y_margin * 0.3,
                              dates[e_idx].strftime('%Y-%m-%d'),
                              fontsize=8, color=color, ha='center', fontweight='bold',
                              bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                                        edgecolor=color, alpha=0.9))
            _highlight_artists.extend([s_label, e_label])

            canvas.draw_idle()

        def _reset_view():
            """전체 범위로 복원."""
            _clear_highlight()
            ax.set_xlim(full_xlim)
            ax.set_ylim(full_ylim)
            canvas.draw_idle()

        # 패턴 상세 정보 테이블
        if patterns:
            info_frame = tk.LabelFrame(pattern_frame, text="감지된 패턴 상세 (클릭하면 차트에서 위치 표시)",
                                       font=FONTS["body"])
            info_frame.pack(fill=tk.X, padx=5, pady=5)

            cols = ("패턴", "신호", "신뢰도", "기간", "설명")
            tree = ttk.Treeview(info_frame, columns=cols, show="headings",
                                height=min(len(patterns), 5))
            tree.column("패턴", width=100, anchor="center")
            tree.column("신호", width=60, anchor="center")
            tree.column("신뢰도", width=70, anchor="center")
            tree.column("기간", width=180, anchor="center")
            tree.column("설명", width=400, anchor="w")
            for c in cols:
                tree.heading(c, text=c)

            tree.tag_configure("buy", background="#E8F5E9")
            tree.tag_configure("sell", background="#FFEBEE")
            tree.tag_configure("selected_buy", background="#A5D6A7")
            tree.tag_configure("selected_sell", background="#EF9A9A")

            _pattern_index_map = {}
            for i, p in enumerate(patterns):
                s_idx = p['start_idx']
                e_idx = min(p['end_idx'], len(dates) - 1)
                date_range = f"{dates[s_idx].strftime('%Y-%m-%d')} ~ {dates[e_idx].strftime('%Y-%m-%d')}"
                tag = "buy" if p['signal'] == '매수' else "sell"
                iid = tree.insert("", "end", values=(
                    p['pattern'],
                    p['signal'],
                    f"{int(p['confidence'] * 100)}%",
                    date_range,
                    p['description']
                ), tags=(tag,))
                _pattern_index_map[iid] = i

            def _on_tree_select(event):
                sel = tree.selection()
                if sel:
                    iid = sel[0]
                    p_idx = _pattern_index_map.get(iid)
                    if p_idx is not None:
                        _focus_pattern(p_idx)
                else:
                    _reset_view()

            tree.bind("<<TreeviewSelect>>", _on_tree_select)
            tree.pack(fill=tk.X, padx=5, pady=3)

            # 전체 보기 버튼
            tk.Button(info_frame, text="전체 보기", font=FONTS["small"],
                      command=lambda: (tree.selection_remove(*tree.selection()), _reset_view())
                      ).pack(pady=(0, 3))

    # 초기 로딩
    _load_and_draw()


# ============================================================
# Phase 10-2: Context menu
# ============================================================
def show_context_menu(event):
    """Right-click context menu on table."""
    row_id = app.table.identify_row(event.y)
    if row_id:
        app.table.selection_set(row_id)
        stock_name = str(app.table.item(row_id)['values'][0])
        ctx_menu = tk.Menu(app.root, tearoff=0)
        ctx_menu.add_command(label="백테스트 실행", command=lambda: on_item_double_click(None))
        ctx_menu.add_command(label="기술 차트 (일목균형표 / 차트패턴)",
                             command=lambda: show_technical_chart(stock_name))
        ctx_menu.add_command(label="보유 정보 편집", command=edit_holding_for_selected)
        ctx_menu.add_command(label="종목 삭제", command=remove_ticker)
        ctx_menu.add_separator()
        ctx_menu.add_command(label="클립보드 복사", command=lambda: _copy_to_clipboard(row_id))
        ctx_menu.tk_popup(event.x_root, event.y_root)


def _copy_to_clipboard(row_id):
    """Copy row data to clipboard."""
    try:
        values = app.table.item(row_id)['values']
        text = "\t".join(str(v) for v in values)
        app.root.clipboard_clear()
        app.root.clipboard_append(text)
        update_status_bar("클립보드에 복사됨")
    except Exception:
        pass


# ============================================================
# Phase 11-1: Status bar
# ============================================================
def update_status_bar(message=None, undo=False):
    """Update the status bar with current state."""
    if not app.status_bar:
        return

    if message:
        status_text = message
    else:
        with app.watchlist_lock:
            count = len(app.watchlist)
        session = guess_market_session()
        refresh_str = ""
        if app.last_refresh_time:
            elapsed = (datetime.now() - app.last_refresh_time).seconds
            if elapsed > 300:
                refresh_str = f" | 마지막 갱신: {elapsed // 60}분 전 (오래됨)"
            else:
                refresh_str = f" | 마지막 갱신: {app.last_refresh_time.strftime('%H:%M:%S')}"
        status_text = f"종목: {count}개 | {session}{refresh_str}"

    # 종목 더블클릭 안내 (항상 표시)
    status_text += " | 종목 더블클릭: 백테스트/상세 보기"

    for widget in app.status_bar.winfo_children():
        widget.destroy()

    tk.Label(app.status_bar, text=status_text, font=FONTS["small"], anchor="w").pack(side=tk.LEFT, padx=PADDING_SM)

    # Phase 11-5: Undo button
    if undo and app.undo_ticker:
        undo_btn = tk.Button(app.status_bar, text="되돌리기", command=undo_delete,
                             font=FONTS["small"], fg=COLORS["accent"])
        undo_btn.pack(side=tk.RIGHT, padx=PADDING_SM)
        # Auto-hide after 10 seconds
        if app.undo_timer:
            app.root.after_cancel(app.undo_timer)
        app.undo_timer = app.root.after(10000, lambda: update_status_bar())


# ============================================================
# Splash screen
# ============================================================
def show_splash(root):
    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    width, height = 300, 150
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    splash.geometry(f"{width}x{height}+{x}+{y}")
    splash_label = tk.Label(splash, text="프로그램 로딩 중...", font=FONTS["title"])
    splash_label.pack(expand=True)
    splash.update()
    return splash


# ============================================================
# Phase 2-4: Graceful shutdown
# ============================================================
def _save_column_widths():
    """현재 컬럼 너비를 config에 저장."""
    try:
        widths = {}
        for col in app.table["columns"]:
            widths[col] = app.table.column(col, "width")
        config.config["column_widths"] = widths
        config.save_config(config.get_config())
    except Exception as e:
        logging.error(f"[CONFIG] Column width save error: {e}")


def on_closing():
    logging.info("[STOP] Application shutting down...")

    # 컬럼 너비 저장
    _save_column_widths()

    app.shutdown_event.set()

    # Wait for monitor thread
    if app.monitor_thread and app.monitor_thread.is_alive():
        app.monitor_thread.join(timeout=5)

    # Phase 4-2: Shutdown executor
    app.executor.shutdown(wait=False)

    # Flush logging handlers
    for h in logging.getLogger().handlers:
        try:
            h.flush()
            h.close()
        except Exception:
            pass

    app.root.destroy()
    sys.exit(0)


# ============================================================
# 설정 팝업 창
# ============================================================
def open_settings_popup():
    """단기/중기/장기 프리셋 파라미터를 수정할 수 있는 설정 팝업."""
    popup = tk.Toplevel(app.root)
    popup.title("분석 설정")
    popup.state('zoomed')
    popup.minsize(580, 560)
    popup.grab_set()
    popup.protocol("WM_DELETE_WINDOW", lambda: (_cleanup_popup_wheel(), popup.destroy()))

    def _cleanup_popup_wheel():
        try:
            popup.unbind_all("<MouseWheel>")
        except Exception:
            pass

    notebook = ttk.Notebook(popup)
    notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    preset_names = {"short": "단기", "middle": "중기", "long": "장기"}
    tab_widgets = {}  # preset_key -> dict of widget references

    HINT_FONT = ("Arial", 8)
    HINT_COLOR = "#888888"

    def _hint(parent, row, text):
        """column=2에 짧은 설명 라벨 추가."""
        tk.Label(parent, text=text, font=HINT_FONT, fg=HINT_COLOR).grid(
            row=row, column=2, sticky="w", padx=(4, 8), pady=3)

    for preset_key, preset_label in preset_names.items():
        # 스크롤 가능한 탭
        tab_outer = ttk.Frame(notebook)
        notebook.add(tab_outer, text=preset_label)

        canvas = tk.Canvas(tab_outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab_outer, orient="vertical", command=canvas.yview)
        tab = ttk.Frame(canvas)

        tab.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
        canvas.create_window((0, 0), window=tab, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 마우스 휠 스크롤: 마우스가 canvas 위에 있을 때만 동작
        def _on_mousewheel(event, c=canvas):
            c.yview_scroll(int(-1 * (event.delta / 120)), "units")
        def _bind_wheel(event, c=canvas, handler=_on_mousewheel):
            c.bind_all("<MouseWheel>", handler)
        def _unbind_wheel(event, c=canvas):
            c.unbind_all("<MouseWheel>")
        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

        settings = config.config["settings"].get(preset_key, {})
        widgets = {}

        row = 0
        # 섹션 헤더: 기본
        tk.Label(tab, text="기본 설정", font=FONTS["heading"]).grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 2))

        row += 1
        tk.Label(tab, text="분석 기간:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        period_frame = tk.Frame(tab)
        period_frame.grid(row=row, column=1, sticky="w", padx=4, pady=3)

        # 기존 period 문자열 파싱 (예: "30d" → 숫자 30, 단위 "d")
        period_str = settings.get("period", "30d")
        import re as _re
        _pm = _re.match(r"(\d+)(\w+)", period_str)
        period_num = int(_pm.group(1)) if _pm else 30
        period_unit = _pm.group(2) if _pm else "d"

        w_period_num = tk.Spinbox(period_frame, from_=1, to=9999, width=5)
        w_period_num.pack(side=tk.LEFT)
        w_period_num.delete(0, tk.END)
        w_period_num.insert(0, period_num)

        _period_unit_map = {"d": "일", "mo": "개월", "y": "년"}
        _period_unit_rmap = {"일": "d", "개월": "mo", "년": "y"}
        w_period_unit = ttk.Combobox(period_frame, values=["일", "개월", "년"], width=4, state="readonly")
        w_period_unit.pack(side=tk.LEFT, padx=(4, 0))
        w_period_unit.set(_period_unit_map.get(period_unit, "일"))

        widgets["period_num"] = w_period_num
        widgets["period_unit"] = w_period_unit
        _hint(tab, row, "데이터를 가져올 과거 기간")

        row += 1
        tk.Label(tab, text="간격:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        interval_values = ["1m", "5m", "15m", "30m", "1h", "1d"]
        w_interval = ttk.Combobox(tab, values=interval_values, width=8, state="readonly")
        w_interval.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_interval.set(settings.get("interval", "5m"))
        widgets["interval"] = w_interval
        _hint(tab, row, "데이터 봉 간격")

        row += 1
        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=6)

        # 섹션 헤더: RSI
        row += 1
        tk.Label(tab, text="RSI (상대강도지수)", font=FONTS["heading"]).grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        row += 1
        tk.Label(tab, text="주가의 과매수/과매도 정도를 0~100 사이 수치로 나타내는 지표",
                 font=HINT_FONT, fg=HINT_COLOR).grid(row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 2))

        row += 1
        tk.Label(tab, text="RSI 기간:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_rsi_period = tk.Spinbox(tab, from_=1, to=100, width=6)
        w_rsi_period.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_rsi_period.delete(0, tk.END)
        w_rsi_period.insert(0, settings.get("rsi", {}).get("period", 14))
        widgets["rsi_period"] = w_rsi_period
        _hint(tab, row, "RSI 계산에 사용할 봉 수 (보통 14)")

        row += 1
        tk.Label(tab, text="RSI 하한:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_rsi_lower = tk.Spinbox(tab, from_=0, to=100, width=6)
        w_rsi_lower.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_rsi_lower.delete(0, tk.END)
        w_rsi_lower.insert(0, settings.get("rsi", {}).get("lower", 30))
        widgets["rsi_lower"] = w_rsi_lower
        _hint(tab, row, "이 아래면 과매도 → 매수 신호")

        row += 1
        tk.Label(tab, text="RSI 상한:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_rsi_upper = tk.Spinbox(tab, from_=0, to=100, width=6)
        w_rsi_upper.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_rsi_upper.delete(0, tk.END)
        w_rsi_upper.insert(0, settings.get("rsi", {}).get("upper", 70))
        widgets["rsi_upper"] = w_rsi_upper
        _hint(tab, row, "이 위면 과매수 → 매도 신호")

        row += 1
        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=6)

        # 섹션 헤더: 이동평균
        row += 1
        tk.Label(tab, text="이동평균 (MA) 교차", font=FONTS["heading"]).grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        row += 1
        tk.Label(tab, text="일정 기간의 평균 주가선. 단기선이 장기선을 위로 뚫으면 매수, 아래로 뚫으면 매도 신호",
                 font=HINT_FONT, fg=HINT_COLOR).grid(row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 2))

        row += 1
        tk.Label(tab, text="MA 단기:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_ma_short = tk.Spinbox(tab, from_=1, to=500, width=6)
        w_ma_short.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_ma_short.delete(0, tk.END)
        w_ma_short.insert(0, settings.get("ma_cross", {}).get("short", 5))
        widgets["ma_short"] = w_ma_short
        _hint(tab, row, "단기 이동평균 봉 수 (< 장기)")

        row += 1
        tk.Label(tab, text="MA 장기:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_ma_long = tk.Spinbox(tab, from_=1, to=500, width=6)
        w_ma_long.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_ma_long.delete(0, tk.END)
        w_ma_long.insert(0, settings.get("ma_cross", {}).get("long", 20))
        widgets["ma_long"] = w_ma_long
        _hint(tab, row, "장기 이동평균 봉 수 (> 단기)")

        row += 1
        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=6)

        # 섹션 헤더: MACD
        row += 1
        tk.Label(tab, text="MACD (추세전환 감지)", font=FONTS["heading"]).grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        row += 1
        tk.Label(tab, text="두 이동평균의 차이로 추세 방향 변화를 감지하는 지표",
                 font=HINT_FONT, fg=HINT_COLOR).grid(row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 2))

        row += 1
        tk.Label(tab, text="MACD 단기:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_macd_short = tk.Spinbox(tab, from_=1, to=100, width=6)
        w_macd_short.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_macd_short.delete(0, tk.END)
        w_macd_short.insert(0, settings.get("macd", {}).get("short", 12))
        widgets["macd_short"] = w_macd_short
        _hint(tab, row, "빠른 지수이동평균 기간 (< 장기)")

        row += 1
        tk.Label(tab, text="MACD 장기:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_macd_long = tk.Spinbox(tab, from_=1, to=100, width=6)
        w_macd_long.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_macd_long.delete(0, tk.END)
        w_macd_long.insert(0, settings.get("macd", {}).get("long", 26))
        widgets["macd_long"] = w_macd_long
        _hint(tab, row, "느린 지수이동평균 기간 (> 단기)")

        row += 1
        tk.Label(tab, text="MACD 시그널:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_macd_signal = tk.Spinbox(tab, from_=1, to=100, width=6)
        w_macd_signal.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_macd_signal.delete(0, tk.END)
        w_macd_signal.insert(0, settings.get("macd", {}).get("signal", 9))
        widgets["macd_signal"] = w_macd_signal
        _hint(tab, row, "시그널 라인 지수이동평균 기간")

        row += 1
        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=6)

        # 섹션 헤더: 볼린저
        row += 1
        tk.Label(tab, text="볼린저 밴드", font=FONTS["heading"]).grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        row += 1
        tk.Label(tab, text="주가가 통계적으로 정상 범위를 벗어났는지 판단하는 상·하한 밴드",
                 font=HINT_FONT, fg=HINT_COLOR).grid(row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 2))

        row += 1
        tk.Label(tab, text="볼린저 기간:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_bb_period = tk.Spinbox(tab, from_=1, to=100, width=6)
        w_bb_period.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_bb_period.delete(0, tk.END)
        w_bb_period.insert(0, settings.get("bollinger", {}).get("period", 20))
        widgets["bb_period"] = w_bb_period
        _hint(tab, row, "이동평균 계산 봉 수 (보통 20)")

        row += 1
        tk.Label(tab, text="표준편차 배수:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_bb_std = tk.Entry(tab, width=8)
        w_bb_std.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_bb_std.insert(0, settings.get("bollinger", {}).get("std_dev_multiplier", 2.0))
        widgets["bb_std"] = w_bb_std
        _hint(tab, row, "밴드 폭 결정 (보통 2.0)")

        row += 1
        w_bb_rebound = tk.BooleanVar(value=settings.get("bollinger", {}).get("use_rebound", False))
        tk.Checkbutton(tab, text="반등확인 사용", variable=w_bb_rebound, font=FONTS["body"]).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8, pady=3)
        widgets["bb_rebound"] = w_bb_rebound
        _hint(tab, row, "밴드 터치 후 반등/반락 확인 시 신호 발생")

        row += 1
        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=6)

        # 섹션 헤더: 모멘텀
        row += 1
        tk.Label(tab, text="모멘텀 수익률", font=FONTS["heading"]).grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        row += 1
        tk.Label(tab, text="최근 N일간 수익률이 기준치를 넘으면 상승 추세로 판단",
                 font=HINT_FONT, fg=HINT_COLOR).grid(row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 2))

        row += 1
        tk.Label(tab, text="수익률 윈도우:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_mr_window = tk.Spinbox(tab, from_=1, to=500, width=6)
        w_mr_window.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_mr_window.delete(0, tk.END)
        w_mr_window.insert(0, settings.get("momentum_return", {}).get("return_window", 5))
        widgets["mr_window"] = w_mr_window
        _hint(tab, row, "며칠간 수익률로 판단할지 (봉 수)")

        row += 1
        tk.Label(tab, text="임계값:", font=FONTS["body"]).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        w_mr_threshold = tk.Entry(tab, width=8)
        w_mr_threshold.grid(row=row, column=1, sticky="w", padx=4, pady=3)
        w_mr_threshold.insert(0, settings.get("momentum_return", {}).get("threshold", 0.02))
        widgets["mr_threshold"] = w_mr_threshold
        _hint(tab, row, "매수 진입 최소 수익률 (0.02 = 2%)")

        tab_widgets[preset_key] = widgets

    def _get_current_preset_key():
        """현재 선택된 탭의 프리셋 키를 반환."""
        idx = notebook.index(notebook.select())
        return list(preset_names.keys())[idx]

    def save_settings():
        """모든 탭의 설정을 검증하고 저장."""
        for preset_key, widgets in tab_widgets.items():
            try:
                rsi_lower = int(widgets["rsi_lower"].get())
                rsi_upper = int(widgets["rsi_upper"].get())
                if rsi_lower >= rsi_upper:
                    messagebox.showerror("검증 오류", f"[{preset_names[preset_key]}] RSI 하한({rsi_lower})이 상한({rsi_upper})보다 작아야 합니다.")
                    return

                ma_short = int(widgets["ma_short"].get())
                ma_long = int(widgets["ma_long"].get())
                if ma_short >= ma_long:
                    messagebox.showerror("검증 오류", f"[{preset_names[preset_key]}] MA 단기({ma_short})가 장기({ma_long})보다 작아야 합니다.")
                    return

                macd_short = int(widgets["macd_short"].get())
                macd_long = int(widgets["macd_long"].get())
                if macd_short >= macd_long:
                    messagebox.showerror("검증 오류", f"[{preset_names[preset_key]}] MACD 단기({macd_short})가 장기({macd_long})보다 작아야 합니다.")
                    return

                bb_std = float(widgets["bb_std"].get())
                if bb_std <= 0:
                    messagebox.showerror("검증 오류", f"[{preset_names[preset_key]}] 볼린저 표준편차 배수는 0보다 커야 합니다.")
                    return

                mr_threshold = float(widgets["mr_threshold"].get())
                if mr_threshold <= 0:
                    messagebox.showerror("검증 오류", f"[{preset_names[preset_key]}] 모멘텀 임계값은 0보다 커야 합니다.")
                    return

                # period 조합: 숫자 + 단위
                p_num = int(widgets["period_num"].get())
                p_unit_display = widgets["period_unit"].get()
                p_unit = {"일": "d", "개월": "mo", "년": "y"}.get(p_unit_display, "d")
                period_combined = f"{p_num}{p_unit}"

                new_settings = {
                    "period": period_combined,
                    "interval": widgets["interval"].get(),
                    "rsi": {"period": int(widgets["rsi_period"].get()), "lower": rsi_lower, "upper": rsi_upper},
                    "ma_cross": {"short": ma_short, "long": ma_long},
                    "macd": {"short": macd_short, "long": macd_long, "signal": int(widgets["macd_signal"].get())},
                    "bollinger": {
                        "period": int(widgets["bb_period"].get()),
                        "std_dev_multiplier": bb_std,
                        "use_rebound": widgets["bb_rebound"].get(),
                    },
                    "momentum_return": {
                        "return_window": int(widgets["mr_window"].get()),
                        "threshold": mr_threshold,
                    },
                }
                config.config["settings"][preset_key] = new_settings

            except (ValueError, TypeError) as e:
                messagebox.showerror("입력 오류", f"[{preset_names[preset_key]}] 잘못된 값: {e}")
                return

        # 현재 선택된 프리셋이면 current도 갱신
        current_view = app.radio_var.get()
        if current_view in config.config["settings"]:
            s = config.config["settings"][current_view]
            config.config["current"]["period"] = s["period"]
            config.config["current"]["interval"] = s["interval"]
            config.config["current"]["rsi"] = copy.deepcopy(s["rsi"])
            config.config["current"]["ma_cross"] = copy.deepcopy(s["ma_cross"])
            config.config["current"]["macd"] = copy.deepcopy(s["macd"])
            config.config["current"]["bollinger"] = copy.deepcopy(s["bollinger"])
            config.config["current"]["momentum_return"] = copy.deepcopy(s["momentum_return"])

        config.save_config(config.get_config())

        # period info 라벨 및 tooltip 갱신
        if app.period_info_label:
            p = config.config["current"]["period"]
            i = config.config["current"]["interval"]
            app.period_info_label.config(text=f"{p} / {i}")
            app.date_range_label.config(text=_format_date_range_text(p))
        _update_radio_tooltips()

        refresh_table()
        _cleanup_popup_wheel()
        popup.destroy()
        update_status_bar("설정이 저장되었습니다.")

    def restore_defaults():
        """현재 탭의 프리셋을 기본값으로 복원."""
        preset_key = _get_current_preset_key()
        defaults = config.default_config["settings"][preset_key]
        widgets = tab_widgets[preset_key]

        # period 복원: "30d" → 숫자 30, 단위 "일"
        import re as _re
        _pm = _re.match(r"(\d+)(\w+)", defaults["period"])
        if _pm:
            widgets["period_num"].delete(0, tk.END)
            widgets["period_num"].insert(0, _pm.group(1))
            _unit_map = {"d": "일", "mo": "개월", "y": "년"}
            widgets["period_unit"].set(_unit_map.get(_pm.group(2), "일"))
        widgets["interval"].set(defaults["interval"])

        for key, path in [
            ("rsi_period", ("rsi", "period")), ("rsi_lower", ("rsi", "lower")), ("rsi_upper", ("rsi", "upper")),
            ("ma_short", ("ma_cross", "short")), ("ma_long", ("ma_cross", "long")),
            ("macd_short", ("macd", "short")), ("macd_long", ("macd", "long")), ("macd_signal", ("macd", "signal")),
            ("bb_period", ("bollinger", "period")),
            ("mr_window", ("momentum_return", "return_window")),
        ]:
            w = widgets[key]
            w.delete(0, tk.END)
            w.insert(0, defaults[path[0]][path[1]])

        widgets["bb_std"].delete(0, tk.END)
        widgets["bb_std"].insert(0, defaults["bollinger"]["std_dev_multiplier"])
        widgets["bb_rebound"].set(defaults["bollinger"]["use_rebound"])
        widgets["mr_threshold"].delete(0, tk.END)
        widgets["mr_threshold"].insert(0, defaults["momentum_return"]["threshold"])

        messagebox.showinfo("복원 완료", f"[{preset_names[preset_key]}] 기본값으로 복원되었습니다.")

    # 버튼 프레임
    btn_frame = tk.Frame(popup)
    btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

    tk.Button(btn_frame, text="저장", command=save_settings, font=FONTS["body"], width=10).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="기본값 복원", command=restore_defaults, font=FONTS["body"], width=10).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="취소", command=lambda: (_cleanup_popup_wheel(), popup.destroy()),
              font=FONTS["body"], width=10).pack(side=tk.RIGHT, padx=5)


# ============================================================
# Phase 10-1: Menu bar
# ============================================================
def create_menu_bar(root):
    menubar = tk.Menu(root)

    # File menu
    file_menu = tk.Menu(menubar, tearoff=0)
    file_menu.add_command(label="설정 새로고침 (F5)", command=reload_config)
    file_menu.add_command(label="설정", command=open_settings_popup)
    file_menu.add_separator()
    file_menu.add_command(label="종료 (Ctrl+Q)", command=on_closing)
    menubar.add_cascade(label="파일", menu=file_menu)

    # Stock menu
    stock_menu = tk.Menu(menubar, tearoff=0)
    stock_menu.add_command(label="종목 추가 (Ctrl+A)", command=add_ticker)
    stock_menu.add_command(label="종목 삭제 (Ctrl+D)", command=remove_ticker)
    stock_menu.add_separator()
    stock_menu.add_command(label="전체 새로고침 (Ctrl+R)", command=refresh_table)
    menubar.add_cascade(label="종목", menu=stock_menu)

    # View menu
    view_menu = tk.Menu(menubar, tearoff=0)
    view_menu.add_command(label="단기", command=lambda: _set_view("short"))
    view_menu.add_command(label="중기", command=lambda: _set_view("middle"))
    view_menu.add_command(label="장기", command=lambda: _set_view("long"))
    view_menu.add_command(label="사용자 지정", command=lambda: _set_view("custom"))
    menubar.add_cascade(label="보기", menu=view_menu)

    # Analysis menu
    from portfolio_analysis import (open_correlation_popup, open_portfolio_popup,
                                    open_optimization_popup, open_portfolio_evaluation_popup,
                                    open_black_litterman_popup, open_fama_french_popup)
    analysis_menu = tk.Menu(menubar, tearoff=0)
    analysis_menu.add_command(label="포트폴리오 평가 (Ctrl+P)",
                              command=lambda: open_portfolio_evaluation_popup(app.watchlist, app.holdings))
    analysis_menu.add_separator()
    analysis_menu.add_command(label="상관관계 매트릭스",
                              command=lambda: open_correlation_popup(app.watchlist, app.holdings))
    analysis_menu.add_command(label="포트폴리오 분석",
                              command=lambda: open_portfolio_popup(app.watchlist, app.holdings))
    analysis_menu.add_command(label="포트폴리오 최적화",
                              command=lambda: open_optimization_popup(app.watchlist, app.holdings))
    analysis_menu.add_separator()
    analysis_menu.add_command(label="Black-Litterman 최적화",
                              command=lambda: open_black_litterman_popup(app.watchlist, app.holdings))
    analysis_menu.add_command(label="Fama-French 팩터 분석",
                              command=lambda: open_fama_french_popup(app.watchlist, app.holdings))
    analysis_menu.add_separator()
    from screener_popup import open_screener_popup
    analysis_menu.add_command(label="퀀트 종목 스크리너 (Ctrl+Shift+S)",
                              command=lambda: open_screener_popup(app_state=app))
    menubar.add_cascade(label="분석", menu=analysis_menu)

    # Help menu
    help_menu = tk.Menu(menubar, tearoff=0)
    help_menu.add_command(label="용어 설명", command=show_help_window)
    help_menu.add_command(label="퀀트 투자 가이드", command=show_quant_guide)
    help_menu.add_separator()
    help_menu.add_command(label="정보", command=lambda: messagebox.showinfo(
        "정보", "미국 주식 모니터링 v2.0\n\nYahoo Finance 기반 실시간 분석"))
    menubar.add_cascade(label="도움말", menu=help_menu)

    root.config(menu=menubar)


def _set_view(mode):
    """Switch view mode from menu."""
    app.radio_var.set(mode)
    on_radio_select()


# ============================================================
# Phase 10-3: Keyboard shortcuts
# ============================================================
def bind_shortcuts(root):
    root.bind("<Control-a>", lambda e: add_ticker())
    root.bind("<Control-A>", lambda e: add_ticker())
    root.bind("<Control-d>", lambda e: remove_ticker())
    root.bind("<Control-D>", lambda e: remove_ticker())
    root.bind("<Control-r>", lambda e: refresh_table())
    root.bind("<Control-R>", lambda e: refresh_table())
    root.bind("<F5>", lambda e: reload_config())
    root.bind("<Control-q>", lambda e: on_closing())
    root.bind("<Control-Q>", lambda e: on_closing())
    root.bind("<Return>", lambda e: on_item_double_click(e) if app.table.selection() else None)
    from portfolio_analysis import open_portfolio_evaluation_popup
    root.bind("<Control-p>", lambda e: open_portfolio_evaluation_popup(app.watchlist, app.holdings))
    root.bind("<Control-P>", lambda e: open_portfolio_evaluation_popup(app.watchlist, app.holdings))
    root.bind("<Control-Shift-s>", lambda e: open_screener_popup(app_state=app))
    root.bind("<Control-Shift-S>", lambda e: open_screener_popup(app_state=app))


# ============================================================
# Column header hover tooltip
# ============================================================
def _on_table_heading_motion(event):
    """컬럼 헤더에 마우스를 올리면 COLUMN_HELP 툴팁 표시."""
    region = app.table.identify_region(event.x, event.y)
    if region == "heading":
        col_id = app.table.identify_column(event.x)
        try:
            col_index = int(col_id.replace("#", "")) - 1
            # displaycolumns가 설정된 경우 시각적 순서를 사용해야 함
            dc = app.table["displaycolumns"]
            if dc == ("#all",) or dc == "#all":
                display_cols = list(app.table["columns"])
            else:
                display_cols = list(dc)
            if 0 <= col_index < len(display_cols):
                col_name = display_cols[col_index]
                # 정렬 표시 제거
                col_name = col_name.rstrip(" ▲▼")
                help_text = COLUMN_HELP.get(col_name, "")
                if help_text:
                    _show_heading_tooltip(event, help_text)
                    return
        except (ValueError, IndexError):
            pass
    _hide_heading_tooltip()


def _show_heading_tooltip(event, text):
    """헤딩 툴팁 표시."""
    _hide_heading_tooltip()
    x = app.table.winfo_rootx() + event.x + 10
    y = app.table.winfo_rooty() + event.y + 20
    tw = tk.Toplevel(app.table)
    tw.wm_overrideredirect(True)
    tw.wm_geometry(f"+{x}+{y}")
    label = tk.Label(tw, text=text, justify=tk.LEFT, background="#ffffe0",
                     relief=tk.SOLID, borderwidth=1, font=("Arial", 10),
                     wraplength=350)
    label.pack(ipadx=6, ipady=4)
    app._column_help_tip = tw


def _hide_heading_tooltip(event=None):
    """헤딩 툴팁 숨김."""
    if app._column_help_tip:
        app._column_help_tip.destroy()
        app._column_help_tip = None


# ============================================================
# Help window (전체 용어 설명 별도 창)
# ============================================================
def show_help_window():
    """Help 메뉴에서 전체 용어 설명 창을 띄운다."""
    win = tk.Toplevel(app.root)
    win.title("용어 설명")
    win.state('zoomed')
    win.minsize(500, 400)

    canvas = tk.Canvas(win)
    scrollbar = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas)

    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # 컬럼 설명
    tk.Label(scroll_frame, text="테이블 컬럼 설명", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 4))
    for col, desc in COLUMN_HELP.items():
        tk.Label(scroll_frame, text=f"[{col}]", font=("Arial", 10, "bold"), anchor="w").pack(anchor="w", padx=14, pady=(4, 0))
        tk.Label(scroll_frame, text=desc, font=("Arial", 10), anchor="w", justify=tk.LEFT, wraplength=550).pack(anchor="w", padx=24, pady=(0, 2))

    ttk.Separator(scroll_frame, orient="horizontal").pack(fill=tk.X, padx=10, pady=8)

    # 신호 설명
    tk.Label(scroll_frame, text="모멘텀 신호 설명", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(4, 4))
    for signal, (en_name, desc) in SIGNAL_HELP.items():
        tk.Label(scroll_frame, text=f"{signal} ({en_name})", font=("Arial", 10, "bold"), anchor="w").pack(anchor="w", padx=14, pady=(4, 0))
        tk.Label(scroll_frame, text=desc, font=("Arial", 10), anchor="w", justify=tk.LEFT, wraplength=550).pack(anchor="w", padx=24, pady=(0, 2))

    # 마우스 휠 스크롤
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _on_mousewheel)
    win.protocol("WM_DELETE_WINDOW", lambda: (canvas.unbind_all("<MouseWheel>"), win.destroy()))


# ============================================================
# Quant guide popup
# ============================================================
def show_quant_guide():
    """퀀트 투자 가이드 팝업."""
    win = tk.Toplevel(app.root)
    win.title("퀀트 투자 가이드")
    win.state('zoomed')
    win.minsize(550, 450)

    canvas = tk.Canvas(win)
    scrollbar = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas)

    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    tk.Label(scroll_frame, text="퀀트 투자 가이드", font=("Arial", 14, "bold")).pack(
        anchor="w", padx=10, pady=(10, 6))

    for category, content in QUANT_GUIDE.items():
        tk.Label(scroll_frame, text=category, font=("Arial", 11, "bold"),
                 fg="#1A5276", anchor="w").pack(anchor="w", padx=14, pady=(8, 2))
        tk.Label(scroll_frame, text=content, font=("Arial", 10), anchor="w",
                 justify=tk.LEFT, wraplength=600).pack(anchor="w", padx=24, pady=(0, 4))
        ttk.Separator(scroll_frame, orient="horizontal").pack(fill=tk.X, padx=10, pady=4)

    # 마우스 휠 스크롤
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _on_mousewheel)
    win.protocol("WM_DELETE_WINDOW", lambda: (canvas.unbind_all("<MouseWheel>"), win.destroy()))


# ============================================================
# Main entry point
# ============================================================
def main():
    config.ensure_watchlist_file()

    root = tk.Tk()
    app.root = root
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.withdraw()
    splash = show_splash(root)
    root.title('미국 주식 모니터링')
    # Phase 9-5: Minimum window size
    root.minsize(900, 600)
    root.state('zoomed')  # 전체화면으로 시작

    # Phase 10-1: Menu bar
    create_menu_bar(root)

    # Phase 10-3: Keyboard shortcuts
    bind_shortcuts(root)


    app.market_status_label = tk.Label(root, text="주식장 종료\n한국 시간:\n미국 시간:",
                                       font=FONTS["title"])
    app.market_status_label.pack(pady=PADDING_MD)

    # Button frame
    button_frame = tk.Frame(root)
    button_frame.pack(pady=PADDING_MD)

    add_button = tk.Button(button_frame, text="종목 추가", command=add_ticker, font=FONTS["body"])
    add_button.pack(side=tk.LEFT, padx=PADDING_MD)
    Tooltip(add_button, "종목 추가 (Ctrl+A)")

    remove_button = tk.Button(button_frame, text="종목 삭제", command=remove_ticker, font=FONTS["body"])
    remove_button.pack(side=tk.LEFT, padx=PADDING_MD)
    Tooltip(remove_button, "선택 종목 삭제 (Ctrl+D)")

    refresh_button = tk.Button(button_frame, text="새로고침", command=refresh_table, font=FONTS["body"])
    refresh_button.pack(side=tk.LEFT, padx=PADDING_MD)
    Tooltip(refresh_button, "전체 새로고침 (Ctrl+R)")

    holdings_btn = tk.Button(button_frame, text="보유 편집", command=edit_holding_for_selected, font=FONTS["body"])
    holdings_btn.pack(side=tk.LEFT, padx=PADDING_MD)
    Tooltip(holdings_btn, "선택 종목 보유 정보 편집")

    def export_to_excel():
        """테이블 데이터를 엑셀 파일로 내보내기."""
        from tkinter import filedialog
        import csv

        # displaycolumns 순서로 헤더 가져오기
        dc = app.table["displaycolumns"]
        if dc == ("#all",) or dc == "#all":
            headers = list(app.table["columns"])
        else:
            headers = list(dc)

        rows = []
        for item in app.table.get_children():
            all_values = app.table.item(item, "values")
            # 논리 컬럼 순서 → 이름 매핑
            logical_cols = list(app.table["columns"])
            val_map = dict(zip(logical_cols, all_values))
            # display 순서로 재배열
            rows.append([val_map.get(h, "") for h in headers])

        if not rows:
            messagebox.showwarning("내보내기", "내보낼 데이터가 없습니다.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV (Excel 호환)", "*.csv"), ("All files", "*.*")],
            initialfile=f"stock_monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)
            messagebox.showinfo("내보내기 완료", f"파일이 저장되었습니다:\n{path}")
        except Exception as e:
            messagebox.showerror("내보내기 실패", f"저장 중 오류:\n{e}")

    export_btn = tk.Button(button_frame, text="엑셀 저장", command=export_to_excel, font=FONTS["body"])
    export_btn.pack(side=tk.LEFT, padx=PADDING_MD)
    Tooltip(export_btn, "테이블 데이터를 CSV 파일로 저장 (엑셀에서 열기 가능)")

    # Radio buttons with LabelFrame (Phase 9-4)
    app.radio_var = tk.StringVar(value=config.config["view_mode"])

    radio_label_frame = tk.LabelFrame(root, text="분석 기간 선택", font=FONTS["body"])
    radio_label_frame.pack(pady=PADDING_SM, padx=PADDING_MD)

    radio_inner = tk.Frame(radio_label_frame)
    radio_inner.pack(pady=PADDING_SM, padx=PADDING_SM)

    short_radio = tk.Radiobutton(radio_inner, text="단기", variable=app.radio_var, value="short",
                                  command=on_radio_select, font=FONTS["body"])
    middle_radio = tk.Radiobutton(radio_inner, text="중기", variable=app.radio_var, value="middle",
                                   command=on_radio_select, font=FONTS["body"])
    long_radio = tk.Radiobutton(radio_inner, text="장기", variable=app.radio_var, value="long",
                                 command=on_radio_select, font=FONTS["body"])

    custom_radio = tk.Radiobutton(radio_inner, text="사용자 지정", variable=app.radio_var, value="custom",
                                    command=on_radio_select, font=FONTS["body"])

    short_radio.pack(side=tk.LEFT, padx=PADDING_MD)
    middle_radio.pack(side=tk.LEFT, padx=PADDING_MD)
    long_radio.pack(side=tk.LEFT, padx=PADDING_MD)
    custom_radio.pack(side=tk.LEFT, padx=PADDING_MD)

    # Phase 10-5: Tooltips on radio buttons (설정값 기반 동적 생성)
    app._radio_tooltips = {
        "short": Tooltip(short_radio, _format_preset_tooltip("short")),
        "middle": Tooltip(middle_radio, _format_preset_tooltip("middle")),
        "long": Tooltip(long_radio, _format_preset_tooltip("long")),
    }
    Tooltip(custom_radio, "직접 시작/종료 날짜를 입력합니다")

    # Phase 9-4: Period info label (2줄: period/interval + 날짜 범위)
    p = config.config["current"]["period"]
    i = config.config["current"]["interval"]
    app.period_info_label = tk.Label(radio_label_frame, text=f"{p} / {i}", font=FONTS["small"], fg="#333333")
    app.period_info_label.pack(pady=(2, 0))
    app.date_range_label = tk.Label(radio_label_frame, text=_format_date_range_text(p), font=FONTS["small"], fg="#333333")
    app.date_range_label.pack(pady=(0, 2))

    app._radio_label_frame = radio_label_frame

    # 사용자 지정 날짜 입력 프레임 (기본 숨김)
    custom_date_frame = tk.Frame(root)
    app._custom_date_frame = custom_date_frame

    _now = datetime.now()
    _one_year_ago = _now - timedelta(days=365)

    try:
        from tkcalendar import DateEntry as _MainDateEntry
        _main_has_calendar = True
    except ImportError:
        _main_has_calendar = False

    _saved_start = config.config["current"].get("start_date", _one_year_ago.strftime('%Y-%m-%d'))
    _saved_end = config.config["current"].get("end_date", _now.strftime('%Y-%m-%d'))

    tk.Label(custom_date_frame, text="시작:", font=FONTS["body"]).pack(side=tk.LEFT, padx=(PADDING_MD, 2))
    if _main_has_calendar:
        _s = datetime.strptime(_saved_start, '%Y-%m-%d')
        app._custom_start_entry = _MainDateEntry(custom_date_frame, width=10, font=FONTS["body"],
                                                  date_pattern="yyyy-mm-dd",
                                                  year=_s.year, month=_s.month, day=_s.day, locale="ko_KR")
    else:
        app._custom_start_entry = tk.Entry(custom_date_frame, width=12, font=FONTS["body"])
        app._custom_start_entry.insert(0, _saved_start)
    app._custom_start_entry.pack(side=tk.LEFT, padx=2)

    tk.Label(custom_date_frame, text="종료:", font=FONTS["body"]).pack(side=tk.LEFT, padx=(PADDING_MD, 2))
    if _main_has_calendar:
        _e = datetime.strptime(_saved_end, '%Y-%m-%d')
        app._custom_end_entry = _MainDateEntry(custom_date_frame, width=10, font=FONTS["body"],
                                                date_pattern="yyyy-mm-dd",
                                                year=_e.year, month=_e.month, day=_e.day, locale="ko_KR")
    else:
        app._custom_end_entry = tk.Entry(custom_date_frame, width=12, font=FONTS["body"])
        app._custom_end_entry.insert(0, _saved_end)
    app._custom_end_entry.pack(side=tk.LEFT, padx=2)

    custom_apply_btn = tk.Button(custom_date_frame, text="적용", command=on_radio_select, font=FONTS["body"])
    custom_apply_btn.pack(side=tk.LEFT, padx=PADDING_MD)

    # "custom" 모드이면 날짜 프레임 표시
    if app.radio_var.get() == "custom":
        custom_date_frame.pack(after=radio_label_frame, pady=(0, PADDING_SM), padx=PADDING_MD)

    # PanedWindow: 테이블 + 뉴스 패널
    # 마우스 조작 안내 (그리드 바로 위, 오른쪽 정렬 3줄)
    hint_frame = tk.Frame(root)
    hint_frame.pack(fill=tk.X, padx=PADDING_MD, pady=(0, 2))
    hint_inner = tk.Frame(hint_frame)
    hint_inner.pack(side=tk.RIGHT)
    for hint_text in [
        "\u25B6 클릭: 종목 선택",
        "\u25B6 더블클릭: 백테스트 실행",
        "\u25B6 우클릭: 컨텍스트 메뉴",
    ]:
        tk.Label(
            hint_inner, text=hint_text,
            font=("Arial", 9, "bold"), fg="#4A90D9", anchor="e"
        ).pack(anchor="e")

    paned = tk.PanedWindow(root, orient=tk.VERTICAL, sashwidth=8, sashrelief=tk.RAISED,
                           sashcursor="sb_v_double_arrow", opaqueresize=True, bg="#C0C0C0")
    paned.pack(fill=tk.BOTH, expand=True, padx=PADDING_MD, pady=(0, PADDING_MD))

    # Table frame with grid resize (Phase 9-6)
    table_frame = tk.Frame(paned)
    table_frame.grid_rowconfigure(0, weight=1)
    table_frame.grid_columnconfigure(0, weight=1)

    # 퀀트 가이드 권장 흐름: ADX추세확인 → MACD/MA → RSI/BB/스토캐스틱 → 거래량검증
    columns = ("종목명", "현재가", "수익률", "모멘텀 신호",
               "ADX", "추세 신호", "MACD 신호",
               "RSI 신호", "BB 신호", "스토캐스틱",
               "VWAP", "OBV", "거래량",
               "다이버전스", "변동성",
               "가치 점수", "PER", "ROE", "52주 위치", "실적발표", "공매도%", "내부자%",
               "일목균형표", "차트패턴",
               "보유수량", "매수가", "평가손익")
    app.table = ttk.Treeview(table_frame, columns=columns, show="headings")
    vsb = ttk.Scrollbar(table_frame, orient="vertical", command=app.table.yview)
    hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=app.table.xview)
    app.table.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    app.table.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")

    # Phase 9-2: Column alignment
    col_config = {
        "종목명": {"width": 150, "anchor": "w"},
        "현재가": {"width": 100, "anchor": "e"},
        "추세 신호": {"width": 200, "anchor": "center"},
        "RSI 신호": {"width": 150, "anchor": "center"},
        "수익률": {"width": 100, "anchor": "e"},
        "MACD 신호": {"width": 150, "anchor": "center"},
        "BB 신호": {"width": 150, "anchor": "center"},
        "모멘텀 신호": {"width": 150, "anchor": "center"},
        "가치 점수": {"width": 120, "anchor": "center"},
        "PER": {"width": 70, "anchor": "e"},
        "ROE": {"width": 70, "anchor": "e"},
        "52주 위치": {"width": 80, "anchor": "e"},
        "거래량": {"width": 90, "anchor": "center"},
        "변동성": {"width": 75, "anchor": "center"},
        "다이버전스": {"width": 80, "anchor": "center"},
        "ADX": {"width": 80, "anchor": "center"},
        "VWAP": {"width": 100, "anchor": "center"},
        "OBV": {"width": 70, "anchor": "center"},
        "스토캐스틱": {"width": 80, "anchor": "center"},
        "실적발표": {"width": 65, "anchor": "center"},
        "공매도%": {"width": 70, "anchor": "e"},
        "내부자%": {"width": 70, "anchor": "e"},
        "일목균형표": {"width": 80, "anchor": "center"},
        "차트패턴": {"width": 80, "anchor": "center"},
        "보유수량": {"width": 80, "anchor": "e"},
        "매수가": {"width": 90, "anchor": "e"},
        "평가손익": {"width": 130, "anchor": "e"},
    }

    # 저장된 컬럼 너비 로드
    saved_col_widths = config.config.get("column_widths", {})

    for col in columns:
        cfg = col_config[col]
        # 저장된 너비가 있으면 사용, 없으면 기본값
        width = saved_col_widths.get(col, cfg["width"])
        # Phase 10-4: Sortable column headers
        app.table.heading(col, text=col, command=lambda c=col: sort_by_column(c))
        app.table.column(col, width=width, minwidth=40, anchor=cfg["anchor"], stretch=False)

    # Phase 9-3: Row color tags
    app.table.tag_configure("buy", background=COLORS["buy"])
    app.table.tag_configure("sell", background=COLORS["sell"])
    app.table.tag_configure("hold", background=COLORS["hold"])
    app.table.tag_configure("strong_buy", background=COLORS["strong_buy"], foreground="white")
    app.table.tag_configure("strong_sell", background=COLORS["strong_sell"], foreground="white")
    # Phase 11-6: Price change highlight
    app.table.tag_configure("price_changed", background=COLORS["highlight"])
    # 저유동 경고 태그
    app.table.tag_configure("low_liquidity", background="#FFFACD")
    # 보유 종목 표시 태그
    app.table.tag_configure("has_holding", background="#E3F2FD")

    # 저장된 컬럼 순서 복원
    saved_col_order = config.config.get("column_order", None)
    if saved_col_order:
        # 저장된 순서에 있는 컬럼만 필터링 (삭제/추가된 컬럼 대응)
        valid_order = [c for c in saved_col_order if c in columns]
        # 새로 추가된 컬럼은 뒤에 붙임
        for c in columns:
            if c not in valid_order:
                valid_order.append(c)
        app.table["displaycolumns"] = valid_order

    # 헤더 드래그로 컬럼 순서 이동
    _drag_state = {"col": None, "indicator": None}

    def _get_display_columns():
        dc = app.table["displaycolumns"]
        if dc == ("#all",) or dc == "#all":
            return list(columns)
        return list(dc)

    def _header_press(event):
        region = app.table.identify_region(event.x, event.y)
        if region != "heading":
            return
        col_id = app.table.identify_column(event.x)
        if not col_id:
            return
        col_idx = int(col_id.replace("#", "")) - 1
        dc = _get_display_columns()
        if 0 <= col_idx < len(dc):
            _drag_state["col"] = dc[col_idx]
            # 드래그 인디케이터 표시
            indicator = tk.Label(app.table, text=f"  {dc[col_idx]}  ",
                                 bg="#4A90D9", fg="white", font=FONTS["body"],
                                 relief=tk.RAISED, padx=4, pady=2)
            _drag_state["indicator"] = indicator

    def _header_motion(event):
        if _drag_state["col"] is None:
            return
        ind = _drag_state["indicator"]
        if ind:
            ind.place(x=event.x - 30, y=event.y - 10)

    def _header_release(event):
        src_col = _drag_state["col"]
        ind = _drag_state["indicator"]
        if ind:
            ind.destroy()
        _drag_state["indicator"] = None
        _drag_state["col"] = None
        if src_col is None:
            return

        region = app.table.identify_region(event.x, event.y)
        if region != "heading":
            return
        col_id = app.table.identify_column(event.x)
        if not col_id:
            return
        col_idx = int(col_id.replace("#", "")) - 1
        dc = _get_display_columns()
        if col_idx < 0 or col_idx >= len(dc):
            return
        dst_col = dc[col_idx]
        if src_col == dst_col:
            return

        # 순서 변경
        dc.remove(src_col)
        new_idx = dc.index(dst_col)
        dc.insert(new_idx, src_col)
        app.table["displaycolumns"] = dc

        # config에 저장
        config.config["column_order"] = dc
        config.save_config(config.get_config())

    app.table.bind("<ButtonPress-1>", _header_press, add=True)
    app.table.bind("<B1-Motion>", _header_motion, add=True)
    app.table.bind("<ButtonRelease-1>", _header_release, add=True)

    # Phase 10-2: Right-click context menu
    app.table.bind("<Button-3>", show_context_menu)
    # Double-click for backtest
    app.table.bind("<Double-1>", on_item_double_click)
    # Column header hover tooltip
    app.table.bind("<Motion>", _on_table_heading_motion)
    app.table.bind("<Leave>", _hide_heading_tooltip)

    # PanedWindow에 테이블 프레임 추가
    paned.add(table_frame, minsize=150, stretch="always")

    # 뉴스 패널
    app.news_panel = NewsPanel(paned, app_state=app)
    paned.add(app.news_panel, minsize=80, stretch="always")

    # 사시 드래그 그립 표시 (═══ 핸들)
    def _draw_sash_grip(event=None):
        """PanedWindow 사시에 그립 핸들 그리기."""
        try:
            sash_y = paned.sash_coord(0)[1]
            sash_h = 8  # sashwidth
            # 기존 그립 삭제
            for w in getattr(paned, '_grip_labels', []):
                w.place_forget()
                w.destroy()
            grip = tk.Label(paned, text="⋯⋯⋯", font=("Arial", 6), fg="#888888",
                            bg="#C0C0C0", cursor="sb_v_double_arrow")
            grip.place(relx=0.5, y=sash_y, anchor="center")
            grip.bind("<Button-1>", lambda e: None)  # 클릭 시 sash로 전달
            grip.bind("<B1-Motion>", lambda e: paned.sash_place(0, 0, e.y_root - paned.winfo_rooty()))
            paned._grip_labels = [grip]
        except (tk.TclError, IndexError):
            pass

    paned.bind("<Configure>", _draw_sash_grip)
    # sash 이동 시에도 그립 위치 업데이트
    paned.bind("<ButtonRelease-1>", lambda e: paned.after(50, _draw_sash_grip))

    # Phase 11-1: Status bar
    app.status_bar = tk.Frame(root, relief=tk.SUNKEN, bd=1)
    app.status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=0, pady=0)
    update_status_bar("프로그램 시작됨")

    # 콜백 등록 (backtest_popup에서 사용)
    app.save_watchlist = save_watchlist
    app.refresh_table_once = refresh_table_once

    # Load watchlist & holdings (fast, local file I/O only)
    load_watchlist()
    app.holdings = holdings_manager.load_holdings()
    update_market_status()

    # 윈도우 즉시 표시 (빈 테이블)
    splash.destroy()
    root.deiconify()
    update_status_bar("데이터 로딩 중...")

    def _deferred_initial_load():
        """윈도우 표시 후 주식 데이터와 뉴스를 병렬로 로드."""
        # 뉴스 로딩 상태 초기화
        app._news_loading_status = "뉴스: 로딩 중..."

        # 1) 주식 데이터 — 백그라운드 스레드
        def _fetch_stocks():
            refresh_table_once()
            # 데이터 로드 완료 후 모니터 스레드 시작
            app.monitor_thread = threading.Thread(target=monitor_stocks, daemon=True)
            app.monitor_thread.start()

        threading.Thread(target=_fetch_stocks, daemon=True).start()

        # 2) 뉴스 갱신 시작 (센티먼트 캐시 포함)
        _original_update_news = app.news_panel.update_news

        def _update_news_with_cache(news_list):
            with app.news_lock:
                app.cached_news_list = news_list or []
            app._news_loading_status = ''  # 뉴스 로딩 완료
            _original_update_news(news_list)

        app.news_panel.update_news = _update_news_with_cache
        start_news_refresh(app)

    root.after(100, _deferred_initial_load)
    root.mainloop()


if __name__ == "__main__":
    main()
