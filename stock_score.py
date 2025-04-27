import yfinance as yf
import pytz
from datetime import datetime

# 서머타임 적용 여부 판단 (미국 뉴욕 기준)
def is_dst_in_ny():
    ny = pytz.timezone('America/New_York')
    now = datetime.now(ny)
    return bool(now.dst())

# 이동평균 계산 함수
def calculate_moving_average(historical_data, days=5):
    close_prices = historical_data['Close']  # 종가 리스트
    moving_average = close_prices.iloc[-days:].mean()  # 최근 'days' 일의 평균
    return moving_average

# RSI 계산 함수 (14일 기준)
def calculate_rsi(historical_data, period=14):
    delta = historical_data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]  # 가장 최근 RSI 값 반환

# 종목 데이터 가져오기
def fetch_stock_data(ticker):
    try:
        # yfinance를 사용하여 종목 정보를 가져옵니다.
        ticker_data = yf.Ticker(ticker)
        ticker_info = ticker_data.info
        historical_data = ticker_data.history(period="3y")  # 3년치 데이터 가져오기

        company_name = ticker_info.get('shortName')  # 영문 종목명

        if not company_name:
            print(f"Company name not found for ticker: {ticker}")
            return None

        # 주식 정보 얻기
        current_price = ticker_info.get('currentPrice', None)

        # Handle None or missing currentPrice
        if current_price is None or isinstance(current_price, str):
            current_price = 0  # Set default value if None or invalid
        else:
            current_price = float(current_price)  # Ensure it is a float

        # RSI 및 이동평균 계산
        rsi = calculate_rsi(historical_data)
        ma5 = calculate_moving_average(historical_data, days=5)
        ma20 = calculate_moving_average(historical_data, days=20)

        # 수익률 및 색상 계산
        rate = ticker_info.get('regularMarketChangePercent', 0)  # 주식 변화율
        rate_color = "black"  # 수익률에 따른 색상 설정 (black 기본)

        if isinstance(rate, str):
            rate = rate.strip('%')  # '%' 기호를 제거
            try:
                rate = float(rate)  # 숫자로 변환
            except ValueError:
                rate = 0  # 만약 숫자가 아니면 0으로 설정

        if rate > 0:
            rate_color = "green"  # 상승
        elif rate < 0:
            rate_color = "red"  # 하락

        # 추세 신호 계산
        if ma5 > ma20:
            trend_signal = f"BUY (MA5: {ma5:.2f}, MA20: {ma20:.2f})"
        elif ma5 < ma20:
            trend_signal = f"SELL (MA5: {ma5:.2f}, MA20: {ma20:.2f})"
        else:
            trend_signal = f"HOLD (MA5: {ma5:.2f}, MA20: {ma20:.2f})"

        # 기호 추가
        current_price = f"${current_price:.2f}"  # 현재가는 달러 기호 추가
        rsi_display = f"{rsi:.2f}%"  # RSI를 %로 표시
        score = f"{round(rate, 2):.2f}%"  # 수익률을 퍼센트로 표시

        # 모든 데이터 반환
        return company_name, ticker, current_price, trend_signal, rsi_display, score, rate_color

    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return None
