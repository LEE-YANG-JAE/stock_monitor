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
    'liquidity_warning', 'adx_value', 'adx_signal', 'htf_trend',
    'vwap_signal', 'obv_signal', 'stoch_signal',
    'earnings_dday', 'short_float', 'insider_held',
    'ichimoku_signal', 'pattern_signal'
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


def calculate_vwap(historical_data):
    """VWAP (Volume Weighted Average Price) 계산.
    Returns: (vwap_value, signal_text)
    """
    try:
        if 'Volume' not in historical_data.columns or historical_data['Volume'].sum() == 0:
            return None, ""
        typical_price = (historical_data['High'] + historical_data['Low'] + historical_data['Close']) / 3
        cum_tp_vol = (typical_price * historical_data['Volume']).cumsum()
        cum_vol = historical_data['Volume'].cumsum()
        vwap = cum_tp_vol / cum_vol
        last_vwap = vwap.iloc[-1]
        last_close = historical_data['Close'].iloc[-1]
        if pd.isna(last_vwap) or last_vwap == 0:
            return None, ""
        pct_diff = (last_close - last_vwap) / last_vwap * 100
        if pct_diff > 1:
            signal = f"강세 +{pct_diff:.1f}%"
        elif pct_diff < -1:
            signal = f"약세 {pct_diff:.1f}%"
        else:
            signal = f"중립 {pct_diff:+.1f}%"
        return round(float(last_vwap), 2), signal
    except Exception as e:
        logging.warning(f"[VWAP] Calculation error: {e}")
        return None, ""


def calculate_obv(historical_data):
    """OBV (On-Balance Volume) 계산 및 추세 판단.
    Returns: signal_text — OBV 10일MA 대비 변화율(%) + 추세 신호
    """
    try:
        if 'Volume' not in historical_data.columns or len(historical_data) < 10:
            return ""
        close = historical_data['Close']
        volume = historical_data['Volume']
        obv = pd.Series(0.0, index=close.index)
        for i in range(1, len(close)):
            if close.iloc[i] > close.iloc[i - 1]:
                obv.iloc[i] = obv.iloc[i - 1] + volume.iloc[i]
            elif close.iloc[i] < close.iloc[i - 1]:
                obv.iloc[i] = obv.iloc[i - 1] - volume.iloc[i]
            else:
                obv.iloc[i] = obv.iloc[i - 1]
        # OBV 10일 이동평균 추세 비교
        obv_ma = obv.rolling(10).mean()
        if pd.isna(obv_ma.iloc[-1]):
            return ""
        # OBV vs MA 변화율
        obv_last = obv.iloc[-1]
        ma_last = obv_ma.iloc[-1]
        if abs(ma_last) > 0:
            obv_pct = (obv_last - ma_last) / abs(ma_last) * 100
        else:
            obv_pct = 0.0
        price_up = close.iloc[-1] > close.iloc[-10]
        obv_up = obv_last > ma_last
        if price_up and obv_up:
            label = "확인↑"
        elif price_up and not obv_up:
            label = "괴리↓"
        elif not price_up and obv_up:
            label = "반전↑"
        else:
            label = "확인↓"
        return f"{obv_pct:+.1f}% {label}"
    except Exception as e:
        logging.warning(f"[OBV] Calculation error: {e}")
        return ""


def calculate_stochastic(historical_data, k_period=14, d_period=3):
    """Stochastic Oscillator (%K, %D) 계산.
    Returns: (k_value, d_value, signal_text)
    """
    try:
        if len(historical_data) < k_period + d_period:
            return None, None, ""
        low_min = historical_data['Low'].rolling(window=k_period).min()
        high_max = historical_data['High'].rolling(window=k_period).max()
        denom = high_max - low_min
        denom = denom.replace(0, 1e-10)
        k = (historical_data['Close'] - low_min) / denom * 100
        d = k.rolling(window=d_period).mean()
        last_k = k.iloc[-1]
        last_d = d.iloc[-1]
        if pd.isna(last_k) or pd.isna(last_d):
            return None, None, ""
        k_val = round(float(last_k), 1)
        d_val = round(float(last_d), 1)
        kd_text = f"{k_val:.0f}/{d_val:.0f}"
        if k_val > 80 and k_val < d_val:
            signal = f"{kd_text} 과매수↓"
        elif k_val < 20 and k_val > d_val:
            signal = f"{kd_text} 과매도↑"
        elif k_val > 80:
            signal = f"{kd_text} 과매수"
        elif k_val < 20:
            signal = f"{kd_text} 과매도"
        else:
            signal = kd_text
        return k_val, d_val, signal
    except Exception as e:
        logging.warning(f"[STOCH] Calculation error: {e}")
        return None, None, ""


def calculate_ichimoku(historical_data, tenkan=9, kijun=26, senkou_b=52):
    """Ichimoku Cloud (일목균형표) 계산.
    Returns: dict with keys: tenkan_sen, kijun_sen, senkou_a, senkou_b, chikou, signal
    """
    try:
        high = historical_data['High']
        low = historical_data['Low']
        close = historical_data['Close']

        if len(historical_data) < senkou_b + kijun:
            return None

        # Tenkan-sen (전환선): (최고+최저)/2 over tenkan period
        tenkan_sen = (high.rolling(window=tenkan).max() + low.rolling(window=tenkan).min()) / 2
        # Kijun-sen (기준선): (최고+최저)/2 over kijun period
        kijun_sen = (high.rolling(window=kijun).max() + low.rolling(window=kijun).min()) / 2
        # Senkou Span A (선행스팬A): (tenkan+kijun)/2, shifted forward kijun periods
        senkou_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun)
        # Senkou Span B (선행스팬B): (highest+lowest)/2 over senkou_b, shifted forward kijun
        senkou_b_line = ((high.rolling(window=senkou_b).max() + low.rolling(window=senkou_b).min()) / 2).shift(kijun)
        # Chikou Span (후행스팬): Close shifted back kijun periods
        chikou = close.shift(-kijun)

        # Signal generation
        last_close = close.iloc[-1]
        last_tenkan = tenkan_sen.iloc[-1]
        last_kijun = kijun_sen.iloc[-1]
        # Use current (non-shifted) cloud for signal
        cur_senkou_a = senkou_a.iloc[-1] if not pd.isna(senkou_a.iloc[-1]) else 0
        cur_senkou_b = senkou_b_line.iloc[-1] if not pd.isna(senkou_b_line.iloc[-1]) else 0
        cloud_top = max(cur_senkou_a, cur_senkou_b)
        cloud_bottom = min(cur_senkou_a, cur_senkou_b)

        if pd.isna(last_tenkan) or pd.isna(last_kijun):
            signal = ""
        elif last_close > cloud_top and last_tenkan > last_kijun:
            signal = "강세↑"
        elif last_close < cloud_bottom and last_tenkan < last_kijun:
            signal = "약세↓"
        elif last_close > cloud_top:
            signal = "구름위"
        elif last_close < cloud_bottom:
            signal = "구름아래"
        else:
            signal = "구름내"

        return {
            'tenkan_sen': tenkan_sen,
            'kijun_sen': kijun_sen,
            'senkou_a': senkou_a,
            'senkou_b': senkou_b_line,
            'chikou': chikou,
            'signal': signal,
        }
    except Exception as e:
        logging.warning(f"[ICHIMOKU] Calculation error: {e}")
        return None


def fetch_earnings_dday(ticker_data):
    """다음 실적 발표일까지 남은 일수 조회.
    Returns: 'D-N' 형태 문자열 또는 ''
    """
    try:
        from datetime import datetime
        cal = ticker_data.calendar
        if cal is None:
            return ""
        # yfinance calendar: dict or DataFrame
        if isinstance(cal, dict):
            earn_date = cal.get('Earnings Date', [None])[0] if 'Earnings Date' in cal else None
        elif hasattr(cal, 'columns'):
            if 'Earnings Date' in cal.columns:
                earn_date = cal['Earnings Date'].iloc[0]
            elif len(cal) > 0:
                earn_date = cal.iloc[0, 0] if cal.shape[1] > 0 else None
            else:
                earn_date = None
        else:
            return ""
        if earn_date is None:
            return ""
        if hasattr(earn_date, 'date'):
            earn_date = earn_date.date()
        elif isinstance(earn_date, str):
            earn_date = datetime.strptime(earn_date, '%Y-%m-%d').date()
        today = datetime.now().date()
        days_left = (earn_date - today).days
        if days_left < 0:
            return ""
        return f"D-{days_left}"
    except Exception:
        return ""


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

        # SQLite 캐시 사용 시도
        try:
            from data_cache import get_cached_history as _cached_hist
            _use_cache = True
        except ImportError:
            _use_cache = False

        # custom_mode: start/end 날짜 직접 사용
        if config.config["current"].get("custom_mode"):
            start_date = config.config["current"].get("start_date")
            end_date = config.config["current"].get("end_date")
            if _use_cache:
                historical_data = _cached_hist(ticker, start=start_date, end=end_date,
                                               interval=config.config["current"]["interval"])
            else:
                historical_data = ticker_data.history(
                    start=start_date, end=end_date,
                    interval=config.config["current"]["interval"]
                )
        elif is_market_open():
            if _use_cache:
                historical_data = _cached_hist(ticker,
                                               period=config.config["current"]["period"],
                                               interval=config.config["current"]["interval"])
            else:
                historical_data = ticker_data.history(
                    period=config.config["current"]["period"],
                    interval=config.config["current"]["interval"]
                )
        else:
            if _use_cache:
                historical_data = _cached_hist(ticker, period=config.config["current"]["period"])
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

        # VWAP
        _, vwap_signal = calculate_vwap(historical_data)

        # OBV
        obv_signal = calculate_obv(historical_data)

        # Stochastic Oscillator
        _, _, stoch_signal = calculate_stochastic(historical_data)

        # 실적 발표일
        earnings_dday = fetch_earnings_dday(ticker_data)

        # 공매도 비율 + 내부자 보유
        short_float = ticker_info.get('shortPercentOfFloat', None)
        insider_held = ticker_info.get('heldPercentInsiders', None)

        # 일목균형표
        ichimoku_result = calculate_ichimoku(historical_data)
        ichimoku_signal = ichimoku_result['signal'] if ichimoku_result else ""

        # 차트 패턴 인식
        try:
            from pattern_recognition import get_pattern_summary
            pattern_signal = get_pattern_summary(historical_data)
        except ImportError:
            pattern_signal = "-"
        except Exception:
            pattern_signal = "-"

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
            htf_trend=htf_trend,
            vwap_signal=vwap_signal,
            obv_signal=obv_signal,
            stoch_signal=stoch_signal,
            earnings_dday=earnings_dday,
            short_float=short_float,
            insider_held=insider_held,
            ichimoku_signal=ichimoku_signal,
            pattern_signal=pattern_signal
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
