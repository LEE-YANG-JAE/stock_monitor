import time
from datetime import datetime, time as dt_time

import holidays
import pytz
import yfinance as yf


class MarketTrendManager:
    def __init__(self, index_ticker="SPY", refresh_interval=600):
        self.index_ticker = index_ticker
        self.refresh_interval = refresh_interval  # seconds (10 minutes)
        self.last_refresh_time = 0
        self.market_trend = "Unknown"
        self.market_source = ""
        self.market_session = ""
        self.momentum = ""

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
        except Exception as e:
            print(f"Error detecting market trend: {e}")
            return "Unknown"

    def get_market_trend(self):
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
            market_source = ""

            if exchange in ["NasdaqGS", "Nasdaq"]:
                market_source = "QQQ"
            if sector in ["Technology", "Semiconductors", "Internet"]:
                market_source = "QQQ"
            self.market_source = market_source
            return market_source
        except Exception as e:
            print(f"Error guessing market source for {ticker}: {e}")
            self.market_source = "SPY"
            return "SPY"


def guess_market_session():
    ny_time_zone = pytz.timezone('America/New_York')
    now = datetime.now(ny_time_zone)
    ny_time = now.time()

    us_holidays = holidays.country_holidays('US')
    market_status = "주식장 종료"

    if now.date() in us_holidays or now.weekday() >= 5:  # 토요일(5), 일요일(6):
        return market_status

    is_dst = bool(now.dst())  # 서머타임 적용 여부
    if is_dst:
        if dt_time(9, 30) <= ny_time <= dt_time(16, 0):
            market_status = "정규장"
        elif dt_time(4, 0) <= ny_time < dt_time(9, 30):
            market_status = "프리장"
        elif dt_time(16, 0) < ny_time <= dt_time(20, 0):
            market_status = "애프터장"
    else:
        if dt_time(10, 30) <= ny_time <= dt_time(17, 0):
            market_status = "정규장"
        elif dt_time(5, 0) <= ny_time < dt_time(10, 30):
            market_status = "프리장"
        elif dt_time(17, 0) < ny_time <= dt_time(21, 0):
            market_status = "애프터장"

    return market_status


def adjust_momentum_based_on_market(macd_signal, ma_signal, bb_signal, rsi_signal):
    momentum_score = 0

    # MACD
    if macd_signal == "BUY":
        momentum_score += 2
    elif macd_signal == "SELL":
        momentum_score -= 2

    # MA
    if ma_signal == "BUY":
        momentum_score += 1
    elif ma_signal == "SELL":
        momentum_score -= 1

    # BB
    if bb_signal == "BUY":
        momentum_score += 1
    elif bb_signal == "SELL":
        momentum_score -= 1

    # RSI
    if rsi_signal == "BUY":
        momentum_score += 1
    elif rsi_signal == "SELL":
        momentum_score -= 1

    # 최종 결정
    if momentum_score >= 4:
        final_momentum = "STRONG BUY"
    elif momentum_score >= 2:
        final_momentum = "BUY"
    elif momentum_score <= -4:
        final_momentum = "STRONG SELL"
    elif momentum_score <= -2:
        final_momentum = "SELL"
    else:
        final_momentum = "HOLD"

    return final_momentum
