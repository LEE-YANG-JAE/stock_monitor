# screener_popup.py — 퀀트 종목 스크리너 UI (Tkinter 팝업)

import csv
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from quant_screener import (
    screen_universe, STRATEGY_NAMES, DEFAULT_MULTIFACTOR_WEIGHTS,
    calculate_buffett_score, calculate_graham_score, calculate_lynch_score,
    calculate_dividend_score, calculate_momentum_quant_score,
    calculate_multifactor_score, calculate_piotroski_fscore,
)
from stock_universe import get_universe, get_universe_names, load_custom_universe, save_custom_universe
from fundamental_score import calculate_valuation_score, safe_get_float
from help_texts import SCREENER_STRATEGY_HELP
from ui_components import HelpTooltip

logger = logging.getLogger(__name__)


def open_screener_popup(app_state=None):
    """퀀트 종목 스크리너 팝업을 엽니다."""
    popup = tk.Toplevel()
    popup.title("퀀트 종목 스크리너")
    popup.state("zoomed")  # 전체 화면
    popup.minsize(1100, 700)

    # 설정 로드
    try:
        from config import get_config, save_config
        cfg = get_config()
        screener_cfg = cfg.get("screener", {})
    except Exception:
        screener_cfg = {}

    # ============================================================
    # 상태 변수
    # ============================================================
    strategy_var = tk.StringVar(value=screener_cfg.get("last_strategy", "buffett"))
    universe_var = tk.StringVar(value=screener_cfg.get("last_universe", "S&P 500"))
    top_n_var = tk.IntVar(value=screener_cfg.get("top_n", 50))
    min_mcap_var = tk.DoubleVar(value=screener_cfg.get("min_market_cap_billions", 1.0))
    cancel_event = threading.Event()
    screening_thread = None
    current_results = []

    # 사용자 정의 필터 변수
    filter_vars = {}
    filter_keys = [
        ("per_min", "PER 최소"), ("per_max", "PER 최대"),
        ("pbr_min", "PBR 최소"), ("pbr_max", "PBR 최대"),
        ("roe_min", "ROE% 최소"), ("roe_max", "ROE% 최대"),
        ("debt_min", "부채비율 최소"), ("debt_max", "부채비율 최대"),
        ("div_min", "배당률% 최소"), ("div_max", "배당률% 최대"),
        ("peg_min", "PEG 최소"), ("peg_max", "PEG 최대"),
        ("om_min", "영업이익률% 최소"), ("om_max", "영업이익률% 최대"),
    ]
    for key, label in filter_keys:
        filter_vars[key] = tk.StringVar(value="")

    # 멀티팩터 가중치 변수
    w_cfg = screener_cfg.get("multifactor_weights", DEFAULT_MULTIFACTOR_WEIGHTS)
    weight_vars = {}
    for factor in ["value", "quality", "growth", "momentum", "dividend"]:
        weight_vars[factor] = tk.IntVar(value=w_cfg.get(factor, DEFAULT_MULTIFACTOR_WEIGHTS[factor]))

    # ============================================================
    # 레이아웃: 상단 컨트롤 + 결과 영역
    # ============================================================
    control_frame = ttk.LabelFrame(popup, text="스크리닝 설정", padding=8)
    control_frame.pack(fill="x", padx=8, pady=(8, 4))

    # --- 전략 선택 ---
    row1 = ttk.Frame(control_frame)
    row1.pack(fill="x", pady=2)

    ttk.Label(row1, text="전략:").pack(side="left", padx=(0, 5))
    strategies = list(STRATEGY_NAMES.items())
    for key, name in strategies:
        rb = ttk.Radiobutton(row1, text=name, variable=strategy_var, value=key)
        rb.pack(side="left", padx=3)
        help_text = SCREENER_STRATEGY_HELP.get(key)
        if help_text:
            HelpTooltip(rb, help_text, wraplength=400)

    # --- 유니버스/필터 ---
    row2 = ttk.Frame(control_frame)
    row2.pack(fill="x", pady=2)

    ttk.Label(row2, text="유니버스:").pack(side="left", padx=(0, 5))
    universe_combo = ttk.Combobox(row2, textvariable=universe_var,
                                   values=get_universe_names(), width=15, state="readonly")
    universe_combo.pack(side="left", padx=3)

    ttk.Label(row2, text="시가총액 최소($B):").pack(side="left", padx=(15, 5))
    ttk.Entry(row2, textvariable=min_mcap_var, width=6).pack(side="left", padx=3)

    ttk.Label(row2, text="상위 N개:").pack(side="left", padx=(15, 5))
    ttk.Entry(row2, textvariable=top_n_var, width=5).pack(side="left", padx=3)

    # 사용자 정의 유니버스 불러오기 버튼
    def load_custom():
        path = filedialog.askopenfilename(
            title="유니버스 파일 선택",
            filetypes=[("텍스트/CSV", "*.txt *.csv"), ("모든 파일", "*.*")])
        if path:
            tickers = load_custom_universe(path)
            if tickers:
                save_custom_universe(tickers)
                universe_var.set("사용자 정의")
                messagebox.showinfo("로드 완료", f"{len(tickers)}개 종목 로드됨")
            else:
                messagebox.showwarning("오류", "유효한 티커를 찾을 수 없습니다.")

    ttk.Button(row2, text="파일 불러오기", command=load_custom).pack(side="left", padx=(15, 3))

    # --- 사용자 정의 필터 (접힘/펼침) ---
    filter_visible = tk.BooleanVar(value=False)
    filter_frame_outer = ttk.Frame(control_frame)
    filter_frame_outer.pack(fill="x", pady=2)

    def toggle_filter():
        filter_visible.set(not filter_visible.get())
        if filter_visible.get():
            filter_inner.pack(fill="x", pady=2)
            toggle_btn.config(text="필터 접기 ▲")
        else:
            filter_inner.pack_forget()
            toggle_btn.config(text="사용자 정의 필터 ▼")

    toggle_btn = ttk.Button(filter_frame_outer, text="사용자 정의 필터 ▼",
                             command=toggle_filter)
    toggle_btn.pack(side="left")

    filter_inner = ttk.Frame(filter_frame_outer)
    # 필터 입력 그리드
    for i, (key, label) in enumerate(filter_keys):
        r, c = divmod(i, 4)
        ttk.Label(filter_inner, text=label + ":").grid(row=r, column=c * 2, padx=3, pady=1, sticky="e")
        ttk.Entry(filter_inner, textvariable=filter_vars[key], width=8).grid(
            row=r, column=c * 2 + 1, padx=3, pady=1, sticky="w")

    # --- 멀티팩터 가중치 (전략이 multifactor일 때만 표시) ---
    weight_frame = ttk.LabelFrame(control_frame, text="멀티팩터 가중치 (%)", padding=4)

    weight_labels = {"value": "밸류", "quality": "퀄리티", "growth": "성장",
                     "momentum": "모멘텀", "dividend": "배당"}
    for i, (factor, label) in enumerate(weight_labels.items()):
        ttk.Label(weight_frame, text=label + ":").grid(row=0, column=i * 2, padx=3)
        ttk.Entry(weight_frame, textvariable=weight_vars[factor], width=4).grid(
            row=0, column=i * 2 + 1, padx=3)

    def on_strategy_change(*_):
        if strategy_var.get() == "multifactor":
            weight_frame.pack(fill="x", pady=2, before=btn_frame)
        else:
            weight_frame.pack_forget()

    strategy_var.trace_add("write", on_strategy_change)

    # --- 버튼 ---
    btn_frame = ttk.Frame(control_frame)
    btn_frame.pack(fill="x", pady=4)

    progress_var = tk.DoubleVar(value=0)
    progress_label = ttk.Label(btn_frame, text="준비")
    progress_label.pack(side="left", padx=5)

    progress_bar = ttk.Progressbar(btn_frame, variable=progress_var, maximum=100, length=300)
    progress_bar.pack(side="left", padx=5, fill="x", expand=True)

    def start_screening():
        nonlocal screening_thread, current_results
        cancel_event.clear()

        # 유니버스 로드
        universe_name = universe_var.get()
        tickers = get_universe(universe_name)
        if not tickers:
            messagebox.showwarning("유니버스 없음", "선택한 유니버스에 종목이 없습니다.")
            return

        strategy = strategy_var.get()

        # 필터 수집
        filters = {}
        for key, _ in filter_keys:
            val = filter_vars[key].get().strip()
            if val:
                try:
                    filters[key] = float(val)
                except ValueError:
                    pass

        weights = None
        if strategy == "multifactor":
            weights = {f: weight_vars[f].get() for f in weight_vars}

        min_mcap = min_mcap_var.get()
        top_n = top_n_var.get()

        # 설정 저장
        try:
            cfg = get_config()
            if "screener" not in cfg:
                cfg["screener"] = {}
            cfg["screener"]["last_universe"] = universe_name
            cfg["screener"]["last_strategy"] = strategy
            cfg["screener"]["top_n"] = top_n
            cfg["screener"]["min_market_cap_billions"] = min_mcap
            if weights:
                cfg["screener"]["multifactor_weights"] = weights
            save_config(cfg)
        except Exception:
            pass

        # UI 초기화
        for item in result_tree.get_children():
            result_tree.delete(item)
        detail_text.delete("1.0", "end")
        start_btn.config(state="disabled")
        cancel_btn.config(state="normal")
        progress_var.set(0)
        progress_label.config(text="스크리닝 시작...")

        def progress_cb(done, total, ticker):
            def _update():
                pct = done / total * 100 if total > 0 else 0
                progress_var.set(pct)
                progress_label.config(text=f"{done}/{total} ({pct:.0f}%) - {ticker}")
            popup.after(0, _update)

        def run():
            nonlocal current_results
            try:
                results = screen_universe(
                    tickers, strategy=strategy, weights=weights,
                    custom_filters=filters if filters else None,
                    min_mcap=min_mcap, top_n=top_n,
                    progress_callback=progress_cb,
                    cancel_event=cancel_event,
                    max_workers=screener_cfg.get("max_workers", 8),
                )
                current_results = results
                popup.after(0, lambda: _populate_results(results))
            except Exception as e:
                logger.error(f"[SCREENER] Error: {e}")
                popup.after(0, lambda: messagebox.showerror("오류", str(e)))
            finally:
                popup.after(0, lambda: _screening_done())

        screening_thread = threading.Thread(target=run, daemon=True)
        screening_thread.start()

    def cancel_screening():
        cancel_event.set()
        cancel_btn.config(state="disabled")
        progress_label.config(text="취소 중...")

    def _screening_done():
        start_btn.config(state="normal")
        cancel_btn.config(state="disabled")
        n = len(current_results)
        if cancel_event.is_set():
            progress_label.config(text=f"취소됨 (부분 결과: {n}개)")
        else:
            progress_label.config(text=f"완료: {n}개 종목")
        progress_var.set(100)

    start_btn = ttk.Button(btn_frame, text="스크리닝 시작", command=start_screening)
    start_btn.pack(side="right", padx=5)

    cancel_btn = ttk.Button(btn_frame, text="취소", command=cancel_screening, state="disabled")
    cancel_btn.pack(side="right", padx=5)

    # 초기 전략에 따라 가중치 패널 표시
    on_strategy_change()

    # ============================================================
    # 결과 영역 (PanedWindow: 트리뷰 + 상세)
    # ============================================================
    paned = ttk.PanedWindow(popup, orient="vertical")
    paned.pack(fill="both", expand=True, padx=8, pady=4)

    # --- 결과 Treeview ---
    tree_frame = ttk.Frame(paned)
    paned.add(tree_frame, weight=3)

    columns = ("#", "종목명", "티커", "점수", "등급", "PER", "PBR", "ROE%",
               "부채%", "배당%", "52주%", "시가총액", "섹터")
    result_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=15)

    col_widths = {
        "#": 40, "종목명": 250, "티커": 70, "점수": 55, "등급": 80,
        "PER": 65, "PBR": 60, "ROE%": 65, "부채%": 65, "배당%": 60,
        "52주%": 60, "시가총액": 100, "섹터": 140,
    }
    for col in columns:
        result_tree.heading(col, text=col,
                             command=lambda c=col: _sort_column(result_tree, c, False))
        w = col_widths.get(col, 70)
        anchor = "w" if col in ("종목명", "섹터") else "center"
        result_tree.column(col, width=w, anchor=anchor)

    tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=result_tree.yview)
    result_tree.configure(yscrollcommand=tree_scroll.set)
    tree_scroll.pack(side="right", fill="y")
    result_tree.pack(fill="both", expand=True)

    # --- 상세 패널 ---
    detail_frame = ttk.LabelFrame(paned, text="종목 상세 분석", padding=5)
    paned.add(detail_frame, weight=2)

    detail_text = tk.Text(detail_frame, wrap="word", height=10,
                          font=("Consolas", 10), bg="#f8f8f8")
    detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=detail_text.yview)
    detail_text.configure(yscrollcommand=detail_scroll.set)
    detail_scroll.pack(side="right", fill="y")
    detail_text.pack(fill="both", expand=True)

    # --- 액션 버튼 바 ---
    action_frame = ttk.Frame(popup)
    action_frame.pack(fill="x", padx=8, pady=(0, 8))

    def add_to_watchlist():
        selected = result_tree.selection()
        if not selected:
            messagebox.showinfo("선택", "워치리스트에 추가할 종목을 선택하세요.")
            return
        if app_state is None:
            messagebox.showwarning("오류", "앱 상태를 찾을 수 없습니다.")
            return
        added = []
        for item in selected:
            vals = result_tree.item(item)["values"]
            ticker = str(vals[2])
            with app_state.watchlist_lock:
                if ticker not in app_state.watchlist:
                    app_state.watchlist.append(ticker)
                    added.append(ticker)
        if added:
            try:
                app_state.save_watchlist()
            except Exception:
                pass
            messagebox.showinfo("추가 완료", f"{', '.join(added)} 워치리스트에 추가됨")

    def run_backtest():
        selected = result_tree.selection()
        if not selected:
            messagebox.showinfo("선택", "백테스트할 종목을 선택하세요.")
            return
        vals = result_tree.item(selected[0])["values"]
        company = str(vals[1])
        ticker = str(vals[2])
        stock_str = f"{company} ({ticker})"
        try:
            from backtest_popup import open_backtest_popup
            open_backtest_popup(stock_str, app_state=app_state)
        except Exception as e:
            messagebox.showerror("오류", f"백테스트 실행 실패: {e}")

    def export_csv():
        if not current_results:
            messagebox.showinfo("결과 없음", "내보낼 결과가 없습니다.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV 파일", "*.csv")],
            initialfile="screener_results.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["순위", "종목명", "티커", "점수", "등급",
                                 "PER", "PBR", "ROE%", "부채%", "배당%",
                                 "52주%", "시가총액", "섹터", "적정가",
                                 "상승여력%"])
                for r in current_results:
                    m = r.raw_metrics
                    writer.writerow([
                        r.rank, r.company_name, r.ticker,
                        r.composite_score, r.judgment,
                        _fmt(m.get("per")), _fmt(m.get("pbr")),
                        _fmt(m.get("roe")), _fmt(m.get("debt_equity")),
                        _fmt(m.get("div_yield")), _fmt(m.get("52w_pct")),
                        _fmt_mcap(r.market_cap), r.sector,
                        _fmt(r.fair_price), _fmt(r.upside_pct),
                    ])
            messagebox.showinfo("내보내기 완료", f"결과가 저장되었습니다:\n{path}")
        except Exception as e:
            messagebox.showerror("오류", f"CSV 저장 실패: {e}")

    ttk.Button(action_frame, text="워치리스트 추가", command=add_to_watchlist).pack(side="left", padx=5)
    ttk.Button(action_frame, text="백테스트 실행", command=run_backtest).pack(side="left", padx=5)
    ttk.Button(action_frame, text="CSV 내보내기", command=export_csv).pack(side="left", padx=5)

    # ============================================================
    # 내부 함수
    # ============================================================

    def _populate_results(results):
        for item in result_tree.get_children():
            result_tree.delete(item)
        for r in results:
            m = r.raw_metrics
            result_tree.insert("", "end", values=(
                r.rank,
                r.company_name,
                r.ticker,
                f"{r.composite_score:.0f}",
                r.judgment,
                _fmt(m.get("per")),
                _fmt(m.get("pbr")),
                _fmt(m.get("roe")),
                _fmt(m.get("debt_equity")),
                _fmt(m.get("div_yield")),
                _fmt(m.get("52w_pct")),
                _fmt_mcap(r.market_cap),
                r.sector if r.sector else "N/A",
            ))

    def on_select(event):
        selected = result_tree.selection()
        if not selected:
            return
        vals = result_tree.item(selected[0])["values"]
        ticker = str(vals[2])
        # 결과에서 찾기
        result = None
        for r in current_results:
            if r.ticker == ticker:
                result = r
                break
        if result is None:
            return

        detail_text.delete("1.0", "end")
        _show_detail(result)

    result_tree.bind("<<TreeviewSelect>>", on_select)
    result_tree.bind("<Double-1>", lambda e: run_backtest())

    def _show_detail(r):
        detail_text.insert("end", f"{'=' * 50}\n")
        detail_text.insert("end", f"  {r.company_name} ({r.ticker})\n")
        detail_text.insert("end", f"  섹터: {r.sector}  |  시가총액: {_fmt_mcap(r.market_cap)}\n")
        detail_text.insert("end", f"  현재가: ${_fmt(r.current_price)}  |  "
                           f"적정가: ${_fmt(r.fair_price)}  |  "
                           f"상승여력: {_fmt(r.upside_pct)}%\n")
        detail_text.insert("end", f"{'=' * 50}\n\n")

        # 종합 점수
        detail_text.insert("end", f"  종합 점수: {r.composite_score:.0f}/100  [{r.judgment}]\n\n")

        # 팩터별 점수
        detail_text.insert("end", "  [팩터별 점수]\n")
        for factor, score in r.factor_scores.items():
            bar = _score_bar(score if isinstance(score, (int, float)) else 0, 30 if factor not in ("value", "quality", "growth", "momentum", "dividend") else 100)
            detail_text.insert("end", f"    {factor:12s}: {bar} {score}\n")

        # 핵심 지표
        m = r.raw_metrics
        detail_text.insert("end", f"\n  [핵심 지표]\n")
        metrics_display = [
            ("PER", m.get("per")), ("PBR", m.get("pbr")),
            ("ROE%", m.get("roe")), ("부채비율", m.get("debt_equity")),
            ("배당률%", m.get("div_yield")), ("PEG", m.get("peg")),
            ("영업이익률%", m.get("operating_margin")),
            ("이익성장%", m.get("earn_growth")),
            ("베타", m.get("beta")),
            ("유동비율", m.get("current_ratio")),
            ("배당성향%", m.get("payout_ratio")),
            ("내부자보유%", m.get("insider_pct")),
            ("52주 위치%", m.get("52w_pct")),
        ]
        for name, val in metrics_display:
            detail_text.insert("end", f"    {name:14s}: {_fmt(val)}\n")

    def _score_bar(score, max_val):
        """텍스트 기반 점수 바."""
        if max_val <= 0:
            return ""
        filled = int(score / max_val * 20)
        filled = max(0, min(20, filled))
        return "█" * filled + "░" * (20 - filled)

    # 트리뷰 정렬
    def _sort_column(tree, col, reverse):
        data = [(tree.set(k, col), k) for k in tree.get_children("")]
        try:
            data.sort(key=lambda t: float(t[0].replace(",", "").replace("$", "").replace("B", "").replace("M", "").replace("N/A", "-1").replace("-", "-1")),
                       reverse=reverse)
        except (ValueError, TypeError):
            data.sort(key=lambda t: t[0], reverse=reverse)
        for i, (_, k) in enumerate(data):
            tree.move(k, "", i)
        tree.heading(col, command=lambda: _sort_column(tree, col, not reverse))


def _fmt(val, decimals=1):
    """숫자 포맷팅. None → '-'."""
    if val is None:
        return "-"
    try:
        return f"{float(val):.{decimals}f}"
    except (ValueError, TypeError):
        return str(val)


def _fmt_mcap(val):
    """시가총액 포맷팅 (B/M 단위)."""
    if val is None:
        return "-"
    try:
        v = float(val)
        if v >= 1e12:
            return f"${v / 1e12:.1f}T"
        if v >= 1e9:
            return f"${v / 1e9:.1f}B"
        if v >= 1e6:
            return f"${v / 1e6:.0f}M"
        return f"${v:,.0f}"
    except (ValueError, TypeError):
        return "-"
