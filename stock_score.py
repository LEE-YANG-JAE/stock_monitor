import logging
import time
from collections import namedtuple

import pandas as pd
import yfinance as yf

import config
from fundamental_score import calculate_valuation_score
from market_trend_manager import guess_market_session, is_market_open, adjust_momentum_based_on_market

# Phase 3-2: Removed unused RSI_confidence, MACD_confidence, MA_confidence, BB_confidence

# Phase 8-3: NamedTuple for structured return
StockData = namedtuple('StockData', [
    'company_name', 'ticker', 'price', 'trend_signal', 'rsi_signal',
    'rate', 'rate_color', 'macd_signal', 'bb_signal', 'momentum_signal',
    'value_score', 'value_judgment', 'per_value', 'roe_value', 'week52_pct',
    'volume_ratio', 'atr_pct', 'divergence_signal',
    'liquidity_warning', 'adx_value', 'adx_signal', 'htf_trend'
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


def calculate_atr(historical_data, period=14):
    """ATR (Average True Range) 계산 — 변동성 측정."""
    high = historical_data['High']
    low = historical_data['Low']
    close = historical_data['Close']
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr


def detect_divergence(historical_data, rsi_values=None, lookback=20):
    """RSI 다이버전스 감지 (다중 lookback).
    - 가격 신고가 + RSI 하락 → 하락 다이버전스 (매도 경고)
    - 가격 신저가 + RSI 상승 → 상승 다이버전스 (매수 기회)
    - 가격/RSI 동반 상승 → 정상 상승
    - 가격/RSI 동반 하락 → 정상 하락
    """
    try:
        close = historical_data['Close']

        # RSI 시리즈 계산
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        loss_safe = loss.replace(0, 1e-10)
        rs = gain / loss_safe
        rsi_series = 100 - (100 / (1 + rs))

        # 여러 lookback 구간으로 다이버전스 탐색 (짧은→긴 순서)
        for lb in [10, 20, 30]:
            if len(historical_data) < lb * 2:
                continue

            recent_close = close.iloc[-lb:]
            prev_close = close.iloc[-lb*2:-lb]
            recent_rsi = rsi_series.iloc[-lb:]
            prev_rsi = rsi_series.iloc[-lb*2:-lb]

            if recent_rsi.empty or prev_rsi.empty:
                continue

            recent_high = recent_close.max()
            prev_high = prev_close.max()
            recent_low = recent_close.min()
            prev_low = prev_close.min()
            recent_rsi_high = recent_rsi.max()
            prev_rsi_high = prev_rsi.max()
            recent_rsi_low = recent_rsi.min()
            prev_rsi_low = prev_rsi.min()

            # 하락 다이버전스: 가격 신고가 but RSI 하락
            if recent_high > prev_high and recent_rsi_high < prev_rsi_high - 2:
                return "하락↓"

            # 상승 다이버전스: 가격 신저가 but RSI 상승
            if recent_low < prev_low and recent_rsi_low > prev_rsi_low + 2:
                return "상승↑"

        # 다이버전스 없으면 가격-RSI 방향 일치 여부 표시
        if len(close) >= 10 and len(rsi_series.dropna()) >= 10:
            price_chg = close.iloc[-1] - close.iloc[-10]
            rsi_chg = rsi_series.iloc[-1] - rsi_series.iloc[-10]
            if price_chg > 0 and rsi_chg > 0:
                return "정상↗"
            elif price_chg < 0 and rsi_chg < 0:
                return "정상↘"
            elif price_chg > 0 and rsi_chg < -2:
                return "약화↓"
            elif price_chg < 0 and rsi_chg > 2:
                return "반전↑"
    except Exception:
        pass

    return "-"


def calculate_adx(historical_data, period=14):
    """ADX (Average Directional Index) 계산 — 추세 강도 측정.
    Returns: (adx_value, adx_signal) tuple
    """
    try:
        high = historical_data['High']
        low = historical_data['Low']
        close = historical_data['Close']

        if len(historical_data) < period * 2:
            return None, ""

        # +DM / -DM 계산
        plus_dm = high.diff()
        minus_dm = low.diff().abs() * -1  # 하락폭을 양수로

        # 실제 방향 움직임 결정
        plus_dm_final = pd.Series(0.0, index=high.index)
        minus_dm_final = pd.Series(0.0, index=high.index)

        for i in range(1, len(high)):
            up = high.iloc[i] - high.iloc[i-1]
            down = low.iloc[i-1] - low.iloc[i]
            if up > down and up > 0:
                plus_dm_final.iloc[i] = up
            if down > up and down > 0:
                minus_dm_final.iloc[i] = down

        # ATR
        atr = calculate_atr(historical_data, period)

        # Smoothed +DI / -DI
        plus_di = 100 * (plus_dm_final.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm_final.rolling(window=period).mean() / atr)

        # DX → ADX
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        adx = dx.rolling(window=period).mean()

        last_adx = adx.iloc[-1]
        if pd.isna(last_adx):
            return None, ""

        adx_val = round(float(last_adx), 1)
        if adx_val > 25:
            signal = "강추세"
        elif adx_val < 20:
            signal = "약추세"
        else:
            signal = "보통"

        return adx_val, signal
    except Exception as e:
        logging.warning(f"[ADX] Calculation error: {e}")
        return None, ""


def fetch_higher_timeframe_trend(ticker_symbol, current_interval):
    """상위 타임프레임 MA 교차 방향을 확인하여 추세 반환.
    Returns: 'UP', 'DOWN', '' (빈 문자열 = 판단 불가)
    """
    # 타임프레임 매핑: 현재 인터벌 → 상위 TF
    htf_map = {
        "1m": ("1d", "3mo"), "5m": ("1d", "3mo"), "15m": ("1d", "3mo"),
        "30m": ("1d", "3mo"), "1h": ("1d", "6mo"),
        "1d": ("1wk", "2y"), "5d": ("1wk", "2y"),
    }
    htf_interval, htf_period = htf_map.get(current_interval, (None, None))
    if not htf_interval:
        return ""

    try:
        data = yf.Ticker(ticker_symbol).history(period=htf_period, interval=htf_interval)
        if data.empty or len(data) < 20:
            return ""

        ma_short = data['Close'].rolling(window=5).mean()
        ma_long = data['Close'].rolling(window=20).mean()

        if pd.isna(ma_short.iloc[-1]) or pd.isna(ma_long.iloc[-1]):
            return ""

        if ma_short.iloc[-1] > ma_long.iloc[-1]:
            return "UP"
        else:
            return "DOWN"
    except Exception as e:
        logging.warning(f"[HTF] Error for {ticker_symbol}: {e}")
        return ""


# Bollinger Bands 계산 함수
def calculate_bollinger_bands(historical_data):
    bb_period = config.config["current"]["bollinger"]["period"]
    std_mult = config.config["current"]["bollinger"]["std_dev_multiplier"]
    rolling_mean = historical_data['Close'].rolling(window=bb_period).mean()
    rolling_std = historical_data['Close'].rolling(window=bb_period).std()
    upper_band = rolling_mean + (rolling_std * std_mult)
    lower_band = rolling_mean - (rolling_std * std_mult)
    return upper_band, lower_band, rolling_mean


def _days_between(start_str, end_str):
    """두 날짜 문자열 사이의 일수 계산."""
    from datetime import datetime
    try:
        s = datetime.strptime(start_str, '%Y-%m-%d')
        e = datetime.strptime(end_str, '%Y-%m-%d')
        return (e - s).days
    except (ValueError, TypeError):
        return 365


def auto_set_interval_by_period():
    # custom_mode: start/end 날짜에서 일수 계산하여 interval 결정
    if config.config["current"].get("custom_mode"):
        start = config.config["current"].get("start_date", "")
        end = config.config["current"].get("end_date", "")
        days = _days_between(start, end)
        if days <= 7:
            interval = "1m"
        elif days <= 60:
            interval = "5m"
        elif days <= 365:
            interval = "1h"
        else:
            interval = "1d"
        config.config["current"]["interval"] = interval
        return

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

        # custom_mode: start/end 날짜 직접 사용
        if config.config["current"].get("custom_mode"):
            start_date = config.config["current"].get("start_date")
            end_date = config.config["current"].get("end_date")
            historical_data = ticker_data.history(
                start=start_date,
                end=end_date,
                interval=config.config["current"]["interval"]
            )
        elif is_market_open():
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

        # 거래량 비율 (현재 거래량 / 평균 거래량)
        current_volume = ticker_info.get('volume', None)
        avg_volume = ticker_info.get('averageVolume', None)
        if current_volume and avg_volume and avg_volume > 0:
            volume_ratio = current_volume / avg_volume
        else:
            volume_ratio = None

        # 유동성 경고
        liquidity_warning = ""
        if volume_ratio is not None and volume_ratio < 0.5:
            liquidity_warning = "저유동"

        # ATR (변동성) 계산 — 현재가 대비 % 표시
        try:
            atr = calculate_atr(historical_data)
            last_atr = atr.iloc[-1]
            atr_pct = (last_atr / current_price * 100) if current_price > 0 else None
        except Exception:
            atr_pct = None

        # RSI 다이버전스 감지
        divergence = detect_divergence(historical_data, rsi)

        # ADX 지표
        adx_period = config.config["current"].get("adx_period", 14)
        adx_value, adx_signal = calculate_adx(historical_data, period=adx_period)

        # ADX 필터: ADX < 20일 때 모멘텀 신호 다운그레이드
        if config.config["current"].get("adx_filter_enabled", False):
            if adx_value is not None and adx_value < 20:
                momentum_signal = adjust_momentum_based_on_market(
                    macd_simple_signal, trend_simple_signal, bb_signal, rsi_signal,
                    adx_value=adx_value
                )

        # 멀티 타임프레임 확인
        htf_trend = ""
        if config.config["current"].get("multi_timeframe_enabled", False):
            current_interval = config.config["current"]["interval"]
            htf_trend = fetch_higher_timeframe_trend(ticker, current_interval)
            # 상위 TF와 불일치 시 모멘텀 다운그레이드
            if htf_trend:
                if ("매수" in momentum_signal and htf_trend == "DOWN") or \
                   ("매도" in momentum_signal and htf_trend == "UP"):
                    momentum_signal = "관망"

        # 펀더멘털 지표 계산 (이미 가져온 ticker_info 재활용)
        fund = calculate_valuation_score(ticker_info)

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
            momentum_signal=momentum_signal,
            value_score=f"{fund.score:+d}/{fund.total_criteria}" if fund.total_criteria > 0 else "N/A",
            value_judgment=fund.judgment,
            per_value=fund.per,
            roe_value=fund.roe,
            week52_pct=fund.fifty_two_week_pct,
            volume_ratio=volume_ratio,
            atr_pct=atr_pct,
            divergence_signal=divergence,
            liquidity_warning=liquidity_warning,
            adx_value=adx_value,
            adx_signal=adx_signal,
            htf_trend=htf_trend
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
