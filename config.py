import json
import os

CONFIG_FILE = 'config.json'

# 기본 설정 값
default_config = {
  "view_mode": "short",      # 기본값: short (단기)
  "current_period": "14d",   # 단기 데이터 기본 설정
  "current_interval": "1m",  # 1분 간격 기본 설정
  "current_rsi": 14,       # 단기 RSI 기간 설정
  "current_macd": [12, 26, 9],   # 단기 MACD 설정
  "current_bollinger": 14, # 단기 Bollinger Bands 설정
}


# config.json 파일에서 설정 읽기
def load_config():
    # config.json 파일이 없으면 기본 설정을 만들어서 저장
    if not os.path.exists(CONFIG_FILE):
        print(f"{CONFIG_FILE} 파일이 없습니다. 기본 설정을 생성합니다.")
        save_config(default_config)  # 기본 설정을 저장
        return default_config  # 기본 설정 반환

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:  # UTF-8로 열기
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # 파일이 없거나 잘못된 형식일 경우 기본 값으로 설정
        config = default_config
    return config


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
