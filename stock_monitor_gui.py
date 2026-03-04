import glob
import json
import logging
import os
import re
import sys
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
from help_texts import COLUMN_HELP, SIGNAL_HELP
from market_trend_manager import guess_market_session
from stock_score import fetch_stock_data
from ui_components import Tooltip, HelpTooltip
from news_panel import NewsPanel, start_news_refresh

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
        self._help_panel = None  # 용어 설명 패널
        self.news_panel = None


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
    try:
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

    try:
        import yfinance as yf
        ticker_info = yf.Ticker(name_or_ticker).info
        company_name = ticker_info.get('shortName')
        if company_name:
            with app.watchlist_lock:  # Phase 2-1
                if name_or_ticker not in app.watchlist:
                    app.watchlist.append(name_or_ticker)
                    save_watchlist()
                    logging.info(f"[STOCK] {company_name} ({name_or_ticker}) added")
                    messagebox.showinfo("추가 완료", f"{company_name} ({name_or_ticker}) 추가되었습니다.")
                    refresh_table_once()
                else:
                    messagebox.showinfo("중복", f"{company_name} ({name_or_ticker})는 이미 감시 중입니다.")
        else:
            messagebox.showwarning("검색 실패", f"{name_or_ticker}에 대한 정보를 찾을 수 없습니다.")
    except (ConnectionError, TimeoutError) as e:
        logging.error(f"[STOCK] Network error adding {name_or_ticker}: {e}")
        messagebox.showwarning("네트워크 오류", f"네트워크 연결을 확인하세요.\n{e}")
    except Exception as e:
        logging.error(f"[STOCK] Error adding {name_or_ticker}: {e}")
        messagebox.showwarning("검색 실패", f"{name_or_ticker} 정보를 가져오는 중 오류가 발생했습니다.")


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
            with app.watchlist_lock:
                if ticker_to_remove in app.watchlist:
                    app.watchlist.remove(ticker_to_remove)
                    save_watchlist()
                    logging.info(f"[STOCK] {company_name_with_ticker} removed")
                    # Phase 11-5: Undo support
                    app.undo_ticker = ticker_to_remove
                    update_status_bar(f"{ticker_to_remove} 삭제됨", undo=True)
                    refresh_table_once()
                else:
                    messagebox.showwarning("없음", f"{ticker_to_remove}은 감시 리스트에 없습니다.")
        else:
            messagebox.showwarning("형식 오류", f"티커를 추출할 수 없습니다: {company_name_with_ticker}")


def undo_delete():
    """Phase 11-5: Undo last delete."""
    if app.undo_ticker:
        with app.watchlist_lock:
            if app.undo_ticker not in app.watchlist:
                app.watchlist.append(app.undo_ticker)
                save_watchlist()
                logging.info(f"[STOCK] Undo delete: {app.undo_ticker}")
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
        update_status_bar("데이터 로딩 중...")  # Phase 11-2

        results = []

        def fetch_and_collect(t):
            result = fetch_stock_data(t)
            if result:
                results.append(result)

        with app.watchlist_lock:
            tickers = list(app.watchlist)

        # Phase 4-2: Reuse executor
        futures = [app.executor.submit(fetch_and_collect, t) for t in tickers]
        for f in futures:
            try:
                f.result(timeout=30)
            except Exception as e:
                logging.error(f"[FETCH] Thread error: {e}")

        update_table(results)
        app.last_refresh_time = datetime.now()
        update_status_bar()
    except Exception as e:
        logging.error(f"[REFRESH] refresh_table_once error: {e}")
        update_status_bar(f"갱신 오류: {e}")


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
    full_text = f"{status}\n분석기간: {period_display}, 간격: {interval}\n한국 시간: {korea_time}\n미국 시간: {new_york_time}"

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
            else:
                (name, t, price, trend, rsi, rate, rate_color, macd_signal, bb_signal, momentum_signal) = record

            rsi_value = float(rsi.replace('%', ''))
            if rsi_value > config.config['current']['rsi']['upper']:
                rsi_display = f"{rsi} (과매수)"
            elif rsi_value < config.config['current']['rsi']['lower']:
                rsi_display = f"{rsi} (과매도)"
            else:
                rsi_display = f"{rsi} (중립)"

            row_id = app.table.insert("", "end", values=(
                f"{name} ({t})",
                price,
                trend,
                rsi_display,
                rate,
                macd_signal,
                bb_signal,
                momentum_signal
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
            app.table.item(row_id, tags=(tag,))

            # Phase 11-6: Price change highlight
            prev_price = app.previous_data.get(t)
            if prev_price and prev_price != price:
                app.table.item(row_id, tags=(tag, "price_changed"))
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

        # Phase 9-2: Column widths
        min_widths = {
            "종목명": 150, "현재가": 100, "추세 신호": 200,
            "RSI 신호": 150, "수익률": 100, "MACD 신호": 150,
            "BB 신호": 150, "모멘텀 신호": 150
        }
        for col, width in min_widths.items():
            app.table.column(col, width=width, minwidth=width)

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
# Phase 10-2: Context menu
# ============================================================
def show_context_menu(event):
    """Right-click context menu on table."""
    row_id = app.table.identify_row(event.y)
    if row_id:
        app.table.selection_set(row_id)
        ctx_menu = tk.Menu(app.root, tearoff=0)
        ctx_menu.add_command(label="백테스트 실행", command=lambda: on_item_double_click(None))
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
def on_closing():
    logging.info("[STOP] Application shutting down...")
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
    popup.geometry("620x620")
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
    menubar.add_cascade(label="보기", menu=view_menu)

    # Help menu
    help_menu = tk.Menu(menubar, tearoff=0)
    help_menu.add_command(label="용어 설명", command=show_help_window)
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
            columns = app.table["columns"]
            if 0 <= col_index < len(columns):
                col_name = columns[col_index]
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
# Help panel toggle
# ============================================================
def toggle_help_panel():
    """'? 용어 설명' 버튼으로 도움말 팝업 표시/숨김."""
    if app._help_panel and app._help_panel.winfo_exists():
        app._help_panel.destroy()
        app._help_panel = None
        return

    popup = tk.Toplevel(app.root)
    popup.title("신호 용어 설명")
    popup.geometry("400x300")
    popup.minsize(350, 250)

    header_frame = tk.Frame(popup)
    header_frame.pack(fill=tk.X, padx=12, pady=(10, 6))

    for i, (signal, (en_name, desc)) in enumerate(SIGNAL_HELP.items()):
        fg = "#2E7D32" if "매수" in signal else "#E74C3C" if "매도" in signal else "#555"
        tk.Label(header_frame, text=signal, font=("Arial", 10, "bold"), width=10,
                 anchor="w", fg=fg).grid(row=i, column=0, padx=(0, 4))
        tk.Label(header_frame, text=desc, font=("Arial", 9), anchor="w",
                 fg="#555").grid(row=i, column=1, sticky="w")

    tk.Label(popup, text="컬럼 헤더에 마우스를 올리면 각 컬럼 설명이 표시됩니다.",
             font=("Arial", 8), fg="#333333").pack(padx=8, pady=(4, 6))

    tk.Button(popup, text="닫기", command=popup.destroy).pack(pady=(0, 8))

    app._help_panel = popup
    popup.protocol("WM_DELETE_WINDOW", lambda: (setattr(app, '_help_panel', None), popup.destroy()))


# ============================================================
# Help window (전체 용어 설명 별도 창)
# ============================================================
def show_help_window():
    """Help 메뉴에서 전체 용어 설명 창을 띄운다."""
    win = tk.Toplevel(app.root)
    win.title("용어 설명")
    win.geometry("600x500")
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

    help_toggle_btn = tk.Button(button_frame, text="? 용어 설명", command=toggle_help_panel, font=FONTS["body"])
    help_toggle_btn.pack(side=tk.LEFT, padx=PADDING_MD)
    Tooltip(help_toggle_btn, "신호 용어 설명 패널 열기/닫기")

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

    short_radio.pack(side=tk.LEFT, padx=PADDING_MD)
    middle_radio.pack(side=tk.LEFT, padx=PADDING_MD)
    long_radio.pack(side=tk.LEFT, padx=PADDING_MD)

    # Phase 10-5: Tooltips on radio buttons (설정값 기반 동적 생성)
    app._radio_tooltips = {
        "short": Tooltip(short_radio, _format_preset_tooltip("short")),
        "middle": Tooltip(middle_radio, _format_preset_tooltip("middle")),
        "long": Tooltip(long_radio, _format_preset_tooltip("long")),
    }

    # Phase 9-4: Period info label (2줄: period/interval + 날짜 범위)
    p = config.config["current"]["period"]
    i = config.config["current"]["interval"]
    app.period_info_label = tk.Label(radio_label_frame, text=f"{p} / {i}", font=FONTS["small"], fg="#333333")
    app.period_info_label.pack(pady=(2, 0))
    app.date_range_label = tk.Label(radio_label_frame, text=_format_date_range_text(p), font=FONTS["small"], fg="#333333")
    app.date_range_label.pack(pady=(0, 2))

    # PanedWindow: 테이블 + 뉴스 패널
    paned = tk.PanedWindow(root, orient=tk.VERTICAL, sashwidth=6, sashrelief=tk.RAISED)
    paned.pack(fill=tk.BOTH, expand=True, padx=PADDING_MD, pady=PADDING_MD)

    # Table frame with grid resize (Phase 9-6)
    table_frame = tk.Frame(paned)
    table_frame.grid_rowconfigure(0, weight=1)
    table_frame.grid_columnconfigure(0, weight=1)

    columns = ("종목명", "현재가", "추세 신호", "RSI 신호", "수익률", "MACD 신호", "BB 신호", "모멘텀 신호")
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
    }

    for col in columns:
        cfg = col_config[col]
        # Phase 10-4: Sortable column headers
        app.table.heading(col, text=col, command=lambda c=col: sort_by_column(c))
        app.table.column(col, width=cfg["width"], minwidth=cfg["width"], anchor=cfg["anchor"])

    # Phase 9-3: Row color tags
    app.table.tag_configure("buy", background=COLORS["buy"])
    app.table.tag_configure("sell", background=COLORS["sell"])
    app.table.tag_configure("hold", background=COLORS["hold"])
    app.table.tag_configure("strong_buy", background=COLORS["strong_buy"], foreground="white")
    app.table.tag_configure("strong_sell", background=COLORS["strong_sell"], foreground="white")
    # Phase 11-6: Price change highlight
    app.table.tag_configure("price_changed", background=COLORS["highlight"])

    # Phase 10-2: Right-click context menu
    app.table.bind("<Button-3>", show_context_menu)
    # Double-click for backtest
    app.table.bind("<Double-1>", on_item_double_click)
    # Column header hover tooltip
    app.table.bind("<Motion>", _on_table_heading_motion)
    app.table.bind("<Leave>", _hide_heading_tooltip)

    # PanedWindow에 테이블 프레임 추가
    paned.add(table_frame, stretch="always")

    # 뉴스 패널
    app.news_panel = NewsPanel(paned, app_state=app)
    paned.add(app.news_panel, minsize=80, stretch="never")

    # Phase 11-1: Status bar
    app.status_bar = tk.Frame(root, relief=tk.SUNKEN, bd=1)
    app.status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=0, pady=0)
    update_status_bar("프로그램 시작됨")

    # 콜백 등록 (backtest_popup에서 사용)
    app.save_watchlist = save_watchlist
    app.refresh_table_once = refresh_table_once

    # Load data
    load_watchlist()
    refresh_table_once()

    # Phase 2-4: Store thread reference
    app.monitor_thread = threading.Thread(target=monitor_stocks, daemon=True)
    app.monitor_thread.start()

    update_market_status()

    # 뉴스 갱신 시작
    start_news_refresh(app)

    splash.destroy()
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
