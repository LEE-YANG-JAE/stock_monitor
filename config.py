import json
import logging
import os

CONFIG_FILE = 'config.json'
WATCHLIST_FILE = "watchlist.json"
DEFAULT_WATCHLIST = ["SPY", "QQQ"]

# 기본 설정 값
default_config = {
    "view_mode": "short",  # 기본값: short (단기)
    "current": {
        "period": "30d",  # 1분봉의 최대 허용 기간
        "interval": "5m",
        "rsi": {"period": 14,"lower": 30,"upper": 70},
        "ma_cross": {"short": 5, "long": 20},
        "macd": {"short": 6, "long": 13, "signal": 5},  # 빠른 반응용
        "bollinger": {"period": 20, "std_dev_multiplier": 2.0, "use_rebound": False},
        "momentum_return": {"return_window": 5, "threshold": 0.02}  # 짧은 수익률 판단
    },
    "settings": {
        "short": {
            "period": "30d",  # 1분봉의 최대 허용 기간
            "interval": "5m",
            "rsi": {"period": 14,"lower": 35,"upper": 65},
            "ma_cross": {"short": 5, "long": 20},
            "macd": {"short": 6, "long": 13, "signal": 5},  # 빠른 반응용
            "bollinger": {"period": 20, "std_dev_multiplier": 2.0, "use_rebound": False},
            "momentum_return": {"return_window": 5, "threshold": 0.02}  # 짧은 수익률 판단
        },
        "middle": {
            "period": "3mo",
            "interval": "30m",
            "rsi": {"period": 14,"lower": 30,"upper": 70},
            "ma_cross": {"short": 10, "long": 50},
            "macd": {"short": 12, "long": 26, "signal": 9},
            "bollinger": {"period": 20, "std_dev_multiplier": 2.0, "use_rebound": True},
            "momentum_return": {"return_window": 20, "threshold": 0.04}
        },
        "long": {
            "period": "1y",
            "interval": "1d",
            "rsi": {"period": 21,"lower": 25,"upper": 75},
            "ma_cross": {"short": 20, "long": 100},  # 추세 중심
            "macd": {"short": 12, "long": 26, "signal": 9},
            "bollinger": {"period": 20, "std_dev_multiplier": 2.0, "use_rebound": True},
            "momentum_return": {"return_window": 60, "threshold": 0.1}  # 장기 모멘텀 판단
        }
    },
    "backtest": {
        "period": 12,  # 숫자 (예: 12)
        "unit": "mo",  # 단위 (d=일, mo=월, y=년)
        "method": "momentum_signal",
    }
}


def merge_config(user_config, default_config):
    """user_config에 기본값에서 빠진 항목이 있으면 채워 넣는다."""
    for key, default_value in default_config.items():
        if key not in user_config:
            user_config[key] = default_value
        elif isinstance(default_value, dict):
            # 재귀적으로 딕셔너리 내부도 비교
            if not isinstance(user_config[key], dict):
                user_config[key] = default_value
            else:
                merge_config(user_config[key], default_value)
    return user_config


# config.json 파일에서 설정 읽기
def load_config():
    if not os.path.exists(CONFIG_FILE):
        logging.info(f"{CONFIG_FILE} 파일이 없습니다. 기본 설정을 생성합니다.")
        save_config(default_config)
        return default_config

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
            # 누락된 항목 채우기
            merged_config = merge_config(user_config, default_config)
            return merged_config
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(e)
        return default_config


# config.json 파일에 설정 저장
def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:  # UTF-8로 저장
            json.dump(config, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving config: {e}")


def ensure_watchlist_file():
    if not os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_WATCHLIST, f, indent=2)
        logging.info(f"✅ watchlist.json 파일 생성됨: {DEFAULT_WATCHLIST}")
    else:
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not data:
                raise ValueError("비정상적이거나 비어 있음")
        except Exception as e:
            logging.error(f"⚠️ watchlist.json 오류: {e}. 기본값으로 초기화합니다.")
            with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_WATCHLIST, f, indent=2)


# config 로드
config = load_config()

# 설정값 확인
print(f"Loaded config: {config}")
