import json
import os

CONFIG_FILE = 'config.json'

# 기본 설정 값
default_config = {
    "view_mode": "short",  # 기본값: short (단기)
    "current_period": "14d",  # 단기 데이터 기본 설정
    "current_interval": "1m",  # 1분 간격 기본 설정
    "current_rsi": 14,  # 단기 RSI 기간 설정
    "current_macd": [12, 26, 9],  # 단기 MACD 설정
    "current_bollinger": 14,  # 단기 Bollinger Bands 설정
    "settings": {
        "short": {
            "period": "14d",  # 단기 데이터 기본 설정
            "rsi": 14,  # 단기 RSI 기간 설정
            "macd": [12, 26, 9],  # 단기 MACD 설정
            "bollinger": 14,  # 단기 Bollinger Bands 설정
        },
        "long": {
            "period": "6mo",  # 장기 데이터 기본 설정
            "rsi": 14,  # 장기 RSI 기간 설정
            "macd": [12, 26, 9],  # 장기 MACD 설정
            "bollinger": 14,  # 장기 Bollinger Bands 설정
        }
    },
    "backtest": {
        "period_value": 12,  # 숫자 (예: 12)
        "period_unit": "mo",  # 단위 (d=일, mo=월, y=년)
        "method": "macd",
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
        print(f"{CONFIG_FILE} 파일이 없습니다. 기본 설정을 생성합니다.")
        save_config(default_config)
        return default_config

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
            # 누락된 항목 채우기
            merged_config = merge_config(user_config, default_config)
            return merged_config
    except (FileNotFoundError, json.JSONDecodeError):
        return default_config


# config.json 파일에 설정 저장
def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:  # UTF-8로 저장
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")


# config 로드
config = load_config()

# 설정값 확인
print(f"Loaded config: {config}")
