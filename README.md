# 📊 미국 주식 실시간 모니터링 & 전략 백테스트 툴

본 프로젝트는 `Tkinter` 기반 GUI와 `yfinance`, `matplotlib`, `pandas` 등을 활용하여  
**미국 주식 실시간 감시**, **다양한 전략 기반 백테스트**, **설정 저장/복원 기능** 등을 지원하는 Python 어플리케이션입니다.

---

## 📁 폴더 구성 및 주요 파일

| 파일명 | 설명 |
|--------|------|
| `stock_monitor_gui.py` | 메인 GUI 실행 (실시간 종목 모니터링 + 전략 선택 팝업) |
| `backtest_popup.py` | 전략 선택 및 시각화 백테스트 실행 |
| `market_trend_manager.py` | 시장 추세 판단 및 세션 구분 (정규장, 프리장 등) |
| `stock_score.py` | 각 종목에 대한 기술적 분석 점수 계산 |
| `config.py` | `config.json` 파일 불러오기 및 병합 처리 |

---

## 🚀 실행 방법

```bash
pip install -r requirements.txt
python stock_monitor_gui.py
```

 - 처음 실행 시 `config.json`, `watchlist.json` 파일과 log 폴더와 `app.log` 자동 생성됩니다. 
-  종목 추가시 `watchlist.json` 에 티커가 추가됩니다.

### 실행 파일 생성 (선택사항)

```bash
pyinstaller stock_monitor_gui.spec
````
---
## ⚙ 주요 기능

### ✅ 실시간 종목 모니터링
- 현재가, 추세(MA), RSI, MACD, Bollinger Band, 모멘텀 종합 신호 표시
- 1분 간격으로 자동 업데이트
- 프리장, 정규장, 애프터장 구분

### ✅ 전략 백테스트 지원
- 팝업에서 종목 선택 후 전략 선택 가능
- 전략별로 `matplotlib`을 통한 시각화 제공

---

## 🧠 지원 전략 목록

| 전략 이름 | 설명 |
|-----------|------|
| `macd` | MACD 교차 전략 |
| `rsi` | RSI 과매수/과매도 전략 |
| `bollinger` | 볼린저 밴드 돌파 / 반등 전략 |
| `ma_cross` | 단기/장기 이동평균선 교차 전략 |
| `momentum_signal` | 지표 기반 모멘텀 (MACD + MA + RSI + BB) |
| `momentum_return_ma` | 수익률 기반 모멘텀 + MA 크로스 필터 |

---

## 🧾 설정 예시 및 방법 (`config.json`)

```json
{
  "view_mode": "long",
  "current": {
    "period": "1y",
    "interval": "1m",
    "rsi": 14,
    "ma_cross": {
      "short": 5,
      "long": 20
    },
    "macd": {
      "short": 12,
      "long": 26,
      "signal": 9
    },
    "bollinger": {
      "period": 20,
      "std_dev_multiplier": 2.0,
      "use_rebound": false
    },
    "momentum_return": {
      "return_window": 30,
      "threshold": 0.05
    }
  },
  "settings": {
    "short": {
      "period": "14d",
      "rsi": 14,
      "ma_cross": {
        "short": 5,
        "long": 20
      },
      "macd": {
        "short": 12,
        "long": 26,
        "signal": 9
      },
      "bollinger": {
        "period": 20,
        "std_dev_multiplier": 2.0,
        "use_rebound": false
      },
      "momentum_return": {
        "return_window": 30,
        "threshold": 0.05
      }
    },
    "long": {
      "period": "1y",
      "rsi": 14,
      "ma_cross": {
        "short": 5,
        "long": 20
      },
      "macd": {
        "short": 12,
        "long": 26,
        "signal": 9
      },
      "bollinger": {
        "period": 20,
        "std_dev_multiplier": 2.0,
        "use_rebound": true
      },
      "momentum_return": {
        "return_window": 30,
        "threshold": 0.05
      }
    }
  },
  "backtest": {
    "period_value": 6,
    "period_unit": "mo",
    "method": "macd"
  }
}
```
### 🔍 설명
- `current`: 현재 분석/백테스트에 사용되는 설정값들
- `settings.short / long`: 단기/장기 전략을 빠르게 전환할 수 있는 사전 설정
- `backtest`: 팝업에서 사용할 기본 백테스트 조건

- **`period` / `interval`**: 데이터 조회 구간
- **`ma_cross`**: 단기/장기 MA 설정
- **`momentum_return`**: 수익률 기준 기간과 임계값
- **`backtest.method`**: 팝업에서 기본 선택될 전략



# ⏱ `period` & `interval` 설정 가이드

이 앱은 Yahoo Finance API (yfinance)를 사용하여 주식 데이터를 얻습니다.   
`interval`(봉 단위)와 `period`(기간) 조합에는 제한이 있습니다.   
아래 표는 각 `interval`에 대해 허용되는 적절한 `period` 범위와 활용 용도를 정리한 것입니다.

---

## 📊 interval별 허용 period

| `interval`  | 설명            | 허용 `period` (대략)      | 권장 용도                      |
|-------------|-----------------|----------------------------|--------------------------------|
| `"1m"`      | 1분봉           | `"1d"` ~ `"7d"`            | 초단타 전략, 실시간 대응     |
| `"5m"`      | 5분봉           | `"1d"` ~ `"60d"`           | 단타 및 단기 전략            |
| `"15m"`     | 15분봉          | `"1d"` ~ `"60d"`           | 단기 기술적 분석             |
| `"1h"`      | 1시간봉         | `"7d"` ~ `"730d"`          | 스윙 및 중기 전략            |
| `"1d"`      | 일봉            | `"1mo"` ~ `"max"`          | 장기 투자, 추세 분석         |
| `"1wk"`     | 주봉            | `"3mo"` ~ `"max"`          | 포트폴리오 리밸런싱          |
| `"1mo"`     | 월봉            | `"1y"` ~ `"max"`           | 매우 장기적 분석 (거시 관점) |

---

## 🧾 config.json 설정 예시

```json
{
  "current": {
    "period": "1y",
    "interval": "1d"
  }
}
```

### 예시 목적별 조합

| 전략 목적 | 설정 예시 |
|-----------|-----------|
| 실시간 분석 | `"period": "5d", "interval": "1m"` |
| 단기 분석   | `"period": "20d", "interval": "5m"` |
| 중기 분석   | `"period": "6mo", "interval": "1h"` |
| 장기 분석   | `"period": "2y", "interval": "1d"` |
| 초장기 투자 | `"period": "max", "interval": "1wk"` |

---

## ⚠️ 주의 사항

- `"1m"` 데이터는 최대 7일까지만 지원됨. 그 이상은 에러 발생.
- `"max"`는 전체 기간 요청이 가능하지만, `"1d"` 이상의 간격만 허용됨.
---
이 가이드는 실시간 데이터 오류 방지 및 안정적인 전략 실행을 위한 추천 기준입니다.
---

## 🖼 전략 백테스트 결과 예시

> ✅ BUY/SELL 시점이 차트에 마킹되고, 전략별 누적 수익률을 출력합니다.

- `MACD`: 이중 지표 그래프
- `RSI`: 30/70 선과 함께 매수/매도 표시
- `MA 교차`: MA(5) / MA(20)과 교차점 표시
- `모멘텀`: MACD, RSI, BB, MA 전부 시각화

---

## 💬 기타 기능

- 단기/장기 설정 스위치 (라디오 버튼으로 전체 파라미터 전환)
- 설정값 `config.json`에 자동 저장/불러오기
- 감시 종목 `watchlist.json` 파일로 유지

---

## 🛠 Requirements

```text
yfinance
pandas
matplotlib
tkinter
numpy
pytz
holidays
```

---

## 🧑‍💻 만든 목적

- 실전에서 사용할 수 있는 전략 검증 툴킷
- 초보자도 시각적으로 쉽게 접근 가능
- 전략 실험과 빠른 튜닝을 GUI 기반으로 수행 가능

---

## 🔗 참고

- 데이터 제공: [Yahoo Finance](https://finance.yahoo.com)
---