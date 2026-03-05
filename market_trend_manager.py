import logging
import time
import threading
from datetime import datetime, time as dt_time

import holidays
import pytz
import yfinance as yf

# Momentum weight constants (Phase 8-4)
MACD_WEIGHT = 2
MA_WEIGHT = 1
BB_WEIGHT = 1
RSI_WEIGHT = 1

# Signal threshold constants
STRONG_BUY_THRESHOLD = 4
BUY_THRESHOLD = 2
SELL_THRESHOLD = -2
STRONG_SELL_THRESHOLD = -4


class MarketTrendManager:
    def __init__(self, index_ticker="SPY", refresh_interval=600):
        self.index_ticker = index_ticker
        self.refresh_interval = refresh_interval  # seconds (10 minutes)
        self.last_refresh_time = 0
        self.market_trend = "Unknown"
        self.market_source = ""
        self.market_session = ""
        self.momentum = ""
        self._lock = threading.Lock()  # Phase 2-3: cache thread safety

    def detect_market_trend(self):
        try:
            data = yf.Ticker(self.index_ticker).history(period="3mo", interval="1d")
            if data.empty:
                return "Unknown"

            ma20 = data['Close'].rolling(window=20).mean()
            ma60 = data['Close'].rolling(window=60).mean()

            if ma20.iloc[-1] > ma60.iloc[-1] * 1.01:
                return "Uptrend"
            elif ma20.iloc[-1] < ma60.iloc[-1] * 0.99:
                return "Downtrend"
            else:
                return "Sideways"
        except (ConnectionError, TimeoutError) as e:
            logging.error(f"[MARKET] Network error detecting trend: {e}")
            return "Unknown"
        except Exception as e:
            logging.error(f"[MARKET] Error detecting market trend: {e}")
            return "Unknown"

    def get_market_trend(self):
        with self._lock:
            current_time = time.time()
            if current_time - self.last_refresh_time > self.refresh_interval:
                self.market_trend = self.detect_market_trend()
                self.last_refresh_time = current_time
            return self.market_trend

    def guess_market_source(self, ticker):
        try:
            info = yf.Ticker(ticker).info
            exchange = info.get('exchange', '')
            sector = info.get('sector', '')

            # Phase 3-3: if→elif fix, ensure default "SPY" return
            if exchange in ("NasdaqGS", "Nasdaq"):
                self.market_source = "QQQ"
            elif sector in ("Technology", "Semiconductors", "Internet"):
                self.market_source = "QQQ"
            else:
                self.market_source = "SPY"
            return self.market_source
        except Exception as e:
            logging.error(f"[MARKET] Error guessing source for {ticker}: {e}")
            self.market_source = "SPY"
            return "SPY"


def guess_market_session():
    """시장 세션 판별 — 통합 함수 (Phase 3-11)"""
    ny_time_zone = pytz.timezone('America/New_York')
    now = datetime.now(ny_time_zone)
    ny_time = now.time()

    us_holidays = holidays.country_holidays('US')
    market_status = "주식장 종료"

    if now.date() in us_holidays or now.weekday() >= 5:
        return market_status

    # ny_time은 이미 뉴욕 현지 시간 (DST 자동 반영)이므로
    # 정규장/프리장/애프터장 기준은 항상 동일
    if dt_time(9, 30) <= ny_time <= dt_time(16, 0):
        market_status = "정규장"
    elif dt_time(4, 0) <= ny_time < dt_time(9, 30):
        market_status = "프리장"
    elif dt_time(16, 0) < ny_time <= dt_time(20, 0):
        market_status = "애프터장"

    return market_status


def is_market_open():
    """정규장 여부 — guess_market_session() 기반 (Phase 3-11: 중복 로직 통합)"""
    return guess_market_session() == "정규장"


def adjust_momentum_based_on_market(macd_signal, ma_signal, bb_signal, rsi_signal,
                                     adx_value=None, sentiment_score=0):
    """모멘텀 점수 계산.
    adx_value: ADX 값 (None이면 무시). ADX < 20일 때 MACD/MA 가중치 50% 감소.
    sentiment_score: 뉴스 센티먼트 점수 (±1 범위로 가중치 반영).
    """
    # ADX 기반 가중치 조정
    macd_w = MACD_WEIGHT
    ma_w = MA_WEIGHT
    if adx_value is not None and adx_value < 20:
        macd_w = MACD_WEIGHT * 0.5
        ma_w = MA_WEIGHT * 0.5

    momentum_score = 0.0

    # MACD
    if "매수" in macd_signal:
        momentum_score += macd_w
    elif "매도" in macd_signal:
        momentum_score -= macd_w

    # MA
    if "매수" in ma_signal:
        momentum_score += ma_w
    elif "매도" in ma_signal:
        momentum_score -= ma_w

    # BB
    if "매수" in bb_signal:
        momentum_score += BB_WEIGHT
    elif "매도" in bb_signal:
        momentum_score -= BB_WEIGHT

    # RSI
    if "매수" in rsi_signal:
        momentum_score += RSI_WEIGHT
    elif "매도" in rsi_signal:
        momentum_score -= RSI_WEIGHT

    # 센티먼트 가중치 (±1)
    if sentiment_score != 0:
        momentum_score += max(-1, min(1, sentiment_score))

    # 최종 결정
    if momentum_score >= STRONG_BUY_THRESHOLD:
        return "강력 매수"
    elif momentum_score >= BUY_THRESHOLD:
        return "매수"
    elif momentum_score <= STRONG_SELL_THRESHOLD:
        return "강력 매도"
    elif momentum_score <= SELL_THRESHOLD:
        return "매도"
    else:
        return "관망"
