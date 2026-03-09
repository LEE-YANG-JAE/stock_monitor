# portfolio_analysis.py — 포트폴리오 분석 모듈
# 상관관계 매트릭스, 포트폴리오 성과, 섹터 분산 등

import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import config as config_module

plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.family'] = 'Malgun Gothic'


def _filter_watchlist(watchlist, holdings, filter_mode):
    """필터 모드에 따라 워치리스트 필터링.
    filter_mode: 'all' (종합), 'held' (보유만), 'not_held' (미보유만)
    """
    if filter_mode == 'all' or not holdings:
        return list(watchlist)
    try:
        import holdings_manager
    except ImportError:
        return list(watchlist)
    result = []
    for t in watchlist:
        h = holdings_manager.get_holding(holdings, t)
        is_held = h is not None and h.get("quantity", 0) > 0
        if filter_mode == 'held' and is_held:
            result.append(t)
        elif filter_mode == 'not_held' and not is_held:
            result.append(t)
    return result


def _add_filter_radio(parent, holdings, on_change_callback):
    """팝업 상단에 종합/보유/미보유 필터 라디오 추가. 반환: filter_var"""
    filter_frame = tk.Frame(parent)
    filter_frame.pack(fill=tk.X, padx=10, pady=(5, 0))

    filter_var = tk.StringVar(value="all")
    tk.Label(filter_frame, text="필터:", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(0, 5))
    for text, value in [("종합", "all"), ("보유 종목만", "held"), ("미보유 종목만", "not_held")]:
        rb = tk.Radiobutton(filter_frame, text=text, variable=filter_var, value=value,
                            command=on_change_callback, font=("Arial", 10))
        rb.pack(side=tk.LEFT, padx=5)

    if not holdings:
        # holdings 없으면 필터 비활성화
        for child in filter_frame.winfo_children():
            if isinstance(child, tk.Radiobutton):
                child.config(state=tk.DISABLED)

    return filter_var


def _download_returns(tickers, period="1y"):
    """워치리스트 종목들의 일일 수익률 데이터를 다운로드."""
    if not tickers:
        return pd.DataFrame()
    try:
        data = yf.download(tickers, period=period, interval="1d", group_by='ticker')
        if data.empty:
            return pd.DataFrame()

        closes = pd.DataFrame()
        if len(tickers) == 1:
            # 단일 종목은 MultiIndex가 아님
            if 'Close' in data.columns:
                closes[tickers[0]] = data['Close']
            elif isinstance(data.columns, pd.MultiIndex):
                closes[tickers[0]] = data[(tickers[0], 'Close')]
        else:
            for t in tickers:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        closes[t] = data[(t, 'Close')]
                    else:
                        closes[t] = data['Close']
                except (KeyError, TypeError):
                    continue

        returns = closes.pct_change().dropna()
        return returns
    except Exception as e:
        logging.error(f"[PORTFOLIO] Download error: {e}")
        return pd.DataFrame()


def open_correlation_popup(watchlist, holdings=None):
    """상관관계 매트릭스 히트맵 팝업."""
    if len(watchlist) < 2:
        messagebox.showinfo("알림", "상관관계 분석은 2개 이상의 종목이 필요합니다.")
        return

    popup = tk.Toplevel()
    popup.title("종목 간 상관관계 매트릭스")
    popup.state('zoomed')

    open_figs = []
    content_frame = tk.Frame(popup)
    content_frame.pack(fill=tk.BOTH, expand=True)

    def _run_analysis():
        # 기존 content 제거
        for widget in content_frame.winfo_children():
            widget.destroy()
        for f in open_figs:
            plt.close(f)
        open_figs.clear()

        filtered = _filter_watchlist(watchlist, holdings, filter_var.get())
        if len(filtered) < 2:
            tk.Label(content_frame, text="필터 결과 2개 이상의 종목이 필요합니다.", font=("Arial", 11)).pack(expand=True)
            return

        loading = tk.Label(content_frame, text=f"상관관계 데이터 다운로드 중... ({len(filtered)}종목)", font=("Arial", 12))
        loading.pack(expand=True)
        _corr_bar = ttk.Progressbar(content_frame, mode='indeterminate', length=300)
        _corr_bar.pack(pady=5)
        _corr_bar.start(15)

        def _compute():
            returns = _download_returns(filtered, period="1y")

            def _show(returns_df):
                loading.destroy()
                _corr_bar.destroy()
                if returns_df.empty or returns_df.shape[1] < 2:
                    tk.Label(content_frame, text="데이터를 가져올 수 없습니다.", font=("Arial", 11)).pack(expand=True)
                    return

                corr = returns_df.corr()

                # 히트맵
                fig, ax = plt.subplots(figsize=(8, 6))
                n = len(corr)
                im = ax.imshow(corr.values, cmap='RdYlGn', vmin=-1, vmax=1, aspect='auto')

                ax.set_xticks(range(n))
                ax.set_yticks(range(n))
                ax.set_xticklabels(corr.columns, rotation=45, ha='right', fontsize=9)
                ax.set_yticklabels(corr.index, fontsize=9)

                for i in range(n):
                    for j in range(n):
                        val = corr.values[i, j]
                        color = "white" if abs(val) > 0.7 else "black"
                        ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                                fontsize=8, color=color, fontweight="bold")

                fig.colorbar(im, ax=ax, label="상관계수")
                ax.set_title("종목 간 수익률 상관관계 (1년)", fontsize=13, fontweight="bold")
                plt.tight_layout()

                open_figs.append(fig)
                canvas = FigureCanvasTkAgg(fig, master=content_frame)
                canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
                canvas.draw()

                # 롤링 상관관계 차트 (60일 윈도우)
                if returns_df.shape[1] >= 2:
                    cols = returns_df.columns.tolist()
                    fig2, ax2 = plt.subplots(figsize=(8, 4))
                    for i in range(len(cols)):
                        for j in range(i + 1, min(len(cols), i + 4)):  # 최대 6쌍
                            rolling_corr = returns_df[cols[i]].rolling(60).corr(returns_df[cols[j]])
                            ax2.plot(rolling_corr.index, rolling_corr.values,
                                     label=f"{cols[i]}-{cols[j]}", linewidth=1)
                    ax2.axhline(y=0.8, color='red', linestyle='--', alpha=0.5, label='높은 상관 (0.8)')
                    ax2.axhline(y=0, color='gray', linestyle=':', alpha=0.5)
                    ax2.set_title("롤링 상관관계 (60일 윈도우)", fontsize=12, fontweight="bold")
                    ax2.set_ylabel("상관계수")
                    ax2.legend(fontsize=7, loc='upper left')
                    ax2.grid(alpha=0.3)
                    plt.tight_layout()

                    open_figs.append(fig2)
                    canvas2 = FigureCanvasTkAgg(fig2, master=content_frame)
                    canvas2.get_tk_widget().pack(fill=tk.BOTH, expand=True, pady=(5, 0))
                    canvas2.draw()

                # 해석 가이드
                guide = tk.Label(content_frame,
                                 text="0.7↑ 높은 양의 상관 (분산투자 효과 낮음) | 0~0.3 낮은 상관 (분산투자 효과 높음) | 음수 역상관 (헤지 효과)",
                                 font=("Arial", 9), fg="#555555")
                guide.pack(pady=3)

                # 경고: 높은 상관 종목 쌍
                high_corr_pairs = []
                for i in range(n):
                    for j in range(i+1, n):
                        if abs(corr.values[i, j]) >= 0.8:
                            high_corr_pairs.append(
                                f"{corr.columns[i]}-{corr.columns[j]}: {corr.values[i,j]:.2f}")
                if high_corr_pairs:
                    warn_text = "⚠ 높은 상관 종목: " + ", ".join(high_corr_pairs[:5])
                    tk.Label(content_frame, text=warn_text, font=("Arial", 9, "bold"), fg="#E74C3C").pack(pady=2)

            try:
                popup.after(0, lambda: _show(returns))
            except tk.TclError:
                pass

        threading.Thread(target=_compute, daemon=True).start()

    filter_var = _add_filter_radio(popup, holdings, _run_analysis)

    def _on_close():
        for f in open_figs:
            plt.close(f)
        popup.destroy()
    popup.protocol("WM_DELETE_WINDOW", _on_close)

    _run_analysis()


def open_portfolio_popup(watchlist, holdings=None):
    """포트폴리오 전체 분석 팝업 (성과, 섹터 분산, 베타)."""
    if not watchlist:
        messagebox.showinfo("알림", "워치리스트에 종목을 추가하세요.")
        return

    popup = tk.Toplevel()
    popup.title("포트폴리오 분석")
    popup.state('zoomed')

    open_figs = []

    def _run_analysis():
        # 기존 content 제거
        for widget in content_frame.winfo_children():
            widget.destroy()
        for f in open_figs:
            plt.close(f)
        open_figs.clear()

        filtered = _filter_watchlist(watchlist, holdings, filter_var.get())
        if not filtered:
            tk.Label(content_frame, text="필터 결과 종목이 없습니다.", font=("Arial", 11)).pack(expand=True)
            return

        # 스크롤
        scroll_canvas = tk.Canvas(content_frame, highlightthickness=0)
        vsb = ttk.Scrollbar(content_frame, orient="vertical", command=scroll_canvas.yview)
        inner = tk.Frame(scroll_canvas)
        inner.bind("<Configure>", lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.create_window((0, 0), window=inner, anchor="nw")
        scroll_canvas.configure(yscrollcommand=vsb.set)
        scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        loading = tk.Label(inner, text="준비 중...", font=("Arial", 12))
        loading.pack(pady=10)
        _pvar = tk.DoubleVar(value=0)
        _pbar = ttk.Progressbar(inner, variable=_pvar, maximum=100, length=300)
        _pbar.pack(pady=(0, 10))
        _data = {}

        def _update_progress(pct, text):
            try:
                popup.after(0, lambda p=pct, t=text: (_pvar.set(p), loading.config(text=t)))
            except tk.TclError:
                pass

        def _analyze():
            try:
                tickers = list(filtered)
                _update_progress(5, f"수익률 데이터 다운로드 중... ({len(tickers)}종목)")
                returns = _download_returns(tickers, period="1y")

                sector_map = {}
                beta_map = {}
                name_map = {}
                for i, t in enumerate(tickers):
                    _update_progress(20 + 50 * (i + 1) // len(tickers),
                                     f"종목 정보 수집 중... {t} ({i+1}/{len(tickers)})")
                    try:
                        info = yf.Ticker(t).info
                        sector_map[t] = info.get('sector', '기타')
                        beta_map[t] = info.get('beta', None)
                        name_map[t] = info.get('shortName', t)
                    except Exception:
                        sector_map[t] = '기타'
                        beta_map[t] = None
                        name_map[t] = t

                _data['returns'] = returns
                _data['sector_map'] = sector_map
                _data['beta_map'] = beta_map
                _data['name_map'] = name_map
                _data['tickers'] = tickers
                _update_progress(90, "결과 표시 중...")
                popup.after(0, _show)
            except Exception as e:
                logging.error(f"[PORTFOLIO] Analysis error: {e}")
                try:
                    popup.after(0, lambda: loading.config(text=f"오류 발생: {e}"))
                except tk.TclError:
                    pass

        def _show():
            try:
                if not popup.winfo_exists():
                    return
            except tk.TclError:
                return
            loading.destroy()
            _pbar.destroy()
            returns = _data.get('returns', pd.DataFrame())
            sector_map = _data.get('sector_map', {})
            beta_map = _data.get('beta_map', {})
            name_map = _data.get('name_map', {})
            tickers = _data.get('tickers', [])

            # --- 1. 섹터 분산 ---
            sector_frame = tk.LabelFrame(inner, text="섹터 분산", font=("Arial", 11, "bold"))
            sector_frame.pack(fill=tk.X, padx=10, pady=5)

            sector_counts = {}
            for t, s in sector_map.items():
                sector_counts[s] = sector_counts.get(s, 0) + 1

            total = len(tickers)
            for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
                pct = count / total * 100
                bar_text = f"{sector}: {count}종목 ({pct:.0f}%)"
                row = tk.Frame(sector_frame)
                row.pack(fill=tk.X, padx=8, pady=1)
                tk.Label(row, text=bar_text, font=("Arial", 9), anchor="w", width=35).pack(side=tk.LEFT)
                bar_canvas = tk.Canvas(row, width=200, height=14, highlightthickness=0)
                bar_canvas.pack(side=tk.LEFT, padx=5)
                bar_width = int(pct * 2)
                color = "#E74C3C" if pct > 50 else "#F39C12" if pct > 30 else "#2ECC71"
                bar_canvas.create_rectangle(0, 0, bar_width, 14, fill=color, outline="")

            if len(sector_counts) == 1:
                tk.Label(sector_frame, text="⚠ 단일 섹터 집중: 분산투자가 필요합니다",
                         font=("Arial", 9, "bold"), fg="#E74C3C").pack(padx=8, pady=2)

            # --- 2. 종목별 성과 ---
            if not returns.empty:
                perf_frame = tk.LabelFrame(inner, text="종목별 1년 성과", font=("Arial", 11, "bold"))
                perf_frame.pack(fill=tk.X, padx=10, pady=5)

                # 헤더
                header = tk.Frame(perf_frame)
                header.pack(fill=tk.X, padx=8, pady=2)
                for col_text, col_w in [("종목", 12), ("수익률", 10), ("변동성", 10),
                                         ("샤프", 8), ("베타", 8), ("섹터", 15)]:
                    tk.Label(header, text=col_text, font=("Arial", 9, "bold"),
                             width=col_w, anchor="center").pack(side=tk.LEFT)

                for t in tickers:
                    if t not in returns.columns:
                        continue
                    r = returns[t].dropna()
                    if r.empty:
                        continue

                    ann_return = r.mean() * 252
                    ann_vol = r.std() * np.sqrt(252)
                    sharpe = (ann_return - config_module.get_risk_free_rate()) / ann_vol if ann_vol > 0 else 0
                    beta = beta_map.get(t)

                    row = tk.Frame(perf_frame)
                    row.pack(fill=tk.X, padx=8, pady=1)

                    ret_color = "#2E7D32" if ann_return > 0 else "#E74C3C"
                    tk.Label(row, text=name_map.get(t, t)[:12], font=("Arial", 9), width=12, anchor="w").pack(side=tk.LEFT)
                    tk.Label(row, text=f"{ann_return:.1%}", font=("Arial", 9), width=10, fg=ret_color).pack(side=tk.LEFT)
                    tk.Label(row, text=f"{ann_vol:.1%}", font=("Arial", 9), width=10).pack(side=tk.LEFT)
                    tk.Label(row, text=f"{sharpe:.2f}", font=("Arial", 9), width=8).pack(side=tk.LEFT)
                    tk.Label(row, text=f"{beta:.2f}" if beta else "N/A", font=("Arial", 9), width=8).pack(side=tk.LEFT)
                    tk.Label(row, text=sector_map.get(t, ""), font=("Arial", 9), width=15, anchor="w").pack(side=tk.LEFT)

                # 포트폴리오 전체 (동일 비중)
                ttk.Separator(perf_frame).pack(fill=tk.X, padx=8, pady=3)
                valid_cols = [c for c in tickers if c in returns.columns]
                if valid_cols:
                    port_returns = returns[valid_cols].mean(axis=1)
                    port_ann = port_returns.mean() * 252
                    port_vol = port_returns.std() * np.sqrt(252)
                    port_sharpe = (port_ann - config_module.get_risk_free_rate()) / port_vol if port_vol > 0 else 0

                    betas = [beta_map[t] for t in valid_cols if beta_map.get(t) is not None]
                    port_beta = np.mean(betas) if betas else None

                    total_row = tk.Frame(perf_frame)
                    total_row.pack(fill=tk.X, padx=8, pady=2)
                    tc = "#2E7D32" if port_ann > 0 else "#E74C3C"
                    tk.Label(total_row, text="포트폴리오", font=("Arial", 9, "bold"), width=12, anchor="w").pack(side=tk.LEFT)
                    tk.Label(total_row, text=f"{port_ann:.1%}", font=("Arial", 9, "bold"), width=10, fg=tc).pack(side=tk.LEFT)
                    tk.Label(total_row, text=f"{port_vol:.1%}", font=("Arial", 9, "bold"), width=10).pack(side=tk.LEFT)
                    tk.Label(total_row, text=f"{port_sharpe:.2f}", font=("Arial", 9, "bold"), width=8).pack(side=tk.LEFT)
                    tk.Label(total_row, text=f"{port_beta:.2f}" if port_beta else "N/A",
                             font=("Arial", 9, "bold"), width=8).pack(side=tk.LEFT)

                # --- 3. 수익률 차트 ---
                fig, ax = plt.subplots(figsize=(8, 4))
                for t in valid_cols[:10]:  # 최대 10종목
                    cumulative = (1 + returns[t]).cumprod()
                    ax.plot(cumulative.index, cumulative.values, label=t, linewidth=1)

                # 포트폴리오 동일비중
                port_cum = (1 + port_returns).cumprod()
                ax.plot(port_cum.index, port_cum.values, label="포트폴리오", linewidth=2.5,
                        color="black", linestyle="--")

                ax.set_title("누적 수익률 비교 (1년)", fontsize=12, fontweight="bold")
                ax.set_ylabel("누적 수익률")
                ax.legend(loc="upper left", fontsize=8)
                ax.grid(alpha=0.3)
                plt.tight_layout()

                open_figs.append(fig)
                chart_canvas = FigureCanvasTkAgg(fig, master=inner)
                chart_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
                chart_canvas.draw()

                # --- 4. Diversification Ratio ---
                if len(valid_cols) >= 2:
                    try:
                        cov_matrix = returns[valid_cols].cov() * 252
                        individual_vols = returns[valid_cols].std() * np.sqrt(252)
                        weights = np.array([1.0 / len(valid_cols)] * len(valid_cols))
                        weighted_vol_sum = np.dot(weights, individual_vols.values)
                        port_vol_calc = np.sqrt(weights @ cov_matrix.values @ weights)
                        div_ratio = weighted_vol_sum / port_vol_calc if port_vol_calc > 0 else 0

                        div_frame = tk.LabelFrame(inner, text="리스크 지표", font=("Arial", 11, "bold"))
                        div_frame.pack(fill=tk.X, padx=10, pady=5)
                        tk.Label(div_frame,
                                 text=f"Diversification Ratio: {div_ratio:.2f}  (1.0 = 분산 효과 없음, 높을수록 분산 효과 큼)",
                                 font=("Arial", 10)).pack(padx=8, pady=4, anchor="w")
                    except Exception as e:
                        logging.warning(f"[PORTFOLIO] Diversification ratio error: {e}")

        threading.Thread(target=_analyze, daemon=True).start()

    filter_var = _add_filter_radio(popup, holdings, _run_analysis)
    content_frame = tk.Frame(popup)
    content_frame.pack(fill=tk.BOTH, expand=True)

    def _on_close():
        for f in open_figs:
            plt.close(f)
        popup.destroy()
    popup.protocol("WM_DELETE_WINDOW", _on_close)

    _run_analysis()


def _optimize_portfolio(returns, method='max_sharpe'):
    """포트폴리오 최적화.
    method: 'equal' (동일비중), 'min_var' (최소분산), 'max_sharpe' (최대샤프), 'risk_parity' (리스크 패리티)
    Returns: dict with 'weights', 'expected_return', 'volatility', 'sharpe'
    """
    n = returns.shape[1]
    tickers = returns.columns.tolist()
    mean_returns = returns.mean() * 252
    cov_matrix = returns.cov() * 252

    if method == 'equal':
        weights = np.array([1.0 / n] * n)
    elif method in ('min_var', 'max_sharpe', 'risk_parity'):
        try:
            from scipy.optimize import minimize

            def portfolio_volatility(w):
                return np.sqrt(w @ cov_matrix.values @ w)

            def neg_sharpe(w):
                ret = w @ mean_returns.values
                vol = portfolio_volatility(w)
                return -(ret - config_module.get_risk_free_rate()) / vol if vol > 0 else 0

            constraints = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1}]
            bounds = [(0, 1)] * n
            x0 = np.array([1.0 / n] * n)

            if method == 'min_var':
                result = minimize(portfolio_volatility, x0, method='SLSQP',
                                  bounds=bounds, constraints=constraints)
            elif method == 'max_sharpe':
                result = minimize(neg_sharpe, x0, method='SLSQP',
                                  bounds=bounds, constraints=constraints)
            elif method == 'risk_parity':
                # 리스크 패리티: 각 자산의 위험 기여도를 동일하게
                def risk_parity_obj(w):
                    port_vol = np.sqrt(w @ cov_matrix.values @ w)
                    if port_vol == 0:
                        return 0
                    marginal_risk = cov_matrix.values @ w
                    risk_contrib = w * marginal_risk / port_vol
                    target = port_vol / n
                    return np.sum((risk_contrib - target) ** 2)

                result = minimize(risk_parity_obj, x0, method='SLSQP',
                                  bounds=bounds, constraints=constraints)

            weights = result.x if result.success else x0
        except ImportError:
            logging.warning("[PORTFOLIO] scipy not installed, using equal weights")
            weights = np.array([1.0 / n] * n)
    else:
        weights = np.array([1.0 / n] * n)

    # 포트폴리오 지표 계산
    port_return = weights @ mean_returns.values
    port_vol = np.sqrt(weights @ cov_matrix.values @ weights)
    port_sharpe = (port_return - config_module.get_risk_free_rate()) / port_vol if port_vol > 0 else 0

    return {
        'tickers': tickers,
        'weights': weights,
        'expected_return': port_return,
        'volatility': port_vol,
        'sharpe': port_sharpe,
    }


def open_optimization_popup(watchlist, holdings=None):
    """포트폴리오 최적화 팝업."""
    if len(watchlist) < 2:
        messagebox.showinfo("알림", "포트폴리오 최적화는 2개 이상의 종목이 필요합니다.")
        return

    popup = tk.Toplevel()
    popup.title("포트폴리오 최적화")
    popup.state('zoomed')

    open_figs = []

    def _run_analysis():
        for widget in content_frame.winfo_children():
            widget.destroy()
        for f in open_figs:
            plt.close(f)
        open_figs.clear()

        filtered = _filter_watchlist(watchlist, holdings, filter_var.get())
        if len(filtered) < 2:
            tk.Label(content_frame, text="필터 결과 2개 이상의 종목이 필요합니다.", font=("Arial", 11)).pack(expand=True)
            return

        # 스크롤
        scroll_canvas = tk.Canvas(content_frame, highlightthickness=0)
        vsb = ttk.Scrollbar(content_frame, orient="vertical", command=scroll_canvas.yview)
        inner = tk.Frame(scroll_canvas)
        inner.bind("<Configure>", lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.create_window((0, 0), window=inner, anchor="nw")
        scroll_canvas.configure(yscrollcommand=vsb.set)
        scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        loading = tk.Label(inner, text="준비 중...", font=("Arial", 12))
        loading.pack(pady=10)
        _pvar = tk.DoubleVar(value=0)
        _pbar = ttk.Progressbar(inner, variable=_pvar, maximum=100, length=300)
        _pbar.pack(pady=(0, 10))
        _data = {}

        def _update_progress(pct, text):
            try:
                popup.after(0, lambda p=pct, t=text: (_pvar.set(p), loading.config(text=t)))
            except tk.TclError:
                pass

        def _optimize():
            try:
                tickers = list(filtered)
                _update_progress(10, f"수익률 데이터 다운로드 중... ({len(tickers)}종목)")
                returns = _download_returns(tickers, period="1y")
                _data['returns'] = returns
                _update_progress(60, "최적화 계산 중... (동일비중/최소분산/최대샤프/리스크패리티)")
                _update_progress(90, "결과 표시 중...")
                popup.after(0, _show)
            except Exception as e:
                logging.error(f"[OPTIMIZE] Error: {e}")
                try:
                    popup.after(0, lambda: loading.config(text=f"오류 발생: {e}"))
                except tk.TclError:
                    pass

        def _show():
            try:
                if not popup.winfo_exists():
                    return
            except tk.TclError:
                return
            loading.destroy()
            _pbar.destroy()
            returns = _data.get('returns', pd.DataFrame())

            if returns.empty or returns.shape[1] < 2:
                tk.Label(inner, text="데이터를 가져올 수 없습니다.", font=("Arial", 11)).pack(expand=True)
                return

            methods = [
                ('equal', '동일비중'),
                ('min_var', '최소분산'),
                ('max_sharpe', '최대샤프'),
                ('risk_parity', '리스크 패리티'),
            ]

            results = {}
            for key, label in methods:
                try:
                    results[key] = _optimize_portfolio(returns, method=key)
                    results[key]['label'] = label
                except Exception as e:
                    logging.warning(f"[OPTIMIZE] {key} failed: {e}")

            if not results:
                tk.Label(inner, text="최적화 실패", font=("Arial", 11)).pack(expand=True)
                return

            # 결과 요약 테이블
            summary_frame = tk.LabelFrame(inner, text="최적화 결과 비교", font=("Arial", 11, "bold"))
            summary_frame.pack(fill=tk.X, padx=10, pady=5)

            header = tk.Frame(summary_frame)
            header.pack(fill=tk.X, padx=8, pady=2)
            for col_text, col_w in [("방법론", 12), ("기대수익률", 10), ("변동성", 10), ("샤프비율", 8)]:
                tk.Label(header, text=col_text, font=("Arial", 9, "bold"),
                         width=col_w, anchor="center").pack(side=tk.LEFT)

            for key, res in results.items():
                row = tk.Frame(summary_frame)
                row.pack(fill=tk.X, padx=8, pady=1)
                tk.Label(row, text=res['label'], font=("Arial", 9, "bold"),
                         width=12, anchor="w").pack(side=tk.LEFT)
                ret_color = "#2E7D32" if res['expected_return'] > 0 else "#E74C3C"
                tk.Label(row, text=f"{res['expected_return']:.1%}",
                         font=("Arial", 9), width=10, fg=ret_color).pack(side=tk.LEFT)
                tk.Label(row, text=f"{res['volatility']:.1%}",
                         font=("Arial", 9), width=10).pack(side=tk.LEFT)
                tk.Label(row, text=f"{res['sharpe']:.2f}",
                         font=("Arial", 9), width=8).pack(side=tk.LEFT)

            # 최대 샤프 포트폴리오 상세 비중
            best = results.get('max_sharpe', results.get('equal'))
            if best:
                detail_frame = tk.LabelFrame(inner, text=f"추천 배분 ({best.get('label', '최대샤프')})",
                                              font=("Arial", 11, "bold"))
                detail_frame.pack(fill=tk.X, padx=10, pady=5)

                for i, t in enumerate(best['tickers']):
                    w_pct = best['weights'][i] * 100
                    if w_pct < 0.1:
                        continue
                    row = tk.Frame(detail_frame)
                    row.pack(fill=tk.X, padx=8, pady=1)
                    tk.Label(row, text=t, font=("Arial", 9, "bold"),
                             width=10, anchor="w").pack(side=tk.LEFT)
                    tk.Label(row, text=f"{w_pct:.1f}%",
                             font=("Arial", 9), width=8).pack(side=tk.LEFT)
                    # 비중 바
                    bar_canvas = tk.Canvas(row, width=200, height=14, highlightthickness=0)
                    bar_canvas.pack(side=tk.LEFT, padx=5)
                    bar_width = int(w_pct * 2)
                    bar_canvas.create_rectangle(0, 0, bar_width, 14, fill="#4A90D9", outline="")

                # 파이 차트
                fig, ax = plt.subplots(figsize=(6, 4))
                # 0.5% 미만 비중은 '기타'로 합산
                labels = []
                sizes = []
                other = 0
                for i, t in enumerate(best['tickers']):
                    w_pct = best['weights'][i] * 100
                    if w_pct >= 0.5:
                        labels.append(t)
                        sizes.append(w_pct)
                    else:
                        other += w_pct
                if other > 0:
                    labels.append('기타')
                    sizes.append(other)

                colors = plt.cm.Set3(np.linspace(0, 1, len(labels)))
                ax.pie(sizes, labels=labels, autopct='%1.1f%%', colors=colors, startangle=90)
                ax.set_title(f"포트폴리오 배분 ({best.get('label', '')})", fontsize=12, fontweight="bold")
                plt.tight_layout()

                open_figs.append(fig)
                chart_canvas = FigureCanvasTkAgg(fig, master=inner)
                chart_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
                chart_canvas.draw()

        threading.Thread(target=_optimize, daemon=True).start()

    filter_var = _add_filter_radio(popup, holdings, _run_analysis)
    content_frame = tk.Frame(popup)
    content_frame.pack(fill=tk.BOTH, expand=True)

    def _on_close():
        for f in open_figs:
            plt.close(f)
        popup.destroy()
    popup.protocol("WM_DELETE_WINDOW", _on_close)

    _run_analysis()


def open_portfolio_evaluation_popup(watchlist, holdings):
    """보유 종목 기반 포트폴리오 평가 팝업."""
    import holdings_manager
    from datetime import datetime, timedelta

    if not holdings:
        messagebox.showinfo("알림", "보유 종목 정보가 없습니다.\n종목 우클릭 → '보유 정보 편집'에서 추가하세요.")
        return

    # holdings 중 실제 수량이 있는 것만 (거래 기반 계산)
    active_tickers = []
    for t in holdings:
        h = holdings_manager.get_holding(holdings, t)
        if h and h.get("quantity", 0) > 0:
            active_tickers.append(t)
    active_holdings = {t: holdings[t] for t in active_tickers}
    if not active_holdings:
        messagebox.showinfo("알림", "보유 수량이 있는 종목이 없습니다.")
        return

    popup = tk.Toplevel()
    popup.title("포트폴리오 평가")
    popup.state('zoomed')

    # 스크롤
    canvas = tk.Canvas(popup, highlightthickness=0)
    vsb = ttk.Scrollbar(popup, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    # 마우스 휠
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    # 기간 선택
    period_frame = tk.LabelFrame(inner, text="기간 선택", font=("Arial", 10, "bold"))
    period_frame.pack(fill=tk.X, padx=10, pady=5)

    period_inner = tk.Frame(period_frame)
    period_inner.pack(padx=8, pady=5)

    # 기본 시작일: 가장 이른 거래일 또는 1년 전
    earliest = None
    for info in active_holdings.values():
        transactions = info.get("transactions", [])
        for tx in transactions:
            pd_str = tx.get("date", "")
            if pd_str:
                try:
                    d = datetime.strptime(pd_str, "%Y-%m-%d")
                    if earliest is None or d < earliest:
                        earliest = d
                except ValueError:
                    pass
    if earliest is None:
        earliest = datetime.now() - timedelta(days=365)

    try:
        from tkcalendar import DateEntry as _DateEntry
        _has_calendar = True
    except ImportError:
        _has_calendar = False

    tk.Label(period_inner, text="시작일:", font=("Arial", 10)).pack(side=tk.LEFT, padx=3)
    if _has_calendar:
        start_entry = _DateEntry(period_inner, width=11, font=("Arial", 10), date_pattern="yyyy-mm-dd",
                                 year=earliest.year, month=earliest.month, day=earliest.day,
                                 locale="ko_KR")
    else:
        start_entry = tk.Entry(period_inner, width=12, font=("Arial", 10))
        start_entry.insert(0, earliest.strftime("%Y-%m-%d"))
    start_entry.pack(side=tk.LEFT, padx=3)

    _now = datetime.now()
    tk.Label(period_inner, text="종료일:", font=("Arial", 10)).pack(side=tk.LEFT, padx=3)
    if _has_calendar:
        end_entry = _DateEntry(period_inner, width=11, font=("Arial", 10), date_pattern="yyyy-mm-dd",
                               year=_now.year, month=_now.month, day=_now.day,
                               locale="ko_KR")
    else:
        end_entry = tk.Entry(period_inner, width=12, font=("Arial", 10))
        end_entry.insert(0, _now.strftime("%Y-%m-%d"))
    end_entry.pack(side=tk.LEFT, padx=3)

    # 결과 영역
    result_frame = tk.Frame(inner)
    result_frame.pack(fill=tk.BOTH, expand=True)

    open_figs = []
    _eval_progress = {"label": None, "bar": None}

    def _update_eval_progress(pct, text):
        def _upd():
            if _eval_progress["label"]:
                try:
                    _eval_progress["label"].config(text=text)
                except tk.TclError:
                    pass
            if _eval_progress["bar"]:
                try:
                    _eval_progress["bar"]["value"] = pct
                except tk.TclError:
                    pass
        try:
            popup.after(0, _upd)
        except tk.TclError:
            pass

    def _run_analysis():
      try:
        if _has_calendar:
            start_date = start_entry.get_date().strftime("%Y-%m-%d")
            end_date = end_entry.get_date().strftime("%Y-%m-%d")
        else:
            start_date = start_entry.get().strip()
            end_date = end_entry.get().strip()

        tickers = list(active_holdings.keys())
        current_prices = {}
        name_map = {}

        # 현재가 수집
        for i, t in enumerate(tickers):
            _update_eval_progress(10 + 30 * (i + 1) // len(tickers),
                                  f"현재가 수집 중... {t} ({i+1}/{len(tickers)})")
            try:
                info = yf.Ticker(t).info
                price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
                if price:
                    current_prices[t] = float(price)
                name_map[t] = info.get('shortName', t)
            except Exception:
                name_map[t] = t

        _update_eval_progress(45, "포트폴리오 요약 계산 중...")
        summary = holdings_manager.calculate_portfolio_summary(active_holdings, current_prices)
        _update_eval_progress(55, "포트폴리오 히스토리 계산 중...")
        portfolio_history = holdings_manager.compute_portfolio_value_history(active_holdings, start_date, end_date)

        _update_eval_progress(65, "수익률 데이터 다운로드 중...")
        # 리스크 지표용 수익률 데이터
        returns_data = _download_returns(tickers, period="1y")
        # 보유 비중 기반 포트폴리오 수익률
        port_weights = {}
        total_val = sum(current_prices.get(t, 0) * holdings_manager.get_holding(active_holdings, t)["quantity"]
                        for t in tickers if holdings_manager.get_holding(active_holdings, t))
        for t in tickers:
            h = holdings_manager.get_holding(active_holdings, t)
            if h and total_val > 0:
                port_weights[t] = (current_prices.get(t, 0) * h["quantity"]) / total_val

        port_returns = None
        risk_metrics = {}
        if not returns_data.empty:
            valid_cols = [c for c in tickers if c in returns_data.columns]
            if valid_cols:
                w_arr = np.array([port_weights.get(t, 0) for t in valid_cols])
                if w_arr.sum() > 0:
                    w_arr = w_arr / w_arr.sum()
                    port_returns = (returns_data[valid_cols] * w_arr).sum(axis=1)

                    # VaR (95%, Historical)
                    var_95 = np.percentile(port_returns.dropna(), 5)
                    # CVaR (Expected Shortfall)
                    cvar_95 = port_returns[port_returns <= var_95].mean()
                    # 연환산 변동성
                    ann_vol = port_returns.std() * np.sqrt(252)
                    # 연환산 수익률
                    ann_ret = port_returns.mean() * 252
                    # 샤프비율
                    sharpe = (ann_ret - config_module.get_risk_free_rate()) / ann_vol if ann_vol > 0 else 0
                    # 소르티노비율
                    downside = port_returns[port_returns < 0]
                    downside_std = downside.std() * np.sqrt(252) if len(downside) > 1 else 0
                    sortino = (ann_ret - config_module.get_risk_free_rate()) / downside_std if downside_std > 0 else 0
                    # MDD
                    cum = (1 + port_returns).cumprod()
                    peak = cum.cummax()
                    dd = (cum - peak) / peak
                    mdd = dd.min()
                    # Calmar ratio (연수익/MDD)
                    calmar = abs(ann_ret / mdd) if mdd < 0 else 0
                    # HHI (집중도)
                    weights_arr = np.array([pos["weight"] / 100 for pos in summary["positions"]])
                    hhi = (weights_arr ** 2).sum() if len(weights_arr) > 0 else 0
                    effective_n = 1 / hhi if hhi > 0 else 0
                    # 일일 VaR 금액
                    var_dollar = abs(var_95) * total_val

                    risk_metrics = {
                        "var_95": var_95, "cvar_95": cvar_95,
                        "ann_vol": ann_vol, "ann_ret": ann_ret,
                        "sharpe": sharpe, "sortino": sortino,
                        "mdd": mdd, "calmar": calmar,
                        "hhi": hhi, "effective_n": effective_n,
                        "var_dollar": var_dollar,
                    }

        def _show():
            try:
                if not popup.winfo_exists():
                    return
            except tk.TclError:
                return
            # 기존 결과 지우기
            for widget in result_frame.winfo_children():
                widget.destroy()

            positions = summary["positions"]
            if not positions:
                tk.Label(result_frame, text="현재가 데이터를 가져올 수 없습니다.", font=("Arial", 11)).pack(pady=20)
                return

            # --- 포트폴리오 요약 ---
            summary_lf = tk.LabelFrame(result_frame, text="포트폴리오 요약", font=("Arial", 11, "bold"))
            summary_lf.pack(fill=tk.X, padx=10, pady=5)

            sf = tk.Frame(summary_lf)
            sf.pack(padx=10, pady=8)

            pnl_color = "#2E7D32" if summary["total_pnl"] >= 0 else "#E74C3C"
            pnl_sign = "+" if summary["total_pnl"] >= 0 else ""

            realized_pnl = summary.get("total_realized_pnl", 0)
            realized_sign = "+" if realized_pnl >= 0 else ""
            realized_color = "#2E7D32" if realized_pnl >= 0 else "#E74C3C"

            for label_text, value_text, color in [
                ("총 투자금:", f"${summary['total_cost']:,.0f}", "#000"),
                ("총 평가액:", f"${summary['total_value']:,.0f}", "#000"),
                ("미실현 손익:", f"{pnl_sign}${summary['total_pnl']:,.0f} ({pnl_sign}{summary['total_pnl_pct']:.1f}%)", pnl_color),
                ("실현 손익:", f"{realized_sign}${realized_pnl:,.0f}", realized_color),
                ("보유 종목:", f"{len(positions)}개", "#000"),
            ]:
                row = tk.Frame(sf)
                row.pack(fill=tk.X, pady=1)
                tk.Label(row, text=label_text, font=("Arial", 10, "bold"), width=12, anchor="e").pack(side=tk.LEFT)
                tk.Label(row, text=value_text, font=("Arial", 10), fg=color, anchor="w").pack(side=tk.LEFT, padx=5)

            # --- 종목별 상세 ---
            detail_lf = tk.LabelFrame(result_frame, text="종목별 상세", font=("Arial", 11, "bold"))
            detail_lf.pack(fill=tk.X, padx=10, pady=5)

            detail_cols = ("종목", "수량", "매수가", "현재가", "평가액", "미실현%", "실현손익", "비중%")
            detail_tree = ttk.Treeview(detail_lf, columns=detail_cols, show="headings", height=min(len(positions) + 1, 12))
            col_widths = [100, 70, 90, 90, 100, 80, 90, 70]
            for i, col in enumerate(detail_cols):
                detail_tree.heading(col, text=col)
                detail_tree.column(col, width=col_widths[i], anchor="e" if i > 0 else "w")

            for pos in positions:
                pnl_sign = "+" if pos["pnl_pct"] >= 0 else ""
                rpnl = pos.get("realized_pnl", 0)
                rpnl_sign = "+" if rpnl >= 0 else ""
                detail_tree.insert("", "end", values=(
                    name_map.get(pos["ticker"], pos["ticker"]),
                    f"{pos['quantity']:g}",
                    f"${pos['avg_price']:,.2f}",
                    f"${pos['current_price']:,.2f}",
                    f"${pos['value']:,.0f}",
                    f"{pnl_sign}{pos['pnl_pct']:.1f}%",
                    f"{rpnl_sign}${rpnl:,.0f}",
                    f"{pos['weight']:.1f}%",
                ))

            detail_tree.pack(fill=tk.X, padx=8, pady=5)

            # --- 리스크 지표 ---
            if risk_metrics:
                risk_lf = tk.LabelFrame(result_frame, text="리스크 지표", font=("Arial", 11, "bold"))
                risk_lf.pack(fill=tk.X, padx=10, pady=5)

                risk_rows = [
                    ("VaR (95%, 일일)", f"{risk_metrics['var_95']:.2%}  (${risk_metrics['var_dollar']:,.0f})",
                     "95% 확률로 하루 최대 손실 한도"),
                    ("CVaR (Expected Shortfall)", f"{risk_metrics['cvar_95']:.2%}",
                     "최악의 5% 시나리오에서 평균 손실"),
                    ("연환산 변동성", f"{risk_metrics['ann_vol']:.1%}", "수익률의 연간 표준편차"),
                    ("연환산 수익률", f"{risk_metrics['ann_ret']:.1%}", ""),
                    ("샤프 비율", f"{risk_metrics['sharpe']:.2f}",
                     f">1 양호, >2 우수 (무위험 {config_module.get_risk_free_rate():.1%})"),
                    ("소르티노 비율", f"{risk_metrics['sortino']:.2f}",
                     "하방 위험만 고려한 위험조정수익률"),
                    ("최대 낙폭 (MDD)", f"{risk_metrics['mdd']:.1%}", "고점 대비 최대 하락폭"),
                    ("Calmar 비율", f"{risk_metrics['calmar']:.2f}",
                     "수익률/MDD, >1 양호"),
                    ("집중도 (HHI)", f"{risk_metrics['hhi']:.3f}  (유효 종목수: {risk_metrics['effective_n']:.1f})",
                     "0=분산, 1=집중. 유효 종목수가 실제 분산 효과"),
                ]
                for label_text, value_text, tooltip_text in risk_rows:
                    row = tk.Frame(risk_lf)
                    row.pack(fill=tk.X, padx=8, pady=1)
                    tk.Label(row, text=label_text, font=("Arial", 9), anchor="w", width=22).pack(side=tk.LEFT)
                    if tooltip_text:
                        q = tk.Label(row, text="?", font=("Arial", 8, "bold"), fg="#4A90D9", cursor="question_arrow")
                        q.pack(side=tk.LEFT, padx=(0, 3))
                        from ui_components import HelpTooltip
                        HelpTooltip(q, tooltip_text)
                    fg = "#000"
                    if "VaR" in label_text or "MDD" in label_text or "CVaR" in label_text:
                        fg = "#E74C3C"
                    elif "샤프" in label_text or "소르티노" in label_text or "Calmar" in label_text:
                        try:
                            val = float(value_text.split()[0])
                            fg = "#2E7D32" if val > 1 else "#E74C3C" if val < 0 else "#000"
                        except ValueError:
                            pass
                    tk.Label(row, text=value_text, font=("Arial", 9, "bold"), anchor="e", fg=fg).pack(side=tk.RIGHT)

                # 리스크 등급 판정
                risk_grade_text = ""
                if risk_metrics["ann_vol"] < 0.15:
                    risk_grade_text = "낮은 위험"
                    risk_grade_color = "#2E7D32"
                elif risk_metrics["ann_vol"] < 0.25:
                    risk_grade_text = "중간 위험"
                    risk_grade_color = "#F39C12"
                else:
                    risk_grade_text = "높은 위험"
                    risk_grade_color = "#E74C3C"

                if risk_metrics["hhi"] > 0.5:
                    risk_grade_text += " | 집중도 높음 (분산 필요)"
                    risk_grade_color = "#E74C3C"

                tk.Label(risk_lf, text=f"→ {risk_grade_text}",
                         font=("Arial", 10, "bold"), fg=risk_grade_color).pack(padx=8, pady=(2, 5), anchor="w")

            # --- 스트레스 테스트 ---
            if risk_metrics and port_returns is not None:
                stress_lf = tk.LabelFrame(result_frame, text="스트레스 테스트 (과거 위기 시나리오)",
                                           font=("Arial", 11, "bold"))
                stress_lf.pack(fill=tk.X, padx=10, pady=5)

                # 종목별 베타를 이용한 시나리오 손실 추정
                scenarios = [
                    ("2008 금융위기", -0.569, "S&P 500 -56.9% (2007.10~2009.03)"),
                    ("2020 코로나", -0.339, "S&P 500 -33.9% (2020.02~2020.03)"),
                    ("2022 긴축", -0.254, "S&P 500 -25.4% (2022.01~2022.10)"),
                    ("10% 조정", -0.10, "일반적 시장 조정"),
                    ("20% 약세장", -0.20, "공식 약세장 진입"),
                ]
                # 포트폴리오 베타 추정 (개별 종목 베타의 가중 평균)
                port_beta = 0
                for pos in positions:
                    t = pos["ticker"]
                    try:
                        beta = yf.Ticker(t).info.get("beta", 1.0) or 1.0
                    except Exception:
                        beta = 1.0
                    port_beta += beta * (pos["weight"] / 100)

                tk.Label(stress_lf, text=f"포트폴리오 베타: {port_beta:.2f}",
                         font=("Arial", 9), fg="#555").pack(padx=8, anchor="w")

                for name, market_drop, desc in scenarios:
                    estimated_loss = market_drop * port_beta
                    loss_dollar = estimated_loss * total_val
                    row = tk.Frame(stress_lf)
                    row.pack(fill=tk.X, padx=8, pady=1)
                    tk.Label(row, text=name, font=("Arial", 9), width=14, anchor="w").pack(side=tk.LEFT)
                    tk.Label(row, text=f"{estimated_loss:.1%}  (${loss_dollar:,.0f})",
                             font=("Arial", 9, "bold"), fg="#E74C3C", width=18).pack(side=tk.LEFT)
                    tk.Label(row, text=desc, font=("Arial", 8), fg="#777", anchor="w").pack(side=tk.LEFT, padx=5)

            # --- 포지션별 리스크 기여도 ---
            if not returns_data.empty and len(positions) >= 2:
                try:
                    valid_risk_cols = [c for c in [p["ticker"] for p in positions] if c in returns_data.columns]
                    if len(valid_risk_cols) >= 2:
                        risk_contrib_lf = tk.LabelFrame(result_frame, text="포지션별 리스크 기여도",
                                                         font=("Arial", 11, "bold"))
                        risk_contrib_lf.pack(fill=tk.X, padx=10, pady=5)

                        cov_mat = returns_data[valid_risk_cols].cov() * 252
                        w_arr_risk = np.array([port_weights.get(t, 0) for t in valid_risk_cols])
                        if w_arr_risk.sum() > 0:
                            w_arr_risk = w_arr_risk / w_arr_risk.sum()
                        port_vol_risk = np.sqrt(w_arr_risk @ cov_mat.values @ w_arr_risk)

                        if port_vol_risk > 0:
                            marginal = cov_mat.values @ w_arr_risk
                            risk_contribs = w_arr_risk * marginal / port_vol_risk
                            risk_pcts = risk_contribs / risk_contribs.sum() * 100

                            rc_header = tk.Frame(risk_contrib_lf)
                            rc_header.pack(fill=tk.X, padx=8, pady=2)
                            for col_text, col_w in [("종목", 10), ("비중", 8), ("리스크 기여", 10), ("리스크%", 9), ("비중 대비", 10)]:
                                tk.Label(rc_header, text=col_text, font=("Arial", 9, "bold"),
                                         width=col_w, anchor="center").pack(side=tk.LEFT)

                            for i, t in enumerate(valid_risk_cols):
                                weight_pct = w_arr_risk[i] * 100
                                risk_pct = risk_pcts[i]
                                ratio = risk_pct / weight_pct if weight_pct > 0 else 0

                                row = tk.Frame(risk_contrib_lf)
                                row.pack(fill=tk.X, padx=8, pady=1)
                                tk.Label(row, text=name_map.get(t, t)[:10], font=("Arial", 9),
                                         width=10, anchor="w").pack(side=tk.LEFT)
                                tk.Label(row, text=f"{weight_pct:.1f}%", font=("Arial", 9),
                                         width=8).pack(side=tk.LEFT)
                                tk.Label(row, text=f"{risk_contribs[i]:.4f}", font=("Arial", 9),
                                         width=10).pack(side=tk.LEFT)
                                risk_color = "#E74C3C" if risk_pct > weight_pct * 1.5 else "#000"
                                tk.Label(row, text=f"{risk_pct:.1f}%", font=("Arial", 9, "bold"),
                                         width=9, fg=risk_color).pack(side=tk.LEFT)
                                ratio_color = "#E74C3C" if ratio > 1.5 else "#2E7D32" if ratio < 0.7 else "#000"
                                tk.Label(row, text=f"×{ratio:.2f}", font=("Arial", 9),
                                         width=10, fg=ratio_color).pack(side=tk.LEFT)

                            tk.Label(risk_contrib_lf,
                                     text="×1.0=비중 비례 | ×1.5↑=리스크 과다 기여 | ×0.7↓=분산 기여",
                                     font=("Arial", 8), fg="#666").pack(padx=8, pady=(2, 4), anchor="w")
                except Exception as e:
                    logging.warning(f"[PORTFOLIO] Risk contribution error: {e}")

            # --- 리밸런싱 분석 ---
            if len(positions) >= 2 and risk_metrics:
                rebal_lf = tk.LabelFrame(result_frame, text="리밸런싱 분석", font=("Arial", 11, "bold"))
                rebal_lf.pack(fill=tk.X, padx=10, pady=5)

                # 동일비중 대비 드리프트
                equal_weight = 100.0 / len(positions)
                tk.Label(rebal_lf, text=f"목표 비중 (동일비중): {equal_weight:.1f}%",
                         font=("Arial", 9), fg="#555").pack(padx=8, anchor="w", pady=(3, 0))

                max_drift = 0
                rebal_cols = ("종목", "현재 비중", "목표 비중", "드리프트", "조정")
                rebal_tree = ttk.Treeview(rebal_lf, columns=rebal_cols, show="headings",
                                           height=min(len(positions), 8))
                for col in rebal_cols:
                    rebal_tree.heading(col, text=col)
                    w = 90 if col != "종목" else 100
                    rebal_tree.column(col, width=w, anchor="e" if col != "종목" else "w")

                for pos in positions:
                    drift = pos["weight"] - equal_weight
                    max_drift = max(max_drift, abs(drift))
                    # 조정 필요 금액
                    adjust_dollar = -drift / 100 * total_val
                    adjust_text = f"{'매도' if adjust_dollar < 0 else '매수'} ${abs(adjust_dollar):,.0f}"
                    rebal_tree.insert("", "end", values=(
                        name_map.get(pos["ticker"], pos["ticker"]),
                        f"{pos['weight']:.1f}%",
                        f"{equal_weight:.1f}%",
                        f"{drift:+.1f}%",
                        adjust_text,
                    ))

                rebal_tree.pack(fill=tk.X, padx=8, pady=3)

                # 리밸런싱 필요성 판단
                if max_drift > 10:
                    rebal_msg = f"최대 드리프트 {max_drift:.1f}% — 리밸런싱 권장"
                    rebal_color = "#E74C3C"
                elif max_drift > 5:
                    rebal_msg = f"최대 드리프트 {max_drift:.1f}% — 모니터링 필요"
                    rebal_color = "#F39C12"
                else:
                    rebal_msg = f"최대 드리프트 {max_drift:.1f}% — 양호"
                    rebal_color = "#2E7D32"

                # 턴오버 비용 추정
                total_turnover = sum(abs(pos["weight"] - equal_weight) / 100 * total_val for pos in positions) / 2
                cost_estimate = total_turnover * 0.001  # 0.1% 수수료 가정

                tk.Label(rebal_lf, text=f"→ {rebal_msg}  |  예상 거래비용: ${cost_estimate:,.0f}",
                         font=("Arial", 9, "bold"), fg=rebal_color).pack(padx=8, pady=(0, 5), anchor="w")

            # --- 차트 ---
            chart_frame = tk.Frame(result_frame)
            chart_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

            # 1. 포트폴리오 가치 추이
            if not portfolio_history.empty and len(portfolio_history) > 1:
                fig1, ax1 = plt.subplots(figsize=(8, 3.5))
                ax1.plot(portfolio_history.index, portfolio_history.values, color="#4A90D9", linewidth=1.5, label="포트폴리오 가치")
                total_cost = summary["total_cost"]
                ax1.axhline(y=total_cost, color="#E74C3C", linestyle="--", linewidth=1, label=f"투자금 (${total_cost:,.0f})")
                ax1.set_title("포트폴리오 가치 추이", fontsize=12, fontweight="bold")
                ax1.set_ylabel("가치 ($)")
                ax1.legend(fontsize=8)
                ax1.grid(alpha=0.3)
                plt.tight_layout()

                open_figs.append(fig1)
                canvas1 = FigureCanvasTkAgg(fig1, master=chart_frame)
                canvas1.get_tk_widget().pack(fill=tk.BOTH, expand=True, pady=3)
                canvas1.draw()

            # 2. 종목 비중 파이차트 + 3. 종목별 손익 바차트
            fig2, (ax2, ax3) = plt.subplots(1, 2, figsize=(8, 3.5))

            # 파이차트
            labels = [name_map.get(p["ticker"], p["ticker"]) for p in positions]
            sizes = [p["weight"] for p in positions]
            colors_pie = plt.cm.Set3(np.linspace(0, 1, len(labels)))
            ax2.pie(sizes, labels=labels, autopct='%1.1f%%', colors=colors_pie, startangle=90, textprops={'fontsize': 8})
            ax2.set_title("종목 비중", fontsize=11, fontweight="bold")

            # 바차트
            bar_labels = [name_map.get(p["ticker"], p["ticker"]) for p in positions]
            bar_values = [p["pnl_pct"] for p in positions]
            bar_colors = ["#2E7D32" if v >= 0 else "#E74C3C" for v in bar_values]
            ax3.barh(bar_labels, bar_values, color=bar_colors)
            ax3.set_title("종목별 손익 (%)", fontsize=11, fontweight="bold")
            ax3.set_xlabel("손익률 (%)")
            ax3.axvline(x=0, color="black", linewidth=0.5)
            ax3.grid(axis='x', alpha=0.3)

            plt.tight_layout()
            open_figs.append(fig2)
            canvas2 = FigureCanvasTkAgg(fig2, master=chart_frame)
            canvas2.get_tk_widget().pack(fill=tk.BOTH, expand=True, pady=3)
            canvas2.draw()

            # 3. 드로우다운 차트 + 롤링 변동성
            if port_returns is not None and len(port_returns) > 20:
                fig3, (ax4, ax5) = plt.subplots(2, 1, figsize=(8, 5), sharex=True)

                # 수중 드로우다운 (Underwater plot)
                cum = (1 + port_returns).cumprod()
                peak = cum.cummax()
                dd = (cum - peak) / peak
                ax4.fill_between(dd.index, dd.values, 0, color="#E74C3C", alpha=0.4)
                ax4.plot(dd.index, dd.values, color="#E74C3C", linewidth=0.8)
                ax4.set_title("드로우다운 (Underwater)", fontsize=10, fontweight="bold")
                ax4.set_ylabel("낙폭 (%)")
                ax4.grid(alpha=0.3)

                # 30일 롤링 변동성 (연환산)
                rolling_vol = port_returns.rolling(30).std() * np.sqrt(252)
                ax5.plot(rolling_vol.index, rolling_vol.values, color="#4A90D9", linewidth=1)
                ax5.axhline(y=rolling_vol.mean(), color="#F39C12", linestyle="--", linewidth=0.8,
                            label=f"평균 {rolling_vol.mean():.1%}")
                ax5.set_title("30일 롤링 변동성 (연환산)", fontsize=10, fontweight="bold")
                ax5.set_ylabel("변동성")
                ax5.legend(fontsize=8)
                ax5.grid(alpha=0.3)

                plt.tight_layout()
                open_figs.append(fig3)
                canvas3 = FigureCanvasTkAgg(fig3, master=chart_frame)
                canvas3.get_tk_widget().pack(fill=tk.BOTH, expand=True, pady=3)
                canvas3.draw()

        _update_eval_progress(90, "결과 표시 중...")
        try:
            popup.after(0, _show)
        except tk.TclError:
            pass
      except Exception as e:
        logging.error(f"[EVAL] Analysis error: {e}")
        try:
            popup.after(0, lambda: _eval_progress["label"].config(text=f"오류 발생: {e}") if _eval_progress["label"] else None)
        except tk.TclError:
            pass

    def _start_analysis():
        # 기존 결과 지우기 & 로딩 표시
        for widget in result_frame.winfo_children():
            widget.destroy()
        lbl = tk.Label(result_frame, text="준비 중...", font=("Arial", 12))
        lbl.pack(pady=10)
        bar = ttk.Progressbar(result_frame, maximum=100, length=300)
        bar.pack(pady=(0, 10))
        _eval_progress["label"] = lbl
        _eval_progress["bar"] = bar
        threading.Thread(target=_run_analysis, daemon=True).start()

    analyze_btn = tk.Button(period_inner, text="분석", command=_start_analysis, font=("Arial", 10, "bold"))
    analyze_btn.pack(side=tk.LEFT, padx=8)

    def _on_close():
        for f in open_figs:
            plt.close(f)
        canvas.unbind_all("<MouseWheel>")
        popup.destroy()

    popup.protocol("WM_DELETE_WINDOW", _on_close)

    # 초기 분석 실행
    _start_analysis()


# ── Fama-French Factor Decomposition ──────────────────────────────────────────

def _download_fama_french_factors(period="1y", n_factors=3):
    """Fama-French 팩터 수익률 다운로드.

    pandas_datareader가 있으면 Kenneth French 라이브러리에서 다운로드,
    없으면 ETF 프록시로 팩터를 구성:
    - Mkt-RF: SPY 수익률 - 무위험이자율
    - SMB (Small Minus Big): IWM - SPY
    - HML (High Minus Low): IWD - IWF (가치 vs 성장)
    5팩터 추가:
    - RMW (Robust Minus Weak): QUAL - SPY (퀄리티 프록시)
    - CMA (Conservative Minus Aggressive): SPLV - SPHB (저변동 vs 고변동)

    Returns: DataFrame with daily factor returns, RF column (daily risk-free rate)
    """
    # pandas_datareader 시도
    try:
        import pandas_datareader.data as web
        if n_factors == 5:
            ds_name = 'F-F_Research_Data_5_Factors_2x3_daily'
        else:
            ds_name = 'F-F_Research_Data_Factors_daily'
        ff_data = web.DataReader(ds_name, 'famafrench', period=period)[0]
        # 퍼센트 → 소수
        ff_data = ff_data / 100.0
        logging.info(f"[FF] pandas_datareader로 {n_factors}팩터 다운로드 성공")
        return ff_data
    except Exception as e:
        logging.info(f"[FF] pandas_datareader 사용 불가, ETF 프록시 사용: {e}")

    # ETF 프록시 팩터 구성
    if n_factors == 5:
        etf_tickers = ["SPY", "IWM", "IWD", "IWF", "QUAL", "SPLV", "SPHB"]
    else:
        etf_tickers = ["SPY", "IWM", "IWD", "IWF"]

    try:
        data = yf.download(etf_tickers, period=period, interval="1d", group_by='ticker')
        if data.empty:
            return pd.DataFrame()

        closes = pd.DataFrame()
        if len(etf_tickers) == 1:
            closes[etf_tickers[0]] = data['Close']
        else:
            for t in etf_tickers:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        closes[t] = data[(t, 'Close')]
                    else:
                        closes[t] = data['Close']
                except (KeyError, TypeError):
                    continue

        returns = closes.pct_change().dropna()
        if returns.empty:
            return pd.DataFrame()

        # 일일 무위험이자율 (연율 → 일일)
        rf_annual = config_module.get_risk_free_rate()
        rf_daily = (1 + rf_annual) ** (1 / 252) - 1

        factors = pd.DataFrame(index=returns.index)
        factors['Mkt-RF'] = returns.get('SPY', 0) - rf_daily
        factors['SMB'] = returns.get('IWM', 0) - returns.get('SPY', 0)
        factors['HML'] = returns.get('IWD', 0) - returns.get('IWF', 0)
        factors['RF'] = rf_daily

        if n_factors == 5:
            factors['RMW'] = returns.get('QUAL', 0) - returns.get('SPY', 0)
            if 'SPLV' in returns.columns and 'SPHB' in returns.columns:
                factors['CMA'] = returns.get('SPLV', 0) - returns.get('SPHB', 0)
            else:
                # SPHB 없으면 SPLV - SPY로 대체
                factors['CMA'] = returns.get('SPLV', 0) - returns.get('SPY', 0)

        factors = factors.dropna()
        logging.info(f"[FF] ETF 프록시 {n_factors}팩터 구성 완료 ({len(factors)}일)")
        return factors

    except Exception as e:
        logging.error(f"[FF] Factor download error: {e}")
        return pd.DataFrame()


def _run_factor_regression(returns_series, factor_data):
    """OLS 회귀: 종목/포트폴리오 수익률 ~ 팩터.

    numpy lstsq 사용 (statsmodels 의존 없음).

    Returns dict:
    - 'alpha': 연환산 알파
    - 'alpha_t': 알파 t-통계량
    - 'betas': {팩터명: 베타계수}
    - 'betas_t': {팩터명: t-통계량}
    - 'r_squared': R²
    - 'adj_r_squared': 조정 R²
    - 'residual_std': 잔차 표준편차 (연환산)
    """
    # 팩터 컬럼 (RF 제외)
    factor_cols = [c for c in factor_data.columns if c != 'RF']

    # 공통 인덱스 정렬
    common_idx = returns_series.dropna().index.intersection(factor_data.dropna().index)
    if len(common_idx) < 30:
        return None

    y = returns_series.loc[common_idx].values
    # 초과수익률 (RF가 있으면 차감)
    if 'RF' in factor_data.columns:
        y = y - factor_data.loc[common_idx, 'RF'].values

    X_factors = factor_data.loc[common_idx, factor_cols].values
    # 상수항 추가 (알파)
    n_obs = len(y)
    X = np.column_stack([np.ones(n_obs), X_factors])

    # OLS: y = X @ beta + epsilon
    try:
        result = np.linalg.lstsq(X, y, rcond=None)
        coeffs = result[0]  # [alpha, beta1, beta2, ...]
    except np.linalg.LinAlgError:
        return None

    # 잔차, R²
    y_hat = X @ coeffs
    residuals = y - y_hat
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    k = len(factor_cols)  # 팩터 수 (상수항 제외)
    adj_r_squared = 1.0 - (1.0 - r_squared) * (n_obs - 1) / (n_obs - k - 1) if n_obs > k + 1 else r_squared

    # 잔차 표준편차
    residual_var = ss_res / (n_obs - k - 1) if n_obs > k + 1 else ss_res / max(n_obs, 1)
    residual_std = np.sqrt(residual_var)

    # t-통계량: se(beta_j) = residual_std * sqrt((X'X)^{-1}_{jj})
    try:
        XtX_inv = np.linalg.inv(X.T @ X)
        se = residual_std * np.sqrt(np.diag(XtX_inv))
    except np.linalg.LinAlgError:
        se = np.ones(k + 1) * np.nan

    alpha_daily = coeffs[0]
    alpha_annual = alpha_daily * 252
    alpha_t = coeffs[0] / se[0] if se[0] > 0 else 0.0

    betas = {}
    betas_t = {}
    for i, col in enumerate(factor_cols):
        betas[col] = coeffs[i + 1]
        betas_t[col] = coeffs[i + 1] / se[i + 1] if se[i + 1] > 0 else 0.0

    return {
        'alpha': alpha_annual,
        'alpha_t': alpha_t,
        'betas': betas,
        'betas_t': betas_t,
        'r_squared': r_squared,
        'adj_r_squared': adj_r_squared,
        'residual_std': residual_std * np.sqrt(252),  # 연환산
    }


def open_fama_french_popup(watchlist, holdings=None):
    """Fama-French 팩터 분해 팝업."""
    if not watchlist:
        messagebox.showinfo("알림", "워치리스트에 종목을 추가하세요.")
        return

    popup = tk.Toplevel()
    popup.title("Fama-French 팩터 분해")
    popup.state('zoomed')

    open_figs = []

    # 스크롤 영역
    scroll_canvas = tk.Canvas(popup, highlightthickness=0)
    vsb = ttk.Scrollbar(popup, orient="vertical", command=scroll_canvas.yview)
    inner = tk.Frame(scroll_canvas)
    inner.bind("<Configure>", lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
    scroll_canvas.create_window((0, 0), window=inner, anchor="nw")
    scroll_canvas.configure(yscrollcommand=vsb.set)
    scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _on_mousewheel(event):
        scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)

    # 설정 영역
    config_frame = tk.LabelFrame(inner, text="팩터 모델 설정", font=("Arial", 10, "bold"))
    config_frame.pack(fill=tk.X, padx=10, pady=5)

    config_inner = tk.Frame(config_frame)
    config_inner.pack(padx=8, pady=5)

    tk.Label(config_inner, text="모델:", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
    factor_var = tk.IntVar(value=3)
    tk.Radiobutton(config_inner, text="3팩터 (Mkt, SMB, HML)", variable=factor_var,
                   value=3, font=("Arial", 10)).pack(side=tk.LEFT, padx=5)
    tk.Radiobutton(config_inner, text="5팩터 (+RMW, CMA)", variable=factor_var,
                   value=5, font=("Arial", 10)).pack(side=tk.LEFT, padx=5)

    tk.Label(config_inner, text="기간:", font=("Arial", 10)).pack(side=tk.LEFT, padx=(15, 5))
    period_var = tk.StringVar(value="1y")
    period_combo = ttk.Combobox(config_inner, textvariable=period_var, values=["6mo", "1y", "2y", "5y"],
                                width=6, state="readonly", font=("Arial", 10))
    period_combo.pack(side=tk.LEFT, padx=3)

    # 필터
    filter_var = _add_filter_radio(inner, holdings, lambda: None)

    # 결과 영역
    result_frame = tk.Frame(inner)
    result_frame.pack(fill=tk.BOTH, expand=True)

    _progress = {"label": None, "bar": None}

    def _update_progress(pct, text):
        def _upd():
            if _progress["label"]:
                try:
                    _progress["label"].config(text=text)
                except tk.TclError:
                    pass
            if _progress["bar"]:
                try:
                    _progress["bar"]["value"] = pct
                except tk.TclError:
                    pass
        try:
            popup.after(0, _upd)
        except tk.TclError:
            pass

    def _run_analysis():
      try:
        n_factors = factor_var.get()
        period = period_var.get()
        filtered = _filter_watchlist(watchlist, holdings, filter_var.get())

        if not filtered:
            def _no_data():
                for widget in result_frame.winfo_children():
                    widget.destroy()
                tk.Label(result_frame, text="분석할 종목이 없습니다.", font=("Arial", 11)).pack(pady=20)
            popup.after(0, _no_data)
            return

        _update_progress(5, f"Fama-French {n_factors}팩터 데이터 다운로드 중...")
        factor_data = _download_fama_french_factors(period=period, n_factors=n_factors)
        if factor_data.empty:
            def _err():
                for widget in result_frame.winfo_children():
                    widget.destroy()
                tk.Label(result_frame, text="팩터 데이터를 다운로드할 수 없습니다.",
                         font=("Arial", 11), fg="#E74C3C").pack(pady=20)
            popup.after(0, _err)
            return

        _update_progress(20, f"종목 수익률 다운로드 중... ({len(filtered)}종목)")
        returns = _download_returns(filtered, period=period)
        if returns.empty:
            def _err():
                for widget in result_frame.winfo_children():
                    widget.destroy()
                tk.Label(result_frame, text="종목 수익률 데이터를 가져올 수 없습니다.",
                         font=("Arial", 11), fg="#E74C3C").pack(pady=20)
            popup.after(0, _err)
            return

        # 종목별 회귀 분석
        results = {}
        factor_cols = [c for c in factor_data.columns if c != 'RF']
        valid_tickers = [t for t in filtered if t in returns.columns]

        for i, t in enumerate(valid_tickers):
            _update_progress(20 + 50 * (i + 1) // len(valid_tickers),
                             f"팩터 회귀 분석 중... {t} ({i+1}/{len(valid_tickers)})")
            reg = _run_factor_regression(returns[t], factor_data)
            if reg is not None:
                results[t] = reg

        if not results:
            def _err():
                for widget in result_frame.winfo_children():
                    widget.destroy()
                tk.Label(result_frame, text="회귀 분석 결과가 없습니다 (데이터 부족).",
                         font=("Arial", 11), fg="#E74C3C").pack(pady=20)
            popup.after(0, _err)
            return

        # 포트폴리오 수준 분석 (holdings가 있을 때)
        portfolio_reg = None
        if holdings:
            try:
                import holdings_manager
                port_weights = {}
                total_val = 0
                for t in valid_tickers:
                    h = holdings_manager.get_holding(holdings, t)
                    if h and h.get("quantity", 0) > 0:
                        try:
                            info = yf.Ticker(t).info
                            price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose', 0)
                            val = float(price) * h["quantity"]
                            port_weights[t] = val
                            total_val += val
                        except Exception:
                            pass

                if total_val > 0 and port_weights:
                    # 가중 수익률 계산
                    w_tickers = [t for t in port_weights if t in returns.columns]
                    if w_tickers:
                        w_arr = np.array([port_weights[t] / total_val for t in w_tickers])
                        port_returns = (returns[w_tickers] * w_arr).sum(axis=1)
                        _update_progress(75, "포트폴리오 팩터 분석 중...")
                        portfolio_reg = _run_factor_regression(port_returns, factor_data)
            except Exception as e:
                logging.warning(f"[FF] Portfolio regression error: {e}")

        _update_progress(85, "결과 표시 중...")

        def _show():
            try:
                if not popup.winfo_exists():
                    return
            except tk.TclError:
                return

            for widget in result_frame.winfo_children():
                widget.destroy()

            # ── 1. 팩터 설명 ──
            info_lf = tk.LabelFrame(result_frame, text="팩터 설명", font=("Arial", 10, "bold"))
            info_lf.pack(fill=tk.X, padx=10, pady=5)

            factor_descs = {
                'Mkt-RF': ('시장 초과수익', '시장 전체 수익률 - 무위험이자율. 베타=1이면 시장과 동일하게 움직임'),
                'SMB': ('소형주 효과', 'Small Minus Big. 양수면 소형주 성향, 음수면 대형주 성향'),
                'HML': ('가치주 효과', 'High Minus Low (B/M). 양수면 가치주 성향, 음수면 성장주 성향'),
                'RMW': ('수익성 팩터', 'Robust Minus Weak. 양수면 고수익성 기업 성향'),
                'CMA': ('투자 팩터', 'Conservative Minus Aggressive. 양수면 보수적 투자 기업 성향'),
            }
            for fc in factor_cols:
                if fc in factor_descs:
                    name, desc = factor_descs[fc]
                    row = tk.Frame(info_lf)
                    row.pack(fill=tk.X, padx=8, pady=1)
                    tk.Label(row, text=f"{fc} ({name}):", font=("Arial", 9, "bold"),
                             anchor="w", width=22).pack(side=tk.LEFT)
                    tk.Label(row, text=desc, font=("Arial", 9), fg="#555", anchor="w").pack(side=tk.LEFT)

            note_text = "(ETF 프록시 사용: SPY, IWM, IWD, IWF"
            if n_factors == 5:
                note_text += ", QUAL, SPLV/SPHB"
            note_text += ")"
            tk.Label(info_lf, text=note_text, font=("Arial", 8), fg="#999").pack(padx=8, pady=(0, 3), anchor="w")

            # ── 2. 종목별 회귀 결과 테이블 ──
            table_lf = tk.LabelFrame(result_frame, text="종목별 팩터 분해", font=("Arial", 11, "bold"))
            table_lf.pack(fill=tk.X, padx=10, pady=5)

            cols = ["종목", "Alpha(%)", "t(α)"]
            for fc in factor_cols:
                cols.append(fc)
                cols.append(f"t({fc})")
            cols.extend(["R²", "Adj-R²"])

            tree = ttk.Treeview(table_lf, columns=cols, show="headings",
                                height=min(len(results) + 2, 15))

            col_widths = {"종목": 70, "Alpha(%)": 65, "t(α)": 50, "R²": 50, "Adj-R²": 55}
            for col in cols:
                tree.heading(col, text=col)
                default_w = col_widths.get(col, 55)
                tree.column(col, width=default_w, anchor="e" if col != "종목" else "w", minwidth=40)

            for t, reg in results.items():
                vals = [t]
                vals.append(f"{reg['alpha']:.2f}")
                vals.append(f"{reg['alpha_t']:.2f}")
                for fc in factor_cols:
                    vals.append(f"{reg['betas'].get(fc, 0):.3f}")
                    vals.append(f"{reg['betas_t'].get(fc, 0):.2f}")
                vals.append(f"{reg['r_squared']:.3f}")
                vals.append(f"{reg['adj_r_squared']:.3f}")

                iid = tree.insert("", "end", values=tuple(vals))

                # 알파가 유의미하면 (|t| > 2) 강조 색상
                if abs(reg['alpha_t']) > 2:
                    tag = 'sig_alpha_pos' if reg['alpha'] > 0 else 'sig_alpha_neg'
                    tree.item(iid, tags=(tag,))

            tree.tag_configure('sig_alpha_pos', foreground='#2E7D32')
            tree.tag_configure('sig_alpha_neg', foreground='#E74C3C')

            # 수평 스크롤바
            h_scroll = ttk.Scrollbar(table_lf, orient="horizontal", command=tree.xview)
            tree.configure(xscrollcommand=h_scroll.set)
            tree.pack(fill=tk.X, padx=8, pady=(5, 0))
            h_scroll.pack(fill=tk.X, padx=8, pady=(0, 3))

            tk.Label(table_lf,
                     text="Alpha: 팩터로 설명되지 않는 초과수익 (연%) | |t| > 2.0 이면 통계적으로 유의미 (녹색/빨간색)",
                     font=("Arial", 8), fg="#666").pack(padx=8, pady=(0, 5), anchor="w")

            # ── 3. 포트폴리오 팩터 분해 (holdings 있을 때) ──
            if portfolio_reg:
                port_lf = tk.LabelFrame(result_frame, text="포트폴리오 팩터 분해",
                                        font=("Arial", 11, "bold"))
                port_lf.pack(fill=tk.X, padx=10, pady=5)

                alpha_color = "#2E7D32" if portfolio_reg['alpha'] > 0 else "#E74C3C"
                alpha_sig = " ***" if abs(portfolio_reg['alpha_t']) > 2.58 else \
                            " **" if abs(portfolio_reg['alpha_t']) > 1.96 else ""

                port_rows = [
                    ("연환산 Alpha", f"{portfolio_reg['alpha']:.2f}%{alpha_sig}",
                     f"t={portfolio_reg['alpha_t']:.2f}", alpha_color),
                ]
                for fc in factor_cols:
                    beta_val = portfolio_reg['betas'].get(fc, 0)
                    t_val = portfolio_reg['betas_t'].get(fc, 0)
                    sig = " ***" if abs(t_val) > 2.58 else " **" if abs(t_val) > 1.96 else ""
                    beta_color = "#000"
                    if abs(t_val) > 1.96:
                        beta_color = "#2E7D32" if beta_val > 0 else "#E74C3C"
                    port_rows.append((f"{fc} 노출도", f"{beta_val:.3f}{sig}", f"t={t_val:.2f}", beta_color))

                port_rows.append(("R²", f"{portfolio_reg['r_squared']:.3f}", "", "#000"))
                port_rows.append(("조정 R²", f"{portfolio_reg['adj_r_squared']:.3f}", "", "#000"))
                port_rows.append(("잔차 변동성 (연)", f"{portfolio_reg['residual_std']:.1%}", "팩터로 설명 안 되는 위험", "#000"))

                for label_text, value_text, note_text, color in port_rows:
                    row = tk.Frame(port_lf)
                    row.pack(fill=tk.X, padx=8, pady=1)
                    tk.Label(row, text=label_text, font=("Arial", 9, "bold"),
                             anchor="w", width=18).pack(side=tk.LEFT)
                    tk.Label(row, text=value_text, font=("Arial", 9, "bold"),
                             fg=color, width=14, anchor="e").pack(side=tk.LEFT)
                    if note_text:
                        tk.Label(row, text=note_text, font=("Arial", 8), fg="#888",
                                 anchor="w").pack(side=tk.LEFT, padx=5)

                # 수익 분해 요약
                explained = sum(portfolio_reg['betas'].get(fc, 0) *
                                factor_data[fc].mean() * 252 for fc in factor_cols)
                total_explained = explained + portfolio_reg['alpha'] / 100 * 1  # alpha는 이미 %
                decomp_frame = tk.Frame(port_lf)
                decomp_frame.pack(fill=tk.X, padx=8, pady=(5, 3))
                tk.Label(decomp_frame, text="수익 분해 (연율):", font=("Arial", 9, "bold")).pack(anchor="w")
                decomp_text = f"  Alpha: {portfolio_reg['alpha']:.2f}%"
                for fc in factor_cols:
                    contrib = portfolio_reg['betas'].get(fc, 0) * factor_data[fc].mean() * 252 * 100
                    decomp_text += f"  |  {fc}: {contrib:.2f}%"
                tk.Label(decomp_frame, text=decomp_text, font=("Arial", 9), fg="#333").pack(anchor="w")

                tk.Label(port_lf,
                         text="***: p<0.01, **: p<0.05 | Alpha>0은 팩터 대비 초과수익 | R²가 높을수록 팩터 설명력 높음",
                         font=("Arial", 8), fg="#666").pack(padx=8, pady=(0, 5), anchor="w")

            # ── 4. 팩터 노출도 바 차트 ──
            chart_lf = tk.LabelFrame(result_frame, text="팩터 노출도 차트", font=("Arial", 11, "bold"))
            chart_lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

            n_tickers = len(results)
            fig_height = max(4, min(8, 1.5 + n_tickers * 0.4))
            fig, axes = plt.subplots(1, len(factor_cols), figsize=(min(12, 3 * len(factor_cols)), fig_height),
                                     sharey=True)
            if len(factor_cols) == 1:
                axes = [axes]

            ticker_names = list(results.keys())
            y_pos = np.arange(len(ticker_names))

            for ax_idx, fc in enumerate(factor_cols):
                ax = axes[ax_idx]
                betas = [results[t]['betas'].get(fc, 0) for t in ticker_names]
                t_stats = [results[t]['betas_t'].get(fc, 0) for t in ticker_names]
                colors = []
                for b, ts in zip(betas, t_stats):
                    if abs(ts) > 1.96:
                        colors.append('#2E7D32' if b > 0 else '#E74C3C')
                    else:
                        colors.append('#AAAAAA')

                ax.barh(y_pos, betas, color=colors, edgecolor='white', height=0.6)
                ax.axvline(x=0, color='black', linewidth=0.5)
                ax.set_title(fc, fontsize=10, fontweight='bold')
                ax.set_xlabel('Beta', fontsize=8)
                ax.grid(axis='x', alpha=0.3)
                ax.tick_params(axis='x', labelsize=8)

                if ax_idx == 0:
                    ax.set_yticks(y_pos)
                    ax.set_yticklabels(ticker_names, fontsize=8)

                # 포트폴리오 표시
                if portfolio_reg:
                    port_beta = portfolio_reg['betas'].get(fc, 0)
                    ax.axvline(x=port_beta, color='#4A90D9', linewidth=1.5, linestyle='--',
                               label=f'포트폴리오 ({port_beta:.2f})')
                    ax.legend(fontsize=7, loc='best')

            fig.suptitle(f"Fama-French {n_factors}팩터 노출도 (|t|>2.0 유의: 색상, 비유의: 회색)",
                         fontsize=11, fontweight='bold')
            fig.tight_layout(rect=[0, 0, 1, 0.95])

            open_figs.append(fig)
            canvas_fig = FigureCanvasTkAgg(fig, master=chart_lf)
            canvas_fig.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            canvas_fig.draw()

            # ── 5. Alpha 차트 ──
            alpha_lf = tk.LabelFrame(result_frame, text="종목별 Alpha", font=("Arial", 11, "bold"))
            alpha_lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

            fig2, ax2 = plt.subplots(figsize=(min(10, 2 + n_tickers * 0.5), 4))
            alphas = [results[t]['alpha'] for t in ticker_names]
            alpha_ts = [results[t]['alpha_t'] for t in ticker_names]
            colors_alpha = []
            for a, at in zip(alphas, alpha_ts):
                if abs(at) > 1.96:
                    colors_alpha.append('#2E7D32' if a > 0 else '#E74C3C')
                else:
                    colors_alpha.append('#AAAAAA')

            bars = ax2.bar(range(len(ticker_names)), alphas, color=colors_alpha, edgecolor='white')
            ax2.set_xticks(range(len(ticker_names)))
            ax2.set_xticklabels(ticker_names, rotation=45, ha='right', fontsize=8)
            ax2.axhline(y=0, color='black', linewidth=0.5)
            ax2.set_ylabel('Alpha (연%, 팩터 대비 초과수익)', fontsize=9)
            ax2.set_title('종목별 연환산 Alpha (유의미: 색상, 비유의: 회색)', fontsize=10, fontweight='bold')
            ax2.grid(axis='y', alpha=0.3)

            # 포트폴리오 알파 라인
            if portfolio_reg:
                ax2.axhline(y=portfolio_reg['alpha'], color='#4A90D9', linewidth=1.5, linestyle='--',
                            label=f"포트폴리오 Alpha: {portfolio_reg['alpha']:.2f}%")
                ax2.legend(fontsize=8)

            plt.tight_layout()
            open_figs.append(fig2)
            canvas_fig2 = FigureCanvasTkAgg(fig2, master=alpha_lf)
            canvas_fig2.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            canvas_fig2.draw()

        try:
            popup.after(0, _show)
        except tk.TclError:
            pass

      except Exception as e:
        logging.error(f"[FF] Analysis error: {e}")
        try:
            popup.after(0, lambda: _progress["label"].config(
                text=f"오류 발생: {e}") if _progress["label"] else None)
        except tk.TclError:
            pass

    def _start_analysis():
        for widget in result_frame.winfo_children():
            widget.destroy()
        for f in open_figs:
            plt.close(f)
        open_figs.clear()

        lbl = tk.Label(result_frame, text="준비 중...", font=("Arial", 12))
        lbl.pack(pady=10)
        bar = ttk.Progressbar(result_frame, maximum=100, length=300)
        bar.pack(pady=(0, 10))
        _progress["label"] = lbl
        _progress["bar"] = bar
        threading.Thread(target=_run_analysis, daemon=True).start()

    analyze_btn = tk.Button(config_inner, text="분석", command=_start_analysis,
                            font=("Arial", 10, "bold"))
    analyze_btn.pack(side=tk.LEFT, padx=8)

    # 필터 변경 시 재분석은 수동 버튼으로
    filter_var.trace_add('write', lambda *_: None)

    def _on_close():
        for f in open_figs:
            plt.close(f)
        scroll_canvas.unbind_all("<MouseWheel>")
        popup.destroy()

    popup.protocol("WM_DELETE_WINDOW", _on_close)

    # 초기 분석 실행
    _start_analysis()


# ============================================================
# Black-Litterman Portfolio Model
# ============================================================

def _black_litterman(market_caps, cov_matrix, risk_aversion=2.5,
                     views=None, view_confidences=None, tau=0.05):
    """Black-Litterman 모델로 최적 비중 계산."""
    tickers = list(cov_matrix.columns)
    n = len(tickers)
    sigma = cov_matrix.values

    total_cap = sum(market_caps.get(t, 1) for t in tickers)
    w_market = np.array([market_caps.get(t, 1) / total_cap for t in tickers])

    # Implied equilibrium returns
    pi = risk_aversion * sigma @ w_market

    if not views:
        return {
            'tickers': tickers, 'weights': w_market,
            'expected_returns': pi, 'prior_returns': pi,
        }

    k = len(views)
    P = np.zeros((k, n))
    Q = np.zeros(k)
    for i, view in enumerate(views):
        t = view['ticker']
        if t in tickers:
            P[i, tickers.index(t)] = 1.0
            Q[i] = view['return']

    if view_confidences:
        omega_diag = []
        for i, conf in enumerate(view_confidences):
            c = max(0.01, min(conf, 0.99))
            omega_diag.append(tau * (P[i] @ sigma @ P[i].T) * (1 - c) / c)
        omega = np.diag(omega_diag)
    else:
        omega = tau * np.diag(np.diag(P @ sigma @ P.T))

    tau_sigma_inv = np.linalg.inv(tau * sigma)
    omega_inv = np.linalg.inv(omega)
    M = np.linalg.inv(tau_sigma_inv + P.T @ omega_inv @ P)
    bl_returns = M @ (tau_sigma_inv @ pi + P.T @ omega_inv @ Q)

    bl_weights = np.linalg.inv(risk_aversion * sigma) @ bl_returns
    bl_weights = np.maximum(bl_weights, 0)
    if bl_weights.sum() > 0:
        bl_weights = bl_weights / bl_weights.sum()
    else:
        bl_weights = w_market

    return {
        'tickers': tickers, 'weights': bl_weights,
        'expected_returns': bl_returns, 'prior_returns': pi,
    }


def open_black_litterman_popup(watchlist, holdings=None):
    """Black-Litterman 포트폴리오 최적화 팝업."""
    if len(watchlist) < 2:
        messagebox.showinfo("알림", "포트폴리오 최적화는 2개 이상의 종목이 필요합니다.")
        return

    popup = tk.Toplevel()
    popup.title("Black-Litterman 포트폴리오 최적화")
    popup.state('zoomed')

    bl_canvas = tk.Canvas(popup, highlightthickness=0)
    vsb = ttk.Scrollbar(popup, orient="vertical", command=bl_canvas.yview)
    inner = tk.Frame(bl_canvas)
    inner.bind("<Configure>", lambda e: bl_canvas.configure(scrollregion=bl_canvas.bbox("all")))
    bl_canvas.create_window((0, 0), window=inner, anchor="nw")
    bl_canvas.configure(yscrollcommand=vsb.set)
    bl_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _on_mousewheel(event):
        bl_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    bl_canvas.bind_all("<MouseWheel>", _on_mousewheel)

    open_figs = []

    desc_frame = tk.LabelFrame(inner, text="Black-Litterman 모델", font=("Arial", 10, "bold"))
    desc_frame.pack(fill=tk.X, padx=10, pady=5)
    tk.Label(desc_frame, text=(
        "시장 시가총액 비중을 사전분포(Prior)로 사용하고,\n"
        "사용자의 전망(View)을 반영하여 최적 포트폴리오 비중을 산출합니다.\n"
        "전망이 없으면 시장 균형 비중을 반환합니다."
    ), font=("Arial", 9), justify=tk.LEFT, wraplength=800).pack(padx=8, pady=5, anchor="w")

    view_frame = tk.LabelFrame(inner, text="투자 전망 입력 (선택사항)", font=("Arial", 10, "bold"))
    view_frame.pack(fill=tk.X, padx=10, pady=5)

    view_entries = []
    view_list_frame = tk.Frame(view_frame)
    view_list_frame.pack(fill=tk.X, padx=8, pady=5)

    tk.Label(view_list_frame, text="종목", font=("Arial", 9, "bold"), width=8).grid(row=0, column=0)
    tk.Label(view_list_frame, text="예상 연수익률(%)", font=("Arial", 9, "bold"), width=15).grid(row=0, column=1)
    tk.Label(view_list_frame, text="확신도(0~1)", font=("Arial", 9, "bold"), width=12).grid(row=0, column=2)

    for i, ticker in enumerate(watchlist[:10]):
        row_idx = i + 1
        tk.Label(view_list_frame, text=ticker, font=("Arial", 9), width=8).grid(row=row_idx, column=0)
        ret_entry = tk.Entry(view_list_frame, width=10, font=("Arial", 9))
        ret_entry.grid(row=row_idx, column=1, padx=3)
        conf_entry = tk.Entry(view_list_frame, width=8, font=("Arial", 9))
        conf_entry.insert(0, "0.5")
        conf_entry.grid(row=row_idx, column=2, padx=3)
        view_entries.append((ticker, ret_entry, conf_entry))

    tk.Label(view_frame, text="예상 수익률을 입력한 종목만 전망에 반영됩니다. 비워두면 시장 균형 비중만 표시합니다.",
             font=("Arial", 8), fg="#777777").pack(padx=8, pady=(0, 5), anchor="w")

    result_frame = tk.Frame(inner)
    result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    def _run_bl():
        for widget in result_frame.winfo_children():
            widget.destroy()
        for f in open_figs:
            plt.close(f)
        open_figs.clear()

        loading = tk.Label(result_frame, text="데이터 수집 중...", font=("Arial", 11))
        loading.pack(pady=10)
        pbar = ttk.Progressbar(result_frame, maximum=100, length=300)
        pbar.pack(pady=5)

        def _compute():
            try:
                tickers = list(watchlist)[:20]
                returns = _download_returns(tickers, period="1y")
                if returns.empty or returns.shape[1] < 2:
                    popup.after(0, lambda: loading.config(text="수익률 데이터를 가져올 수 없습니다."))
                    return

                valid_tickers = [t for t in tickers if t in returns.columns]
                returns = returns[valid_tickers]
                cov_mat = returns.cov() * 252

                market_caps = {}
                name_map = {}
                for i, t in enumerate(valid_tickers):
                    try:
                        popup.after(0, lambda p=30+40*i//len(valid_tickers), txt=f"시가총액 수집 중... {t}":
                                    (pbar.configure(value=p), loading.config(text=txt)))
                    except tk.TclError:
                        pass
                    try:
                        info = yf.Ticker(t).info
                        mc = info.get('marketCap', None)
                        market_caps[t] = float(mc) if mc else 1e9
                        name_map[t] = info.get('shortName', t)
                    except Exception:
                        market_caps[t] = 1e9
                        name_map[t] = t

                views = []
                view_confs = []
                for ticker, ret_entry, conf_entry in view_entries:
                    if ticker not in valid_tickers:
                        continue
                    ret_text = ret_entry.get().strip()
                    if not ret_text:
                        continue
                    try:
                        ret_val = float(ret_text) / 100.0
                        conf_val = float(conf_entry.get().strip() or "0.5")
                        conf_val = max(0.01, min(0.99, conf_val))
                        views.append({'ticker': ticker, 'return': ret_val})
                        view_confs.append(conf_val)
                    except ValueError:
                        continue

                bl_result = _black_litterman(
                    market_caps, cov_mat,
                    views=views if views else None,
                    view_confidences=view_confs if view_confs else None,
                )
                std_opt = _optimize_portfolio(returns, method='max_sharpe')

                def _show():
                    try:
                        if not popup.winfo_exists():
                            return
                    except tk.TclError:
                        return
                    loading.destroy()
                    pbar.destroy()

                    bl_tickers = bl_result['tickers']
                    bl_wts = bl_result['weights']
                    bl_rets = bl_result['expected_returns']
                    prior_rets = bl_result['prior_returns']

                    table_lf = tk.LabelFrame(result_frame, text="Black-Litterman 최적 비중",
                                             font=("Arial", 10, "bold"))
                    table_lf.pack(fill=tk.X, pady=5)

                    hdr = tk.Frame(table_lf)
                    hdr.pack(fill=tk.X, padx=8, pady=2)
                    for txt, wid in [("종목", 10), ("이름", 16), ("시총비중", 8),
                                      ("BL비중", 8), ("최대샤프", 8), ("균형수익률", 9), ("BL수익률", 9)]:
                        tk.Label(hdr, text=txt, font=("Arial", 9, "bold"),
                                 width=wid, anchor="center").pack(side=tk.LEFT)

                    total_cap = sum(market_caps.get(t, 1) for t in bl_tickers)
                    for idx, t in enumerate(bl_tickers):
                        row = tk.Frame(table_lf)
                        row.pack(fill=tk.X, padx=8, pady=1)
                        mkt_w = market_caps.get(t, 1) / total_cap * 100
                        bl_w = bl_wts[idx] * 100
                        std_w = std_opt['weights'][std_opt['tickers'].index(t)] * 100 if t in std_opt['tickers'] else 0
                        pr = prior_rets[idx] * 100
                        blr = bl_rets[idx] * 100

                        tk.Label(row, text=t, font=("Arial", 9), width=10, anchor="w").pack(side=tk.LEFT)
                        tk.Label(row, text=name_map.get(t, t)[:14], font=("Arial", 9), width=16, anchor="w").pack(side=tk.LEFT)
                        tk.Label(row, text=f"{mkt_w:.1f}%", font=("Arial", 9), width=8).pack(side=tk.LEFT)
                        bl_clr = "#2E7D32" if bl_w > mkt_w * 1.2 else "#E74C3C" if bl_w < mkt_w * 0.8 else "black"
                        tk.Label(row, text=f"{bl_w:.1f}%", font=("Arial", 9, "bold"),
                                 width=8, fg=bl_clr).pack(side=tk.LEFT)
                        tk.Label(row, text=f"{std_w:.1f}%", font=("Arial", 9), width=8).pack(side=tk.LEFT)
                        tk.Label(row, text=f"{pr:.1f}%", font=("Arial", 9), width=9).pack(side=tk.LEFT)
                        r_clr = "#2E7D32" if blr > pr else "#E74C3C" if blr < pr else "black"
                        tk.Label(row, text=f"{blr:.1f}%", font=("Arial", 9), width=9, fg=r_clr).pack(side=tk.LEFT)

                    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
                    x = np.arange(len(bl_tickers))
                    bw = 0.25
                    mkt_arr = [market_caps.get(t, 1) / total_cap * 100 for t in bl_tickers]
                    bl_arr = [ww * 100 for ww in bl_wts]
                    std_arr = [std_opt['weights'][std_opt['tickers'].index(t)] * 100
                               if t in std_opt['tickers'] else 0 for t in bl_tickers]

                    axes[0].bar(x - bw, mkt_arr, bw, label='시총비중', alpha=0.7)
                    axes[0].bar(x, bl_arr, bw, label='BL비중', alpha=0.9)
                    axes[0].bar(x + bw, std_arr, bw, label='최대샤프', alpha=0.7)
                    axes[0].set_xticks(x)
                    axes[0].set_xticklabels(bl_tickers, rotation=45, ha='right', fontsize=8)
                    axes[0].set_ylabel("비중 (%)")
                    axes[0].set_title("비중 비교", fontsize=11, fontweight="bold")
                    axes[0].legend(fontsize=8)
                    axes[0].grid(alpha=0.3, axis='y')

                    axes[1].bar(x - 0.15, [r * 100 for r in prior_rets], 0.3,
                                label='균형 수익률', alpha=0.7, color='steelblue')
                    axes[1].bar(x + 0.15, [r * 100 for r in bl_rets], 0.3,
                                label='BL 수익률', alpha=0.9, color='darkorange')
                    axes[1].set_xticks(x)
                    axes[1].set_xticklabels(bl_tickers, rotation=45, ha='right', fontsize=8)
                    axes[1].set_ylabel("예상 연수익률 (%)")
                    axes[1].set_title("수익률 비교", fontsize=11, fontweight="bold")
                    axes[1].legend(fontsize=8)
                    axes[1].grid(alpha=0.3, axis='y')

                    plt.tight_layout()
                    open_figs.append(fig)
                    chart_c = FigureCanvasTkAgg(fig, master=result_frame)
                    chart_c.get_tk_widget().pack(fill=tk.BOTH, expand=True, pady=5)
                    chart_c.draw()

                    vt = f"입력된 전망 {len(views)}개가 반영되었습니다." if views else \
                         "전망 미입력 → 시장 균형(시총) 비중을 표시합니다."
                    tk.Label(result_frame, text=vt, font=("Arial", 9), fg="#555555").pack(pady=3)

                popup.after(0, _show)
            except Exception as e:
                logging.error(f"[BLACK-LITTERMAN] Error: {e}")
                try:
                    popup.after(0, lambda: loading.config(text=f"오류: {e}"))
                except tk.TclError:
                    pass

        threading.Thread(target=_compute, daemon=True).start()

    run_btn = tk.Button(inner, text="BL 최적화 실행", command=_run_bl,
                        font=("Arial", 10, "bold"), bg="#4CAF50", fg="white")
    run_btn.pack(pady=10)

    def _on_bl_close():
        for f in open_figs:
            plt.close(f)
        bl_canvas.unbind_all("<MouseWheel>")
        popup.destroy()

    popup.protocol("WM_DELETE_WINDOW", _on_bl_close)
