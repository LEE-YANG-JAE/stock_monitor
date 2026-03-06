# 미국 주식 실시간 모니터링 & 전략 백테스트 툴

`Tkinter` 기반 GUI와 `yfinance`, `matplotlib`, `pandas` 등을 활용하여
**미국 주식 실시간 감시**, **다양한 전략 기반 백테스트**, **포트폴리오 분석**, **퀀트 종목 스크리닝** 등을 지원하는 Python 어플리케이션입니다.

---

## 폴더 구성 및 주요 파일

| 파일명 | 설명 |
|--------|------|
| `stock_monitor_gui.py` | 메인 GUI 실행 (실시간 종목 모니터링 + 전략 선택 팝업) |
| `backtest_popup.py` | 전략 선택 및 시각화 백테스트 실행 (8개 전략) |
| `market_trend_manager.py` | 시장 추세 판단, 세션 구분, 변동성 레짐 분류 |
| `stock_score.py` | 각 종목에 대한 기술적 분석 점수 계산 (일목균형표 포함) |
| `config.py` | `config.json` 파일 불러오기 및 기본값 병합 처리 |
| `data_cache.py` | SQLite 기반 yfinance 데이터 캐시 (델타 업데이트, TTL 만료) |
| `pattern_recognition.py` | 차트 패턴 인식 (이중 천장/바닥, 헤드앤숄더, 삼각형) |
| `fundamental_score.py` | 밸류에이션 점수, Piotroski F-Score, 팩터 점수 계산 |
| `portfolio_analysis.py` | 상관관계, 포트폴리오 최적화, Black-Litterman, Fama-French |
| `holdings_manager.py` | 보유 종목 관리 (매수/매도 기록, 손익 계산) |
| `quant_screener.py` | 퀀트 종목 스크리너 (6개 전략) |
| `screener_popup.py` | 스크리너 UI 팝업 (Treeview 결과 + 상세 패널) |
| `stock_universe.py` | 종목 유니버스 (S&P500/NASDAQ100/DOW30 내장 + 온라인 + CSV) |
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
- 현재가, 추세(MA), RSI, MACD, Bollinger Band, 일목균형표, 차트패턴, 모멘텀 종합 신호 표시
- 60초 간격으로 자동 업데이트 (10개 스레드 병렬 조회)
- 프리장, 정규장, 애프터장, 장 종료 구분
- 매수/매도 알림 기능 (`alert_enabled` 설정)
- 로딩 시 종목별 진행률 표시 (`주식 데이터: 3/10 | 뉴스: 로딩 중...`)
- SQLite 기반 데이터 캐시로 빠른 재시작

### 뉴스 패널
- Finviz 실시간 뉴스 스크래핑 (5분 간격 자동 갱신)
- 감성 분류: 상승(긍정) / 하락(부정) / 중립
- 워치리스트 종목 필터링 (관련 뉴스만 표시)
- 티커 클릭 시 종목명과 함께 백테스트 팝업 열기
- 우클릭으로 브라우저에서 기사 원문 열기

### 전략 백테스트
- 종목 더블클릭, 뉴스 티커 클릭, 또는 우클릭 메뉴로 백테스트 팝업 실행
- 전략별 `matplotlib` 시각화 (BUY/SELL 마커, 누적 수익률)
- 전략 비교 및 민감도 분석 (결과 컨테이너 내 임베드)
- 분석 중 프로그레스바 로딩 표시

### 포트폴리오 분석
- 포트폴리오 평가: 보유 종목 기반 수익률, 손익 현황
- 상관관계 매트릭스: 종목 간 상관계수 히트맵
- 포트폴리오 최적화: 최대 샤프, 최소 분산, 리스크 패리티, 동일 비중
- Black-Litterman 모델: 시장 균형 기반 투자자 견해 반영 최적화
- Fama-French 팩터 분석: 3/5 팩터 모델 회귀 분석

### 보유 종목 관리
- 매수/매도 거래 기록 (`holdings.json`)
- 종목별 포지션, 평균 단가, 실현/미실현 손익 계산
- 우클릭 메뉴에서 보유 정보 편집

### 퀀트 종목 스크리너
- 6개 전략: 버핏(Buffett), 그레이엄(Graham), 린치(Lynch), 배당(Dividend), 모멘텀(Momentum), 멀티팩터(Multifactor)
- S&P500, NASDAQ100, DOW30 유니버스 또는 사용자 CSV
- Piotroski F-Score, 밸류에이션 점수, 팩터 점수 통합
- 결과 상세 패널에서 개별 종목 분석

### 기술 차트
- 일목균형표 (전환선, 기준선, 선행 스팬, 후행 스팬)
- 차트 패턴 인식: 이중 천장/바닥, 헤드앤숄더/역 헤드앤숄더, 상승/하강 삼각형
- 우클릭 메뉴 "기술 차트" 또는 백테스트 전략 선택으로 실행

### 설정 관리
- 단기/중기/장기/사용자 지정 프리셋 전환 (라디오 버튼 또는 보기 메뉴)
- 설정 팝업에서 프리셋별 파라미터 개별 조정
- `config.json` 자동 저장/불러오기 (원자적 쓰기로 안전 저장)

### 기타
- 종목 삭제 후 5초 내 실행 취소 지원
- 컬럼 헤더 클릭으로 정렬
- 컬럼 헤더 툴팁 (마우스 오버 시 설명 표시)
- 우클릭 메뉴 (백테스트, 기술 차트, 보유 정보 편집, 삭제, 클립보드 복사)
- 로그 자동 관리 (5MB/파일, 5개 백업, 30일 보관)

---

## 키보드 단축키

| 단축키 | 기능 |
|--------|------|
| `Ctrl+A` | 종목 추가 |
| `Ctrl+D` | 종목 삭제 |
| `Ctrl+R` | 전체 새로고침 |
| `Ctrl+P` | 포트폴리오 평가 |
| `Ctrl+Shift+S` | 퀀트 종목 스크리너 |
| `Ctrl+Q` | 종료 |
| `F5` | 설정 새로고침 |
| `Enter` | 선택된 종목 백테스트 실행 |

---

## 지원 전략 목록

### 백테스트 전략

| 전략 이름 | 설명 |
|-----------|------|
| `ma_cross` | 단기/장기 이동평균선 교차 전략 |
| `macd` | MACD 교차 전략 |
| `rsi` | RSI 과매수/과매도 전략 |
| `macd_rsi` | MACD + RSI 복합 전략 |
| `bollinger` | 볼린저 밴드 돌파 / 반등 전략 |
| `momentum_signal` | 지표 기반 모멘텀 (MACD + MA + RSI + BB) |
| `momentum_return_ma` | 수익률 기반 모멘텀 + MA 크로스 필터 |
| `ichimoku` | 일목균형표 (전환선/기준선 교차, 구름대 돌파) |

### 퀀트 스크리너 전략

| 전략 이름 | 설명 |
|-----------|------|
| `buffett` | 워렌 버핏 스타일 (ROE, 이익 안정성, 해자) |
| `graham` | 벤자민 그레이엄 스타일 (저PER, 저PBR, 안전마진) |
| `lynch` | 피터 린치 스타일 (PEG, 성장성) |
| `dividend` | 배당 투자 (배당수익률, 배당성향, 지속성) |
| `momentum_quant` | 모멘텀 퀀트 (가격 모멘텀, 거래량, 추세) |
| `multifactor` | 멀티팩터 (밸류+퀄리티+모멘텀+사이즈 복합) |

---

## 메뉴 구조

| 메뉴 | 하위 항목 |
|------|-----------|
| 파일 | 설정 새로고침, 설정, 종료 |
| 종목 | 종목 추가, 종목 삭제, 전체 새로고침 |
| 보기 | 단기, 중기, 장기, 사용자 지정 |
| 분석 | 포트폴리오 평가, 상관관계 매트릭스, 포트폴리오 분석, 포트폴리오 최적화, Black-Litterman, Fama-French, 퀀트 종목 스크리너 |
| 도움말 | 용어 설명, 퀀트 투자 가이드, 정보 |

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
    "method": "momentum_signal",
    "commission_rate": 0.001,
    "slippage_pct": 0.0005
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
- `backtest`: 백테스트 팝업 기본 조건 (기간, 단위, 전략, 수수료, 슬리피지)
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
scipy>=1.10
tkcalendar>=1.6
pyinstaller>=6.0
```

`tkinter`는 Python 표준 라이브러리에 포함되어 있어 별도 설치가 필요 없습니다.

---

## 데이터 파일

| 파일 | 설명 |
|------|------|
| `config.json` | 사용자 설정 (첫 실행 시 자동 생성, 변경 시 자동 저장) |
| `watchlist.json` | 감시 종목 목록 (GUI에서 추가/삭제) |
| `holdings.json` | 보유 종목 거래 기록 (매수/매도, 손익 계산) |
| `stock_data_cache.db` | SQLite 데이터 캐시 (yfinance + 펀더멘털) |
| `logs/app.log` | 로테이팅 로그 (5MB/파일, 5개 백업, 30일 보관) |

---

## 참고

- 데이터 제공: [Yahoo Finance](https://finance.yahoo.com)
