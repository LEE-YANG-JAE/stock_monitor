import random
from datetime import datetime

import pytz
import yfinance as yf

import config  # config 모듈 임포트
from market_trend_manager import guess_market_session, adjust_momentum_based_on_market

# 각 지표의 신뢰도 점수 (예시)
RSI_confidence = 0.7  # 70% 확률로 유효한 매수 신호
MACD_confidence = 0.8  # 80% 확률로 유효한 매도 신호
MA_confidence = 0.6  # 60% 확률로 유효한 매도 신호
BB_confidence = 0.75  # 75% 확률로 유효한 매수 신호


def update_period_interval(period, interval):
    config.config["current"]["period"] = period
    config.config["current"]["interval"] = interval
    print(f"현재 설정: period={config.config['current']['period']}, interval={config.config['current']['interval']}")
    config.save_config(config.config)


# 서머타임 적용 여부 판단 (미국 뉴욕 기준)
def is_market_open():
    ny_time_zone = pytz.timezone('America/New_York')
    now = datetime.now(ny_time_zone)

    is_dst = bool(now.dst())  # 서머타임 적용 여부

    if is_dst:
        market_open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)  # 서머타임 적용 시: 09:30 AM
        market_close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)  # 서머타임 적용 시: 04:00 PM
    else:
        market_open_time = now.replace(hour=10, minute=30, second=0, microsecond=0)  # 서머타임: 10:30 AM
        market_close_time = now.replace(hour=17, minute=0, second=0, microsecond=0)  # 서머타임: 05:00 PM

    if market_open_time <= now <= market_close_time:
        return True
    else:
        return False


# 이동평균 계산 함수
def calculate_moving_average(historical_data, days=5):
    close_prices = historical_data['Close']
    moving_average = close_prices.iloc[-days:].mean()
    return moving_average


# RSI 계산 함수 (14일 기준)
def calculate_rsi(historical_data, period=14):
    # 종가의 차이값 계산
    delta = historical_data['Close'].diff()

    # 상승분과 하락분을 나누어 계산
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    # NaN이 생길 수 있는 부분에 대한 처리: Rolling mean에서 결측치를 처리
    if gain.isna().any() or loss.isna().any():
        gain.fillna(0, inplace=True)  # 결측치는 0으로 채움
        loss.fillna(0, inplace=True)

    # 만약 gain 또는 loss가 모두 0이면 RSI는 계산할 수 없으므로 예외 처리
    if (gain.sum() == 0) or (loss.sum() == 0):
        return 50  # RSI 기본값(중립)을 반환

    # RSI 계산
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    # 최근 값 반환
    return rsi.iloc[-1]  # 마지막 값만 반환


# MACD 계산 함수
def calculate_macd(historical_data, period=(12, 26, 9)):
    # 12일, 26일 EMA를 사용하여 MACD 계산
    short_ema = historical_data['Close'].ewm(span=period[0], adjust=False).mean()
    long_ema = historical_data['Close'].ewm(span=period[1], adjust=False).mean()

    macd = short_ema - long_ema  # MACD Line
    signal_line = macd.ewm(span=period[2], adjust=False).mean()  # Signal Line
    macd_histogram = macd - signal_line  # MACD Histogram

    return macd, signal_line, macd_histogram


# Bollinger Bands 계산 함수
def calculate_bollinger_bands(historical_data):
    # 20일 이동평균 및 표준편차
    rolling_mean = historical_data['Close'].rolling(window=config.config["current"]["bollinger"]["period"]).mean()
    rolling_std = historical_data['Close'].rolling(window=config.config["current"]["bollinger"]["period"]).std()

    upper_band = rolling_mean + (rolling_std * config.config["current"]["bollinger"]["std_dev_multiplier"]) # Upper Band
    lower_band = rolling_mean - (rolling_std * config.config["current"]["bollinger"]["std_dev_multiplier"]) # Lower Band

    return upper_band, lower_band, rolling_mean


def auto_set_interval_by_period():
    period = config.config["current"].get("period", "1y")  # 혹시 누락되었을 경우 기본 "1y"
    try:
        if isinstance(period, str):
            if period.endswith("d"):
                days = int(period.replace("d", ""))
                if days <= 7:
                    interval = "1m"
                elif days <= 60:
                    interval = "5m"
                elif days <= 365:
                    interval = "1h"
                else:
                    interval = "1d"
            elif period.endswith("mo"):
                interval = "1h"
            elif period.endswith("y"):
                interval = "1d"
            elif period == "max":
                interval = "1d"
            else:
                raise ValueError("Unsupported period format")
        else:
            raise ValueError("Period must be a string")
    except Exception as e:
        print(f"⚠️ Invalid period '{period}' detected: {e}. Reverting to default '1y' + '1d'")
        config.config["current"]["period"] = "1y"
        interval = "1d"

    config.config["current"]["interval"] = interval
    config.save_config(config.config)

# 종목 데이터 가져오기
def fetch_stock_data(ticker):
    try:
        ticker_data = yf.Ticker(ticker)
        auto_set_interval_by_period()
        # 장중일 때와 비장중일 때 데이터 요청 방식 처리
        if is_market_open():
            historical_data = ticker_data.history(period=config.config["current"]["period"],
                                                  interval=config.config["current"]["interval"])
        else:
            historical_data = ticker_data.history(period=config.config["current"]["period"])

        # Fetch company name and current price
        company_name = ticker_data.info.get('shortName', 'Unknown Company')

        session = guess_market_session()
        if session == "정규장":
            current_price = ticker_data.info.get('regularMarketPrice', 0)
        elif session == "프리장":
            current_price = ticker_data.info.get('preMarketPrice', ticker_data.info.get('regularMarketPrice', 0))
        elif session == "애프터장":
            current_price = ticker_data.info.get('postMarketPrice', ticker_data.info.get('regularMarketPrice', 0))
        else:
            current_price = ticker_data.info.get('regularMarketPrice', 0)

        if current_price is None or isinstance(current_price, str):
            current_price = 0  # Default value if current price is unavailable

        # Calculate RSI and moving averages (MA5, MA20)
        rsi = calculate_rsi(historical_data, config.config["current"]["rsi"])  # Assuming you have this function defined
        ma5 = calculate_moving_average(historical_data, days=config.config["current"]["ma_cross"]["short"])
        ma20 = calculate_moving_average(historical_data, days=config.config["current"]["ma_cross"]["long"])

        # Calculate MACD and Bollinger Bands
        macd_period = config.config["current"]["macd"]
        macd, signal_line, macd_histogram = calculate_macd(historical_data, (macd_period["short"], macd_period["long"], macd_period["signal"]))
        upper_band, lower_band, middle_band = calculate_bollinger_bands(historical_data)
        # Calculate MACD Signal: BUY, SELL, or HOLD based on MACD crossover
        macd_simple_signal = 'HOLD'
        if macd.iloc[-1] > signal_line.iloc[-1]:  # MACD crosses above Signal Line (BUY)
            macd_signal = f"BUY ({macd.iloc[-1]:.2f})"
            macd_simple_signal = 'BUY'
        elif macd.iloc[-1] < signal_line.iloc[-1]:  # MACD crosses below Signal Line (SELL)
            macd_signal = f"SELL ({macd.iloc[-1]:.2f})"
            macd_simple_signal = 'SELL'
        else:  # MACD and Signal Line are flat (HOLD)
            macd_signal = f"HOLD ({macd.iloc[-1]:.2f})"

        # Rate calculation (percentage change) and color code for the rate
        rate = ticker_data.info.get('regularMarketChangePercent', 0)
        rate_color = "black"
        if rate > 0:
            rate_color = "green"  # Green if price is increasing
        elif rate < 0:
            rate_color = "red"  # Red if price is decreasing

        ma_s_str = config.config["current"]["ma_cross"]["short"]
        ma_l_str = config.config["current"]["ma_cross"]["long"]
        # Trend Signal based on the comparison of moving averages (MA5 vs MA20)
        if ma5 > ma20:
            trend_signal = f"BUY (MA{ma_s_str}: {ma5:.2f}, MA{ma_l_str}: {ma20:.2f})"
            trend_simple_signal = 'BUY'
        elif ma5 < ma20:
            trend_signal = f"SELL (MA{ma_s_str}: {ma5:.2f}, MA{ma_l_str}: {ma20:.2f})"
            trend_simple_signal = 'SELL'
        else:
            trend_signal = f"HOLD (MA{ma_s_str}: {ma5:.2f}, MA{ma_l_str}: {ma20:.2f})"
            trend_simple_signal = 'HOLD'

        use_rebound_confirmation = config.config["current"]["bollinger"]["use_rebound"]

        bb_signal = "HOLD"
        if use_rebound_confirmation:
            # 반등 검증 활성화된 경우
            # 최근 2~3일 데이터 이용 (바로 반등 여부 판단)
            recent_close = historical_data['Close'].iloc[-3:]  # 최근 3개 종가
            lower_band_recent = lower_band.iloc[-3:]
            touched_lower = (recent_close <= lower_band_recent).any()
            rebounded = recent_close.diff().iloc[-1] > 0  # 마지막에 종가 상승 확인
            if touched_lower and rebounded:
                bb_signal = "BUY (반등확인)"
            elif (recent_close >= upper_band.iloc[-3:]).any() and recent_close.diff().iloc[-1] < 0:
                bb_signal = "SELL (반락확인)"
            else:
                bb_signal = "HOLD"
        else:
            # 기존 방식
            if current_price > upper_band.iloc[-1]:
                bb_signal = "SELL"
            elif current_price < lower_band.iloc[-1]:
                bb_signal = "BUY"

        # RSI Signal
        rsi_signal = "HOLD"
        if rsi > 70:
            rsi_signal = "SELL"
        elif rsi < 30:
            rsi_signal = "BUY"

        momentum_signal = adjust_momentum_based_on_market(macd_simple_signal, trend_simple_signal, bb_signal,
                                                          rsi_signal)

        # Return all data in a tuple (or dictionary if preferred)
        return (
            company_name,  # Company Name
            ticker,  # Ticker Symbol
            f"${current_price:.2f}",  # Current Price
            trend_signal,  # Trend Signal (BUY/SELL/HOLD)
            f"{rsi:.2f}%",  # RSI Signal
            f"{round(rate, 2):.2f}%",  # Rate of Change
            rate_color,  # Rate color (green, red, black)
            macd_signal,  # MACD Signal (BUY/SELL/HOLD)
            bb_signal,  # Bollinger Bands Signal (BUY/SELL/HOLD)
            momentum_signal  # Momentum_Signal (BUY/SELL/HOLD)
        )

    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        return None
