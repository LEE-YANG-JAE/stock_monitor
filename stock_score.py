import yfinance as yf
import pytz
from datetime import datetime
import pandas as pd  # pandas 모듈 import 추가

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

# 종목 데이터 가져오기
def fetch_stock_data(ticker):
    try:
        # 장중 여부 확인
        if is_market_open():
            ticker_data = yf.Ticker(ticker)
            historical_data = ticker_data.history(period="1d", interval="1m")  # 장중 1분 간격 데이터
        else:
            # 장 종료 후: 마지막 거래일 데이터를 1분 간격으로 가져오기
            ticker_data = yf.Ticker(ticker)
            historical_data = ticker_data.history(period="1d", interval="1m")  # 장 종료 후에도 1분 간격 데이터 요청

        company_name = ticker_data.info.get('shortName')
        current_price = ticker_data.info.get('regularMarketPrice', None)

        if current_price is None or isinstance(current_price, str):
            current_price = 0  # 기본 값 설정

        # RSI 및 이동 평균 계산
        rsi = calculate_rsi(historical_data)  # RSI 계산 함수 호출
        ma5 = calculate_moving_average(historical_data, days=5)
        ma20 = calculate_moving_average(historical_data, days=20)

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

        return company_name, ticker, f"${current_price:.2f}", trend_signal, f"{rsi:.2f}%", f"{round(rate, 2):.2f}%", rate_color

    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return None

