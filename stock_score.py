import yfinance as yf
import pytz
from datetime import datetime
import pandas as pd

# 서머타임 적용 여부 판단 (미국 뉴욕 기준)
def is_market_open():
    ny_time_zone = pytz.timezone('America/New_York')
    now = datetime.now(ny_time_zone)

    is_dst = bool(now.dst())  # 서머타임 적용 여부

    if is_dst:
        market_open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)  # 서머타임: 09:30 AM
        market_close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)  # 서머타임: 04:00 PM
    else:
        market_open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)  # 비서머타임: 09:30 AM
        market_close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)  # 비서머타임: 04:00 PM

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
def calculate_macd(historical_data):
    # 12일, 26일 EMA를 사용하여 MACD 계산
    short_ema = historical_data['Close'].ewm(span=12, adjust=False).mean()
    long_ema = historical_data['Close'].ewm(span=26, adjust=False).mean()

    macd = short_ema - long_ema  # MACD Line
    signal_line = macd.ewm(span=9, adjust=False).mean()  # Signal Line
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

# 종목 데이터 가져오기
def fetch_stock_data(ticker):
    try:
        if is_market_open():
            ticker_data = yf.Ticker(ticker)
            historical_data = ticker_data.history(period="1d", interval="1m")  # 장중 1분 간격 데이터
        else:
            ticker_data = yf.Ticker(ticker)
            historical_data = ticker_data.history(period="3y")  # 장 종료 후에는 3년치

        company_name = ticker_data.info.get('shortName')
        current_price = ticker_data.info.get('regularMarketPrice', None)

        if current_price is None or isinstance(current_price, str):
            current_price = 0  # 기본 값 설정

        # RSI 및 이동 평균 계산
        rsi = calculate_rsi(historical_data)  # RSI 계산 함수 호출
        ma5 = calculate_moving_average(historical_data, days=5)
        ma20 = calculate_moving_average(historical_data, days=20)

        # MACD 및 Bollinger Bands 계산
        macd, signal_line, macd_histogram = calculate_macd(historical_data)
        upper_band, lower_band, middle_band = calculate_bollinger_bands(historical_data)

        # MACD Signal (매수, 보류, 매도) 계산
        if macd.iloc[-1] > signal_line.iloc[-1]:  # Use .iloc for position-based access
            macd_signal = f"BUY ({macd.iloc[-1]:.2f})"  # MACD가 Signal Line 위로 교차 시 매수 신호
        elif macd.iloc[-1] < signal_line.iloc[-1]:
            macd_signal = f"SELL ({macd.iloc[-1]:.2f})"  # MACD가 Signal Line 아래로 교차 시 매도 신호
        else:
            macd_signal = f"HOLD ({macd.iloc[-1]:.2f})"  # MACD와 Signal Line이 교차하지 않으면 보류

        rate = ticker_data.info.get('regularMarketChangePercent', 0)
        rate_color = "black"
        if rate > 0:
            rate_color = "green"
        elif rate < 0:
            rate_color = "red"

        if ma5 > ma20:
            trend_signal = f"BUY (MA5: {ma5:.2f}, MA20: {ma20:.2f})"
        elif ma5 < ma20:
            trend_signal = f"SELL (MA5: {ma5:.2f}, MA20: {ma20:.2f})"
        else:
            trend_signal = f"HOLD (MA5: {ma5:.2f}, MA20: {ma20:.2f})"

        # Return the data with updated MACD signal
        return (
            company_name,
            ticker,
            f"${current_price:.2f}",
            trend_signal,
            f"{rsi:.2f}%",
            f"{round(rate, 2):.2f}%",
            rate_color,
            macd_signal,
            signal_line.iloc[-1],
            macd_histogram.iloc[-1],
            upper_band.iloc[-1],
            lower_band.iloc[-1],
            middle_band.iloc[-1]
        )

    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return None
