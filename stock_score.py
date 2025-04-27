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
    config.config["current_period"] = period
    config.config["current_interval"] = interval
    print(f"현재 설정: period={config.config['current_period']}, interval={config.config['current_interval']}")
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
def calculate_bollinger_bands(historical_data, window=20):
    # 20일 이동평균 및 표준편차
    rolling_mean = historical_data['Close'].rolling(window=window).mean()
    rolling_std = historical_data['Close'].rolling(window=window).std()

    upper_band = rolling_mean + (rolling_std * 2)  # Upper Band
    lower_band = rolling_mean - (rolling_std * 2)  # Lower Band

    return upper_band, lower_band, rolling_mean


# 각 지표가 주는 신뢰도를 기반으로 확률적으로 결정
def generate_momentum_signal(rsi_signal, macd_signal, ma_signal, bb_signal):
    # 각 신호가 유효한 경우, 확률적으로 결정
    momentum_score = 0

    if rsi_signal == "BUY" and random.random() < RSI_confidence:
        momentum_score += 1
    elif rsi_signal == "SELL" and random.random() < (1 - RSI_confidence):
        momentum_score -= 1

    if macd_signal == "BUY" and random.random() < MACD_confidence:
        momentum_score += 1
    elif macd_signal == "SELL" and random.random() < (1 - MACD_confidence):
        momentum_score -= 1

    if ma_signal == "BUY" and random.random() < MA_confidence:
        momentum_score += 1
    elif ma_signal == "SELL" and random.random() < (1 - MA_confidence):
        momentum_score -= 1

    if bb_signal == "BUY" and random.random() < BB_confidence:
        momentum_score += 1
    elif bb_signal == "SELL" and random.random() < (1 - BB_confidence):
        momentum_score -= 1

    # 모멘텀 신호 결정
    if momentum_score > 1:
        return "BUY"
    elif momentum_score < -1:
        return "SELL"
    else:
        return "HOLD"


# 종목 데이터 가져오기
def fetch_stock_data(ticker):
    try:
        ticker_data = yf.Ticker(ticker)
        # 장중일 때와 비장중일 때 데이터 요청 방식 처리
        if is_market_open():
            historical_data = ticker_data.history(period=config.config["current_period"],
                                                  interval=config.config["current_interval"])
        else:
            historical_data = ticker_data.history(period=config.config["current_period"])

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
        rsi = calculate_rsi(historical_data, config.config["current_rsi"])  # Assuming you have this function defined
        ma5 = calculate_moving_average(historical_data, days=5)
        ma20 = calculate_moving_average(historical_data, days=20)

        # Calculate MACD and Bollinger Bands
        macd, signal_line, macd_histogram = calculate_macd(historical_data, config.config["current_macd"])
        upper_band, lower_band, middle_band = calculate_bollinger_bands(historical_data,
                                                                        config.config["current_bollinger"])

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

        # Trend Signal based on the comparison of moving averages (MA5 vs MA20)
        if ma5 > ma20:
            trend_signal = f"BUY (MA5: {ma5:.2f}, MA20: {ma20:.2f})"
            trend_simple_signal = 'BUY'
        elif ma5 < ma20:
            trend_signal = f"SELL (MA5: {ma5:.2f}, MA20: {ma20:.2f})"
            trend_simple_signal = 'SELL'
        else:
            trend_signal = f"HOLD (MA5: {ma5:.2f}, MA20: {ma20:.2f})"
            trend_simple_signal = 'HOLD'

        # Bollinger Bands Signal: BUY, SELL, or HOLD based on the price position relative to the bands
        bb_signal = "HOLD"
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
