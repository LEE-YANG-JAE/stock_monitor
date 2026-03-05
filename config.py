import json
import logging
import os
import tempfile
import threading
import time

CONFIG_FILE = 'config.json'
WATCHLIST_FILE = "watchlist.json"
DEFAULT_WATCHLIST = ["SPY", "QQQ"]

# Thread lock for config file access
config_lock = threading.Lock()

# 기본 설정 값
default_config = {
    "view_mode": "short",  # 기본값: short (단기)
    "alert_enabled": True,  # BUY/SELL 알림 on/off
    "hint_shown": False,  # 백테스트 힌트 표시 여부
    "show_fundamental_columns": True,  # 펀더멘털 지표 컬럼 표시 여부
    "show_holdings_columns": True,  # 보유 정보 컬럼 표시 여부
    "current": {
        "period": "30d",  # 1분봉의 최대 허용 기간
        "interval": "5m",
        "rsi": {"period": 14, "lower": 30, "upper": 70},
        "ma_cross": {"short": 5, "long": 20},
        "macd": {"short": 6, "long": 13, "signal": 5},  # 빠른 반응용
        "bollinger": {"period": 20, "std_dev_multiplier": 2.0, "use_rebound": False},
        "momentum_return": {"return_window": 5, "threshold": 0.02},  # 짧은 수익률 판단
        "adx_period": 14,
        "adx_filter_enabled": False,
        "sentiment_enabled": False,
        "multi_timeframe_enabled": False,
    },
    "settings": {
        "short": {
            "period": "30d",
            "interval": "5m",
            "rsi": {"period": 14, "lower": 35, "upper": 65},
            "ma_cross": {"short": 5, "long": 20},
            "macd": {"short": 6, "long": 13, "signal": 5},
            "bollinger": {"period": 20, "std_dev_multiplier": 2.0, "use_rebound": False},
            "momentum_return": {"return_window": 5, "threshold": 0.02}
        },
        "middle": {
            "period": "3mo",
            "interval": "30m",
            "rsi": {"period": 14, "lower": 30, "upper": 70},
            "ma_cross": {"short": 10, "long": 50},
            "macd": {"short": 12, "long": 26, "signal": 9},
            "bollinger": {"period": 20, "std_dev_multiplier": 2.0, "use_rebound": True},
            "momentum_return": {"return_window": 20, "threshold": 0.04}
        },
        "long": {
            "period": "1y",
            "interval": "1d",
            "rsi": {"period": 21, "lower": 25, "upper": 75},
            "ma_cross": {"short": 20, "long": 100},
            "macd": {"short": 12, "long": 26, "signal": 9},
            "bollinger": {"period": 20, "std_dev_multiplier": 2.0, "use_rebound": True},
            "momentum_return": {"return_window": 60, "threshold": 0.1}
        }
    },
    "backtest": {
        "period": 12,
        "unit": "mo",
        "method": "momentum_signal",
        "stoploss_enabled": False,
        "stoploss_pct": 5,
        "regime_filter": False,
        "trailing_enabled": False,
        "trailing_type": "pct",
        "trailing_param": 5.0,
        "position_sizing": "full",
        "risk_per_trade": 2.0,
        "atr_sizing_multiplier": 2.0,
        "walk_forward_enabled": False,
        "walk_forward_train_ratio": 0.7,
        "commission_rate": 0.001,
        "slippage_pct": 0.0005,
    }
}

# Valid period/interval combinations for yfinance
VALID_PERIOD_INTERVAL = {
    "1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60,
    "60m": 730, "1h": 730, "90m": 60, "1d": 99999, "5d": 99999,
    "1wk": 99999, "1mo": 99999, "3mo": 99999,
}


def merge_config(user_config, default_config):
    """user_config에 기본값에서 빠진 항목이 있으면 채워 넣는다."""
    for key, default_value in default_config.items():
        if key not in user_config:
            user_config[key] = default_value
        elif isinstance(default_value, dict):
            if not isinstance(user_config[key], dict):
                user_config[key] = default_value
            else:
                merge_config(user_config[key], default_value)
    return user_config


def validate_config(cfg):
    """설정값 유효성 검사. 잘못된 값은 기본값으로 복원."""
    errors = []
    current = cfg.get("current", {})

    # RSI period 검증
    rsi = current.get("rsi", {})
    if not (1 <= rsi.get("period", 14) <= 100):
        errors.append("RSI period must be 1~100")
        current["rsi"]["period"] = default_config["current"]["rsi"]["period"]
    if rsi.get("lower", 30) >= rsi.get("upper", 70):
        errors.append("RSI lower must be less than upper")
        current["rsi"] = default_config["current"]["rsi"].copy()

    # MA cross 검증
    ma = current.get("ma_cross", {})
    if ma.get("short", 5) >= ma.get("long", 20):
        errors.append("MA cross short must be less than long")
        current["ma_cross"] = default_config["current"]["ma_cross"].copy()

    # MACD 검증
    macd = current.get("macd", {})
    if macd.get("short", 6) >= macd.get("long", 13):
        errors.append("MACD short must be less than long")
        current["macd"] = default_config["current"]["macd"].copy()

    # Bollinger 검증
    bb = current.get("bollinger", {})
    if bb.get("period", 20) <= 0:
        errors.append("Bollinger period must be > 0")
        current["bollinger"]["period"] = 20
    if bb.get("std_dev_multiplier", 2.0) <= 0:
        errors.append("Bollinger std_dev must be > 0")
        current["bollinger"]["std_dev_multiplier"] = 2.0

    # Momentum return 검증
    mr = current.get("momentum_return", {})
    if mr.get("return_window", 5) <= 0:
        errors.append("momentum_return window must be > 0")
        current["momentum_return"]["return_window"] = 5
    if mr.get("threshold", 0.02) <= 0:
        errors.append("momentum_return threshold must be > 0")
        current["momentum_return"]["threshold"] = 0.02

    for err in errors:
        logging.warning(f"[CONFIG] Invalid setting: {err}")

    return cfg


def load_config():
    """config.json 파일에서 설정 읽기"""
    with config_lock:
        if not os.path.exists(CONFIG_FILE):
            logging.warning(f"[CONFIG] {CONFIG_FILE} not found, creating with defaults")
            _save_config_internal(default_config)
            return default_config.copy()

        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
            merged_config = merge_config(user_config, default_config)
            validated = validate_config(merged_config)
            logging.info("[CONFIG] Configuration loaded successfully")
            return validated
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"[CONFIG] Error loading config: {e}, using defaults")
            return default_config.copy()


def _save_config_internal(cfg):
    """Atomic write — 내부 전용 (lock 없이)"""
    try:
        dir_name = os.path.dirname(os.path.abspath(CONFIG_FILE))
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
            # Windows: can't rename to existing file, so remove first
            if os.path.exists(CONFIG_FILE):
                os.replace(tmp_path, CONFIG_FILE)
            else:
                os.rename(tmp_path, CONFIG_FILE)
        except Exception:
            # Clean up temp file on failure
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    except Exception as e:
        logging.error(f"[CONFIG] Error saving config: {e}")


def save_config(cfg):
    """config.json 파일에 설정 저장 (thread-safe, atomic write)"""
    with config_lock:
        _save_config_internal(cfg)


def ensure_watchlist_file():
    if not os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_WATCHLIST, f, indent=2)
        logging.info(f"[WATCHLIST] watchlist.json created: {DEFAULT_WATCHLIST}")
    else:
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not data:
                raise ValueError("Invalid or empty watchlist")
        except Exception as e:
            logging.error(f"[WATCHLIST] watchlist.json error: {e}, resetting to defaults")
            with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_WATCHLIST, f, indent=2)


# Lazy config loading — initialized on first access via get_config()
_config = None
_config_init_lock = threading.Lock()


def get_config():
    """Lazy-load config on first access."""
    global _config
    if _config is None:
        with _config_init_lock:
            if _config is None:
                _config = load_config()
    return _config


def set_config(new_config):
    """Update the global config reference."""
    global _config
    _config = new_config


# Backward compatibility: module-level config attribute
# Other modules access config.config — we use property-like access via a wrapper
class _ConfigProxy:
    """Proxy object so `config.config` works with lazy loading."""
    def __getitem__(self, key):
        return get_config()[key]

    def __setitem__(self, key, value):
        get_config()[key] = value

    def __contains__(self, key):
        return key in get_config()

    def __repr__(self):
        return repr(get_config())

    def get(self, key, default=None):
        return get_config().get(key, default)

    def __iter__(self):
        return iter(get_config())

    def keys(self):
        return get_config().keys()

    def values(self):
        return get_config().values()

    def items(self):
        return get_config().items()

    def copy(self):
        return get_config().copy()

    def update(self, *args, **kwargs):
        return get_config().update(*args, **kwargs)


config = _ConfigProxy()


# ============================================================
# Dynamic risk-free rate (^TNX 10-year Treasury yield)
# ============================================================
_risk_free_rate = None
_risk_free_rate_time = 0
_risk_free_rate_lock = threading.Lock()
_RISK_FREE_CACHE_SECONDS = 3600  # 1시간 캐시
_RISK_FREE_FALLBACK = 0.045  # 4.5% fallback


def get_risk_free_rate():
    """10년 국채 수익률을 yfinance로 조회 (1시간 캐시). 실패 시 4.5% 폴백."""
    global _risk_free_rate, _risk_free_rate_time
    with _risk_free_rate_lock:
        now = time.time()
        if _risk_free_rate is not None and (now - _risk_free_rate_time) < _RISK_FREE_CACHE_SECONDS:
            return _risk_free_rate
    try:
        import yfinance as yf
        tnx = yf.Ticker("^TNX")
        hist = tnx.history(period="5d")
        if not hist.empty:
            rate = float(hist['Close'].iloc[-1]) / 100.0  # ^TNX is in percentage
            if 0 < rate < 0.20:  # sanity check: 0~20%
                with _risk_free_rate_lock:
                    _risk_free_rate = rate
                    _risk_free_rate_time = time.time()
                logging.info(f"[CONFIG] Risk-free rate updated: {rate:.4f} ({rate*100:.2f}%)")
                return rate
    except Exception as e:
        logging.warning(f"[CONFIG] Failed to fetch risk-free rate: {e}")
    with _risk_free_rate_lock:
        if _risk_free_rate is not None:
            return _risk_free_rate
    return _RISK_FREE_FALLBACK
