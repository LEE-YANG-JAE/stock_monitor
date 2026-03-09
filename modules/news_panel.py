"""
뉴스 패널 모듈: finviz에서 미국 주식 뉴스를 크롤링하여 표시.
워치리스트 종목뿐 아니라 전체 시장 뉴스를 보여주며, 상승/하락를 자동 분류.
"""

import logging
import re
import threading
import tkinter as tk
import webbrowser

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_FINVIZ_URL = "https://finviz.com/news.ashx?v=3"
_FINVIZ_BASE = "https://finviz.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# 키워드 기반 감성 분석 사전
_POSITIVE_KEYWORDS = [
    "upgrade", "upgrades", "upgraded", "beat", "beats", "beating",
    "raises guidance", "raised guidance", "raises forecast",
    "record revenue", "record profit", "record earnings",
    "fda approves", "fda approval", "approved by fda",
    "partnership", "acquisition", "acquires",
    "outperform", "buy rating", "price target raised",
    "strong demand", "surges", "soars", "rallies", "jumps",
    "dividend increase", "buyback", "stock split",
    "all-time high", "breakthrough", "innovation",
    "beats estimates", "exceeds expectations", "tops forecast",
]
_NEGATIVE_KEYWORDS = [
    "downgrade", "downgrades", "downgraded",
    "miss", "misses", "missed",
    "lowers guidance", "lowered guidance", "cuts forecast",
    "layoff", "layoffs", "job cuts", "restructuring",
    "fda rejects", "fda rejection", "recall",
    "lawsuit", "investigation", "probe", "fraud", "sec charges",
    "underperform", "sell rating", "price target cut",
    "weak demand", "plunges", "crashes", "tumbles", "drops",
    "dividend cut", "bankruptcy", "default",
    "warning", "profit warning", "revenue decline",
    "ceo resigns", "cfo resigns", "accounting issues",
]


def _keyword_sentiment(title):
    """제목에서 키워드 기반 감성 분석. Returns: (score, label, color) or None."""
    title_lower = title.lower()
    pos_count = sum(1 for kw in _POSITIVE_KEYWORDS if kw in title_lower)
    neg_count = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in title_lower)
    if pos_count > neg_count:
        return 1, "상승", "#2E7D32"
    elif neg_count > pos_count:
        return -1, "하락", "#E74C3C"
    return None


# ============================================================
# finviz 뉴스 크롤링
# ============================================================
def fetch_finviz_news(max_items=50):
    """finviz 뉴스 페이지에서 최신 뉴스를 크롤링.

    Returns:
        list[dict]: 뉴스 항목 리스트. 각 항목은:
            ticker, title, sentiment, color, url, time, source
    """
    try:
        resp = requests.get(_FINVIZ_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("finviz 뉴스 요청 실패: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    news_div = soup.find(class_="news")
    if not news_div:
        logger.warning("finviz 뉴스 테이블을 찾을 수 없음")
        return []

    rows = news_div.find_all("tr")
    results = []

    for row in rows[:max_items]:
        date_cell = row.find("td", class_="news_date-cell")
        link_cell = row.find("td", class_="news_link-cell")
        if not link_cell:
            continue

        # 뉴스 제목 & URL
        a_tag = link_cell.find("a", class_="nn-tab-link")
        if not a_tag:
            continue

        title = a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        if href.startswith("/"):
            href = _FINVIZ_BASE + href

        # 시간
        time_text = date_cell.get_text(strip=True) if date_cell else ""

        # 종목 배지 (티커 + 감성)
        badges = link_cell.find_all("a", class_="stock-news-label")
        tickers = []
        sentiment = "중립"
        color = "#555555"

        for badge in badges:
            classes = " ".join(badge.get("class", []))
            # 티커명 추출 (span 내부 텍스트 = 순수 티커)
            span = badge.find("span", class_="font-semibold")
            ticker_name = span.get_text(strip=True) if span else badge.get_text(strip=True)
            # 퍼센트 변동 제거 (FIGR+7.10% -> FIGR)
            ticker_name = re.sub(r"[+\-][\d.]+%$", "", ticker_name)
            tickers.append(ticker_name)

            # 감성 판단 (배지 CSS 클래스 기반)
            if "is-positive" in classes:
                sentiment = "상승"
                color = "#2E7D32"
            elif "is-negative" in classes:
                sentiment = "하락"
                color = "#E74C3C"

        # 소스 (두 번째 span with news_date-cell inside link_cell)
        source_spans = link_cell.find_all("span", class_="news_date-cell")
        source = source_spans[0].get_text(strip=True) if source_spans else ""

        ticker_str = ", ".join(tickers) if tickers else "-"

        # 키워드 기반 감성 분석으로 CSS 기반 결과 보강
        kw_result = _keyword_sentiment(title)
        if sentiment == "중립" and kw_result is not None:
            # CSS가 중립인데 키워드가 감성을 감지한 경우 → 키워드 결과 적용
            _, sentiment, color = kw_result

        # 센티먼트 수치화: 상승=+1, 하락=-1, 중립=0
        sentiment_score = {"상승": 1, "하락": -1, "중립": 0}.get(sentiment, 0)

        results.append({
            "ticker": ticker_str,
            "tickers": tickers,
            "title": title,
            "sentiment": sentiment,
            "sentiment_score": sentiment_score,
            "color": color,
            "url": href,
            "time": time_text,
            "source": source,
        })

    return results


def get_ticker_sentiment_score(news_list, ticker_symbol):
    """특정 종목의 뉴스 센티먼트 점수 합산."""
    score = 0
    count = 0
    for item in news_list:
        if ticker_symbol in item.get("tickers", []):
            score += item.get("sentiment_score", 0)
            count += 1
    return score, count


def get_market_sentiment_summary(news_list):
    """전체 시장 센티먼트 요약."""
    positive = sum(1 for n in news_list if n.get("sentiment_score", 0) > 0)
    negative = sum(1 for n in news_list if n.get("sentiment_score", 0) < 0)
    neutral = sum(1 for n in news_list if n.get("sentiment_score", 0) == 0)
    total_score = sum(n.get("sentiment_score", 0) for n in news_list)
    total = len(news_list)

    if total_score > 2:
        label = "긍정"
    elif total_score < -2:
        label = "부정"
    else:
        label = "중립"

    return {
        "score": total_score,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "total": total,
        "label": label,
    }


# ============================================================
# 종목별 뉴스 조회 (yfinance)
# ============================================================
def fetch_ticker_news(ticker_symbol, count=25):
    """yfinance를 통해 특정 종목의 뉴스와 현재가를 조회.

    Args:
        ticker_symbol: 종목 티커 (예: "AAPL")
        count: 가져올 뉴스 개수 (기본 25)

    Returns:
        tuple: (news_list, current_price)
            news_list: list[dict] — 각 항목: title, publisher, url, time
            current_price: float 또는 None
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker_symbol)

        # 현재가 조회
        current_price = None
        try:
            current_price = t.fast_info.get("lastPrice")
        except Exception:
            pass

        # 뉴스 조회
        raw_news = t.get_news(count=count) or []
        news_list = []
        for item in raw_news:
            content = item.get("content", item)
            title = content.get("title", "")
            # publisher
            provider = content.get("provider", {})
            publisher = provider.get("displayName", "") if isinstance(provider, dict) else ""
            # URL
            click_url = content.get("clickThroughUrl", {})
            url = click_url.get("url", "") if isinstance(click_url, dict) else ""
            if not url:
                canon = content.get("canonicalUrl", {})
                url = canon.get("url", "") if isinstance(canon, dict) else ""
            # 날짜
            pub_date = content.get("pubDate", "")
            time_str = ""
            if pub_date:
                from datetime import datetime as _dt
                try:
                    time_str = _dt.fromisoformat(pub_date.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    time_str = pub_date[:16] if len(pub_date) >= 16 else pub_date
            news_list.append({
                "title": title,
                "publisher": publisher,
                "url": url,
                "time": time_str,
            })

        return news_list, current_price
    except Exception as e:
        logger.warning("종목 뉴스 조회 실패 (%s): %s", ticker_symbol, e)
        return [], None


# ============================================================
# 뉴스 패널 UI
# ============================================================
class NewsPanel(tk.LabelFrame):
    """뉴스 목록을 표시하는 스크롤 가능한 패널."""

    def __init__(self, parent, app_state=None, **kwargs):
        super().__init__(parent, text="  뉴스  ", font=("Arial", 10, "bold"), **kwargs)
        self._app_state = app_state
        self._refreshing = False

        # 상단 버튼 바
        btn_bar = tk.Frame(self)
        btn_bar.pack(fill=tk.X, padx=4, pady=(2, 0))
        tk.Label(
            btn_bar, text="제목 클릭: 기사 열기 | 티커 클릭: 종목 상세",
            font=("Arial", 8), fg="#333333"
        ).pack(side=tk.LEFT)
        self._refresh_btn = tk.Button(
            btn_bar, text="뉴스 새로고침", font=("Arial", 9),
            command=self._on_refresh_click, cursor="hand2"
        )
        self._refresh_btn.pack(side=tk.RIGHT)

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner_frame = tk.Frame(self.canvas)

        self.inner_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self._window_id = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # inner_frame 너비를 canvas에 맞추기
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 마우스 휠 스크롤
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

        # 초기 메시지
        self._placeholder = tk.Label(
            self.inner_frame, text="뉴스를 불러오는 중...",
            font=("Arial", 10), fg="#444444", pady=10
        )
        self._placeholder.pack(anchor="w", padx=10)

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self._window_id, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_wheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_wheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_refresh_click(self):
        """뉴스 새로고침 버튼 클릭."""
        if self._refreshing:
            return
        self._refreshing = True
        self._refresh_btn.config(state=tk.DISABLED, text="불러오는 중...")

        def _fetch():
            news = fetch_finviz_news()
            try:
                self.after(0, lambda: self._finish_refresh(news))
            except tk.TclError:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _finish_refresh(self, news):
        self.update_news(news)
        self._refreshing = False
        self._refresh_btn.config(state=tk.NORMAL, text="뉴스 새로고침")

    def _open_ticker_popup(self, ticker_symbol):
        """티커 클릭 시 백테스트 팝업 열기 (종목명 포함)."""
        from backtest_popup import open_backtest_popup
        try:
            import yfinance as yf
            name = yf.Ticker(ticker_symbol).info.get('shortName', '')
            stock = f"{name} ({ticker_symbol})" if name else ticker_symbol
        except Exception:
            stock = ticker_symbol
        open_backtest_popup(stock, app_state=self._app_state)

    def update_news(self, news_list):
        """뉴스 목록 갱신."""
        for widget in self.inner_frame.winfo_children():
            widget.destroy()

        if not news_list:
            tk.Label(
                self.inner_frame, text="뉴스 없음",
                font=("Arial", 10), fg="#444444", pady=10
            ).pack(anchor="w", padx=10)
            return

        # 센티먼트 요약 바
        summary = get_market_sentiment_summary(news_list)
        summary_frame = tk.Frame(self.inner_frame)
        summary_frame.pack(fill=tk.X, padx=6, pady=(4, 2))

        score_color = "#2E7D32" if summary["score"] > 0 else "#E74C3C" if summary["score"] < 0 else "#555555"
        tk.Label(summary_frame,
                 text=f"시장 센티먼트: {summary['label']} ({summary['score']:+d})",
                 font=("Arial", 9, "bold"), fg=score_color).pack(side=tk.LEFT, padx=4)
        tk.Label(summary_frame,
                 text=f"상승 {summary['positive']} | 하락 {summary['negative']} | 중립 {summary['neutral']}",
                 font=("Arial", 8), fg="#666666").pack(side=tk.LEFT, padx=8)

        for item in news_list:
            row = tk.Frame(self.inner_frame)
            row.pack(fill=tk.X, padx=6, pady=1)

            # 감성 라벨
            tk.Label(
                row, text=f"[{item['sentiment']}]",
                font=("Arial", 9, "bold"), fg=item["color"], width=6
            ).pack(side=tk.LEFT, padx=(4, 2))

            # 종목명 — 개별 티커 클릭 시 백테스트 팝업
            tickers_list = item.get("tickers", [])
            if tickers_list:
                for i, t in enumerate(tickers_list):
                    lbl = tk.Label(
                        row, text=t,
                        font=("Arial", 9, "bold"), fg="#4A90D9",
                        cursor="hand2", anchor="w"
                    )
                    lbl.pack(side=tk.LEFT, padx=(0, 2 if i < len(tickers_list) - 1 else 4))
                    lbl.bind("<Button-1>", lambda e, sym=t: self._open_ticker_popup(sym))
            else:
                tk.Label(
                    row, text="-",
                    font=("Arial", 9, "bold"), fg="#4A90D9", anchor="w"
                ).pack(side=tk.LEFT, padx=(0, 4))

            # 뉴스 제목 — 싱글클릭으로 기사 열기
            url = item.get("url", "")
            title_label = tk.Label(
                row, text=item["title"],
                font=("Arial", 9), fg="#222222", anchor="w",
                cursor="hand2" if url else ""
            )
            title_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

            if url:
                title_label.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))

            # 시간 + 소스
            info_parts = []
            if item.get("source"):
                info_parts.append(item["source"])
            if item.get("time"):
                info_parts.append(item["time"])
            if info_parts:
                tk.Label(
                    row, text=" | ".join(info_parts),
                    font=("Arial", 8), fg="#333333"
                ).pack(side=tk.RIGHT, padx=(4, 6))

        # 스크롤 위치 초기화
        self.canvas.yview_moveto(0)


# ============================================================
# 뉴스 갱신 타이머
# ============================================================
def start_news_refresh(app, interval_ms=300000):
    """첫 실행 후 5분마다 뉴스 갱신. root.after() 사용."""

    def _do_refresh():
        if app.shutdown_event.is_set():
            return

        def _fetch():
            news = fetch_finviz_news()
            if not app.shutdown_event.is_set() and app.root:
                app.root.after(0, lambda: _update_ui(news))

        def _update_ui(news):
            if app.news_panel:
                app.news_panel.update_news(news)
            # 다음 갱신 예약
            if not app.shutdown_event.is_set():
                app.root.after(interval_ms, _do_refresh)

        thread = threading.Thread(target=_fetch, daemon=True)
        thread.start()

    # 첫 실행: 0.5초 후 시작
    app.root.after(500, _do_refresh)
