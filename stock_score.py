import logging
import time
from collections import namedtuple

import yfinance as yf

import config
from market_trend_manager import guess_market_session, is_market_open, adjust_momentum_based_on_market

# Phase 3-2: Removed unused RSI_confidence, MACD_confidence, MA_confidence, BB_confidence

# Phase 8-3: NamedTuple for structured return
StockData = namedtuple('StockData', [
    'company_name', 'ticker', 'price', 'trend_signal', 'rsi_signal',
    'rate', 'rate_color', 'macd_signal', 'bb_signal', 'momentum_signal'
])

# Phase 7-2: API retry with exponential backoff
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1  # seconds


def _retry_api_call(func, *args, **kwargs):
    """Retry API call with exponential backoff for network errors."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logging.warning(f"[RETRY] Attempt {attempt + 1} failed: {e}, retrying in {delay}s")
                time.sleep(delay)
    raise last_error


def update_period_interval(period, interval):
    config.config["current"]["period"] = period
    config.config["current"]["interval"] = interval
    logging.info(f"[CONFIG] Updated: period={period}, interval={interval}")
    config.save_config(config.get_config())


# 이동평균 계산 함수
def calculate_moving_average(historical_data, days=5):
    close_prices = historical_data['Close']
    moving_average = close_prices.iloc[-days:].mean()
    return moving_average


# RSI 계산 함수
def calculate_rsi(historical_data, period=14):
    delta = historical_data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    if gain.isna().any() or loss.isna().any():
        gain = gain.fillna(0)
        loss = loss.fillna(0)

    if gain.sum() == 0 or loss.sum() == 0:
        return 50

    # Phase 3-1: RSI division by zero guard
    last_loss = loss.iloc[-1]
    if last_loss == 0:
        return 100.0

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


# MACD 계산 함수
def calculate_macd(historical_data, period=(12, 26, 9)):
    short_ema = historical_data['Close'].ewm(span=period[0], adjust=False).mean()
    long_ema = historical_data['Close'].ewm(span=period[1], adjust=False).mean()
    macd = short_ema - long_ema
    signal_line = macd.ewm(span=period[2], adjust=False).mean()
    macd_histogram = macd - signal_line
    return macd, signal_line, macd_histogram


# Bollinger Bands 계산 함수
def calculate_bollinger_bands(historical_data):
    bb_period = config.config["current"]["bollinger"]["period"]
    std_mult = config.config["current"]["bollinger"]["std_dev_multiplier"]
    rolling_mean = historical_data['Close'].rolling(window=bb_period).mean()
    rolling_std = historical_data['Close'].rolling(window=bb_period).std()
    upper_band = rolling_mean + (rolling_std * std_mult)
    lower_band = rolling_mean - (rolling_std * std_mult)
    return upper_band, lower_band, rolling_mean


def auto_set_interval_by_period():
    period = config.config["current"].get("period", "1y")
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
        logging.warning(f"[CONFIG] Invalid period '{period}': {e}. Reverting to '1y' + '1d'")
        config.config["current"]["period"] = "1y"
        interval = "1d"

    config.config["current"]["interval"] = interval
    config.save_config(config.get_config())


# 종목 데이터 가져오기
def fetch_stock_data(ticker):
    try:
        ticker_data = yf.Ticker(ticker)
        auto_set_interval_by_period()

        # Phase 3-11: Use unified is_market_open()
        if is_market_open():
            historical_data = ticker_data.history(
                period=config.config["current"]["period"],
                interval=config.config["current"]["interval"]
            )
        else:
            historical_data = ticker_data.history(period=config.config["current"]["period"])

        if historical_data.empty:
            logging.warning(f"[FETCH] No historical data for {ticker}")
            return None

        # Phase 7-1: Single .info call, reuse result
        ticker_info = ticker_data.info
        company_name = ticker_info.get('shortName', 'Unknown Company')

        session = guess_market_session()
        if session == "정규장":
            current_price = ticker_info.get('regularMarketPrice', 0)
        elif session == "프리장":
            current_price = ticker_info.get('preMarketPrice', ticker_info.get('regularMarketPrice', 0))
        elif session == "애프터장":
            current_price = ticker_info.get('postMarketPrice', ticker_info.get('regularMarketPrice', 0))
        else:
            current_price = ticker_info.get('regularMarketPrice', 0)

        if current_price is None or isinstance(current_price, str):
            current_price = 0

        rsi = calculate_rsi(historical_data, config.config["current"]["rsi"]['period'])
        ma5 = calculate_moving_average(historical_data, days=config.config["current"]["ma_cross"]["short"])
        ma20 = calculate_moving_average(historical_data, days=config.config["current"]["ma_cross"]["long"])

        macd_period = config.config["current"]["macd"]
        macd, signal_line, macd_histogram = calculate_macd(
            historical_data, (macd_period["short"], macd_period["long"], macd_period["signal"])
        )
        upper_band, lower_band, middle_band = calculate_bollinger_bands(historical_data)

        macd_simple_signal = '관망'
        if macd.iloc[-1] > signal_line.iloc[-1]:
            macd_signal = f"매수 ({macd.iloc[-1]:.2f})"
            macd_simple_signal = '매수'
        elif macd.iloc[-1] < signal_line.iloc[-1]:
            macd_signal = f"매도 ({macd.iloc[-1]:.2f})"
            macd_simple_signal = '매도'
        else:
            macd_signal = f"관망 ({macd.iloc[-1]:.2f})"

        rate = ticker_info.get('regularMarketChangePercent', 0)
        if rate is None:
            rate = 0
        rate_color = "black"
        if rate > 0:
            rate_color = "green"
        elif rate < 0:
            rate_color = "red"

        ma_s_str = config.config["current"]["ma_cross"]["short"]
        ma_l_str = config.config["current"]["ma_cross"]["long"]
        if ma5 > ma20:
            trend_signal = f"매수 (MA{ma_s_str}: {ma5:.2f}, MA{ma_l_str}: {ma20:.2f})"
            trend_simple_signal = '매수'
        elif ma5 < ma20:
            trend_signal = f"매도 (MA{ma_s_str}: {ma5:.2f}, MA{ma_l_str}: {ma20:.2f})"
            trend_simple_signal = '매도'
        else:
            trend_signal = f"관망 (MA{ma_s_str}: {ma5:.2f}, MA{ma_l_str}: {ma20:.2f})"
            trend_simple_signal = '관망'

        use_rebound_confirmation = config.config["current"]["bollinger"]["use_rebound"]

        bb_signal = "관망"
        if use_rebound_confirmation:
            # Phase 3-4: Fixed Bollinger rebound logic — same-timeframe check
            if len(historical_data) >= 3:
                recent_close = historical_data['Close'].iloc[-3:]
                lower_band_recent = lower_band.iloc[-3:]
                upper_band_recent = upper_band.iloc[-3:]
                touched_lower = (recent_close <= lower_band_recent).any()
                touched_upper = (recent_close >= upper_band_recent).any()
                rebounded = recent_close.iloc[-1] > recent_close.iloc[-2]
                declined = recent_close.iloc[-1] < recent_close.iloc[-2]
                if touched_lower and rebounded:
                    bb_signal = "매수 (반등확인)"
                elif touched_upper and declined:
                    bb_signal = "매도 (반락확인)"
        else:
            if current_price > upper_band.iloc[-1]:
                bb_signal = "매도"
            elif current_price < lower_band.iloc[-1]:
                bb_signal = "매수"

        rsi_signal = "관망"
        if rsi > config.config["current"]["rsi"]["upper"]:
            rsi_signal = "매도"
        elif rsi < config.config["current"]["rsi"]["lower"]:
            rsi_signal = "매수"

        momentum_signal = adjust_momentum_based_on_market(
            macd_simple_signal, trend_simple_signal, bb_signal, rsi_signal
        )

        # Phase 8-3: Return NamedTuple instead of plain tuple
        return StockData(
            company_name=company_name,
            ticker=ticker,
            price=f"${current_price:.2f}",
            trend_signal=trend_signal,
            rsi_signal=f"{rsi:.2f}%",
            rate=f"{round(rate, 2):.2f}%",
            rate_color=rate_color,
            macd_signal=macd_signal,
            bb_signal=bb_signal,
            momentum_signal=momentum_signal
        )

    except (ConnectionError, TimeoutError) as e:
        logging.error(f"[FETCH] Network error for {ticker}: {e}")
        return None
    except KeyError as e:
        logging.error(f"[FETCH] Missing data key for {ticker}: {e}")
        return None
    except Exception as e:
        logging.error(f"[FETCH] Error fetching data for {ticker}: {e}")
        return None
