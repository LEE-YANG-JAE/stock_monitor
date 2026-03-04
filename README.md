# 미국 주식 실시간 모니터링 & 전략 백테스트 툴

`Tkinter` 기반 GUI와 `yfinance`, `matplotlib`, `pandas` 등을 활용하여
**미국 주식 실시간 감시**, **다양한 전략 기반 백테스트**, **설정 저장/복원 기능** 등을 지원하는 Python 어플리케이션입니다.

---

## 폴더 구성 및 주요 파일

| 파일명 | 설명 |
|--------|------|
| `stock_monitor_gui.py` | 메인 GUI 실행 (실시간 종목 모니터링 + 전략 선택 팝업) |
| `backtest_popup.py` | 전략 선택 및 시각화 백테스트 실행 |
| `market_trend_manager.py` | 시장 추세 판단 및 세션 구분 (정규장, 프리장, 애프터장) |
| `stock_score.py` | 각 종목에 대한 기술적 분석 점수 계산 |
| `config.py` | `config.json` 파일 불러오기 및 기본값 병합 처리 |
| `news_panel.py` | Finviz 뉴스 스크래핑, 감성 분류, 티커 연동 |
| `help_texts.py` | 각 기능별 도움말 텍스트 관리 |
| `ui_components.py` | 재사용 가능한 UI 컴포넌트 (Tooltip, HelpTooltip) |

---

## 실행 방법

```bash
pip install -r requirements.txt
python stock_monitor_gui.py
```

- 처음 실행 시 `config.json`, `watchlist.json` 파일과 `logs/` 폴더 및 `app.log`이 자동 생성됩니다.
- 종목 추가 시 `watchlist.json`에 티커가 추가됩니다.

### 실행 파일 생성 (선택사항)

```bash
pyinstaller stock_monitor_gui.spec
```

---

## 주요 기능

### 실시간 종목 모니터링
- 현재가, 추세(MA), RSI, MACD, Bollinger Band, 모멘텀 종합 신호 표시
- 60초 간격으로 자동 업데이트 (10개 스레드 병렬 조회)
- 프리장, 정규장, 애프터장, 장 종료 구분
- 매수/매도 알림 기능 (`alert_enabled` 설정)

### 뉴스 패널
- Finviz 실시간 뉴스 스크래핑 (5분 간격 자동 갱신)
- 감성 분류: 상승(긍정) / 하락(부정) / 중립
- 워치리스트 종목 필터링 (관련 뉴스만 표시)
- 티커 클릭 시 종목명과 함께 백테스트 팝업 열기
- 우클릭으로 브라우저에서 기사 원문 열기

### 전략 백테스트
- 종목 더블클릭, 뉴스 티커 클릭, 또는 우클릭 메뉴로 백테스트 팝업 실행
- 전략별 `matplotlib` 시각화 (BUY/SELL 마커, 누적 수익률)
- 분석 중 프로그레스바 로딩 표시

### 설정 관리
- 단기/중기/장기 프리셋 전환 (라디오 버튼 또는 보기 메뉴)
- 설정 팝업에서 프리셋별 파라미터 개별 조정
- `config.json` 자동 저장/불러오기 (원자적 쓰기로 안전 저장)

### 기타
- 종목 삭제 후 5초 내 실행 취소 지원
- 컬럼 헤더 클릭으로 정렬
- 컬럼 헤더 툴팁 (마우스 오버 시 설명 표시)
- 우클릭 메뉴 (백테스트, 삭제, 클립보드 복사)
- 로그 자동 관리 (5MB/파일, 5개 백업, 30일 보관)

---

## 키보드 단축키

| 단축키 | 기능 |
|--------|------|
| `Ctrl+A` | 종목 추가 |
| `Ctrl+D` | 종목 삭제 |
| `Ctrl+R` | 전체 새로고침 |
| `Ctrl+Q` | 종료 |
| `F5` | 설정 새로고침 |
| `Enter` | 선택된 종목 백테스트 실행 |

---

## 지원 전략 목록

| 전략 이름 | 설명 |
|-----------|------|
| `ma_cross` | 단기/장기 이동평균선 교차 전략 |
| `macd` | MACD 교차 전략 |
| `rsi` | RSI 과매수/과매도 전략 |
| `macd_rsi` | MACD + RSI 복합 전략 |
| `bollinger` | 볼린저 밴드 돌파 / 반등 전략 |
| `momentum_signal` | 지표 기반 모멘텀 (MACD + MA + RSI + BB) |
| `momentum_return_ma` | 수익률 기반 모멘텀 + MA 크로스 필터 |

---

## 시장 세션 구분

| 세션 | 시간 (ET) |
|------|-----------|
| 프리장 | 04:00 ~ 09:30 |
| 정규장 | 09:30 ~ 16:00 |
| 애프터장 | 16:00 ~ 20:00 |
| 장 종료 | 그 외 시간 / 미국 공휴일 |

미국 공휴일은 `holidays` 패키지를 통해 자동 판별됩니다.

---

## 종합 신호 판단 기준

| 점수 | 신호 |
|------|------|
| 4 이상 | 강력 매수 |
| 2 이상 | 매수 |
| -2 ~ 2 | 관망 |
| -2 이하 | 매도 |
| -4 이하 | 강력 매도 |

점수는 MACD(2배), MA(1배), BB(1배), RSI(1배) 가중치로 합산됩니다.

---

## 설정 (`config.json`)

처음 실행 시 기본값으로 자동 생성되며, GUI에서 변경 시 자동 저장됩니다.

### 구조

```json
{
  "view_mode": "short",
  "current": { ... },
  "settings": {
    "short": { ... },
    "middle": { ... },
    "long": { ... }
  },
  "backtest": {
    "period": 1,
    "unit": "y",
    "method": "momentum_signal"
  },
  "alert_enabled": true,
  "hint_shown": true
}
```

### 프리셋 기본값

| 항목 | 단기 (short) | 중기 (middle) | 장기 (long) |
|------|-------------|--------------|------------|
| period | 30d | 3mo | 1y |
| interval | 5m | 30m | 1d |
| RSI 기간 | 14 | 14 | 14 |
| MA 교차 (단/장) | 5 / 20 | 5 / 20 | 5 / 20 |
| MACD (단/장/시그널) | 12 / 26 / 9 | 12 / 26 / 9 | 12 / 26 / 9 |
| BB (기간/표준편차) | 20 / 2.0 | 20 / 2.0 | 20 / 2.0 |
| BB 반등 | false | false | true |

### 항목 설명

- `view_mode`: 현재 선택된 프리셋 (`short` / `middle` / `long`)
- `current`: 현재 분석에 사용되는 활성 설정값
- `settings`: 프리셋별 사전 설정
- `backtest`: 백테스트 팝업 기본 조건 (기간, 단위, 전략)
- `alert_enabled`: 매수/매도 알림 활성화 여부
- `hint_shown`: 백테스트 힌트 표시 여부

---

## `period` & `interval` 설정 가이드

Yahoo Finance API (yfinance)의 `interval`(봉 단위)와 `period`(기간) 조합에는 제한이 있습니다.

| `interval` | 설명 | 허용 `period` (대략) | 권장 용도 |
|------------|------|---------------------|-----------|
| `1m` | 1분봉 | `1d` ~ `7d` | 초단타 전략, 실시간 대응 |
| `5m` | 5분봉 | `1d` ~ `60d` | 단타 및 단기 전략 |
| `15m` | 15분봉 | `1d` ~ `60d` | 단기 기술적 분석 |
| `1h` | 1시간봉 | `7d` ~ `730d` | 스윙 및 중기 전략 |
| `1d` | 일봉 | `1mo` ~ `max` | 장기 투자, 추세 분석 |
| `1wk` | 주봉 | `3mo` ~ `max` | 포트폴리오 리밸런싱 |
| `1mo` | 월봉 | `1y` ~ `max` | 매우 장기적 분석 (거시 관점) |

> `1m` 데이터는 최대 7일까지만 지원됩니다. 그 이상은 에러가 발생합니다.

---

## Requirements

```text
yfinance>=0.2.36
pandas>=2.0
matplotlib>=3.7
numpy>=1.24
pytz>=2023.3
holidays>=0.45
requests>=2.28
beautifulsoup4>=4.12
pyinstaller>=6.0
```

`tkinter`는 Python 표준 라이브러리에 포함되어 있어 별도 설치가 필요 없습니다.

---

## 데이터 파일

| 파일 | 설명 |
|------|------|
| `config.json` | 사용자 설정 (첫 실행 시 자동 생성, 변경 시 자동 저장) |
| `watchlist.json` | 감시 종목 목록 (GUI에서 추가/삭제) |
| `logs/app.log` | 로테이팅 로그 (5MB/파일, 5개 백업, 30일 보관) |

---

## 참고

- 데이터 제공: [Yahoo Finance](https://finance.yahoo.com)
