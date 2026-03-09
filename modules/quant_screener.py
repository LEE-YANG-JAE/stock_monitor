# quant_screener.py — 퀀트 종목 스크리닝 엔진
# 6가지 프리셋 전략 + 사용자 정의 필터

import logging
import math
import threading
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

from fundamental_score import (
    safe_get_float, calculate_valuation_score,
    calculate_factor_score, calculate_piotroski_fscore,
)

logger = logging.getLogger(__name__)

ScreenerResult = namedtuple("ScreenerResult", [
    "ticker", "company_name", "sector", "market_cap",
    "current_price", "composite_score", "rank",
    "factor_scores", "raw_metrics", "fair_price",
    "upside_pct", "judgment",
])

STRATEGY_NAMES = {
    "buffett": "워렌 버핏 가치투자",
    "graham": "벤저민 그레이엄 안전마진",
    "lynch": "피터 린치 GARP",
    "dividend": "배당 귀족",
    "momentum_quant": "모멘텀 퀀트",
    "multifactor": "종합 멀티팩터",
}

DEFAULT_MULTIFACTOR_WEIGHTS = {
    "value": 25, "quality": 25, "growth": 20,
    "momentum": 15, "dividend": 15,
}


# ============================================================
# 개별 전략 스코어링 함수
# ============================================================

def calculate_buffett_score(info: dict) -> dict:
    """워렌 버핏 가치투자 스코어 (0-100)."""
    score = 0
    max_score = 100
    details = {}

    roe = safe_get_float(info, "returnOnEquity")
    de = safe_get_float(info, "debtToEquity")
    om = safe_get_float(info, "operatingMargins")
    per = safe_get_float(info, "trailingPE")
    fcf = safe_get_float(info, "freeCashflow")
    earn_growth = safe_get_float(info, "earningsGrowth")
    rev_growth = safe_get_float(info, "revenueGrowth")
    gm = safe_get_float(info, "grossMargins")
    cr = safe_get_float(info, "currentRatio")

    # ROE >= 15% (25점)
    if roe is not None:
        roe_pct = roe * 100
        if roe_pct >= 20:
            details["ROE"] = 25
        elif roe_pct >= 15:
            details["ROE"] = 20
        elif roe_pct >= 10:
            details["ROE"] = 10
        else:
            details["ROE"] = 0
    else:
        details["ROE"] = 0

    # 부채비율 < 50% (20점)
    if de is not None:
        if de < 50:
            details["부채비율"] = 20
        elif de < 100:
            details["부채비율"] = 12
        elif de < 150:
            details["부채비율"] = 5
        else:
            details["부채비율"] = 0
    else:
        details["부채비율"] = 5  # 데이터 없으면 중립

    # 영업이익률 > 15% (20점)
    if om is not None:
        om_pct = om * 100
        if om_pct >= 20:
            details["영업이익률"] = 20
        elif om_pct >= 15:
            details["영업이익률"] = 15
        elif om_pct >= 10:
            details["영업이익률"] = 8
        else:
            details["영업이익률"] = 0
    else:
        details["영업이익률"] = 0

    # 합리적 PER (15점)
    if per is not None:
        if 5 <= per <= 20:
            details["PER"] = 15
        elif 20 < per <= 30:
            details["PER"] = 8
        elif per > 30:
            details["PER"] = 0
        else:
            details["PER"] = 5  # 매우 낮은 PER은 주의
    else:
        details["PER"] = 0

    # FCF > 0 (10점)
    if fcf is not None:
        details["FCF"] = 10 if fcf > 0 else 0
    else:
        details["FCF"] = 0

    # 이익 성장 (10점)
    if earn_growth is not None:
        if earn_growth > 0.1:
            details["이익성장"] = 10
        elif earn_growth > 0:
            details["이익성장"] = 5
        else:
            details["이익성장"] = 0
    else:
        details["이익성장"] = 0

    score = sum(details.values())
    grade = _score_to_grade(score)

    return {"score": score, "max_score": max_score, "details": details, "grade": grade}


def calculate_graham_score(info: dict) -> dict:
    """벤저민 그레이엄 안전마진 스코어 (0-100)."""
    score = 0
    details = {}
    graham_number = None

    per = safe_get_float(info, "trailingPE")
    pbr = safe_get_float(info, "priceToBook")
    eps = safe_get_float(info, "trailingEps")
    bps = safe_get_float(info, "bookValue")
    cr = safe_get_float(info, "currentRatio")
    de = safe_get_float(info, "debtToEquity")
    div_yield = safe_get_float(info, "dividendYield")
    current_price = safe_get_float(info, "currentPrice")
    earn_growth = safe_get_float(info, "earningsGrowth")

    # 그레이엄 넘버 = sqrt(22.5 * EPS * BPS)
    if eps is not None and eps > 0 and bps is not None and bps > 0:
        graham_number = math.sqrt(22.5 * eps * bps)

    # PER < 15 (20점)
    if per is not None:
        if per < 10:
            details["PER<15"] = 20
        elif per < 15:
            details["PER<15"] = 15
        elif per < 20:
            details["PER<15"] = 5
        else:
            details["PER<15"] = 0
    else:
        details["PER<15"] = 0

    # PBR < 1.5 (20점)
    if pbr is not None:
        if pbr < 1.0:
            details["PBR<1.5"] = 20
        elif pbr < 1.5:
            details["PBR<1.5"] = 15
        elif pbr < 2.0:
            details["PBR<1.5"] = 5
        else:
            details["PBR<1.5"] = 0
    else:
        details["PBR<1.5"] = 0

    # 그레이엄 넘버 대비 안전마진 (25점)
    if graham_number is not None and current_price is not None and current_price > 0:
        margin = (graham_number - current_price) / current_price * 100
        if margin >= 30:
            details["안전마진"] = 25
        elif margin >= 15:
            details["안전마진"] = 18
        elif margin >= 0:
            details["안전마진"] = 10
        else:
            details["안전마진"] = 0
    else:
        details["안전마진"] = 0

    # 유동비율 > 2 (15점)
    if cr is not None:
        if cr >= 2.0:
            details["유동비율"] = 15
        elif cr >= 1.5:
            details["유동비율"] = 10
        elif cr >= 1.0:
            details["유동비율"] = 5
        else:
            details["유동비율"] = 0
    else:
        details["유동비율"] = 0

    # 부채비율 낮음 (10점)
    if de is not None:
        if de < 50:
            details["저부채"] = 10
        elif de < 100:
            details["저부채"] = 5
        else:
            details["저부채"] = 0
    else:
        details["저부채"] = 0

    # 배당 지급 (10점)
    if div_yield is not None and div_yield > 0:
        details["배당"] = 10
    else:
        details["배당"] = 0

    score = sum(details.values())
    grade = _score_to_grade(score)

    return {
        "score": score, "max_score": 100, "details": details,
        "grade": grade, "graham_number": graham_number,
    }


def calculate_lynch_score(info: dict) -> dict:
    """피터 린치 GARP 스코어 (0-100)."""
    details = {}

    peg = safe_get_float(info, "pegRatio")
    per = safe_get_float(info, "trailingPE")
    earn_growth = safe_get_float(info, "earningsGrowth")
    rev_growth = safe_get_float(info, "revenueGrowth")
    insider = safe_get_float(info, "heldPercentInsiders")
    de = safe_get_float(info, "debtToEquity")

    # PEG 직접 계산 fallback
    if peg is None and per is not None and earn_growth is not None:
        eg_pct = earn_growth * 100
        if eg_pct > 0:
            peg = per / eg_pct

    # PEG < 1.0 (30점)
    if peg is not None:
        if peg < 0.5:
            details["PEG"] = 30
        elif peg < 1.0:
            details["PEG"] = 25
        elif peg < 1.5:
            details["PEG"] = 10
        else:
            details["PEG"] = 0
    else:
        details["PEG"] = 0

    # 이익 성장률 10-25% (25점)
    if earn_growth is not None:
        eg = earn_growth * 100
        if 10 <= eg <= 25:
            details["이익성장"] = 25
        elif 5 <= eg < 10:
            details["이익성장"] = 15
        elif 25 < eg <= 50:
            details["이익성장"] = 15
        elif eg > 0:
            details["이익성장"] = 5
        else:
            details["이익성장"] = 0
    else:
        details["이익성장"] = 0

    # PER < 20 (20점)
    if per is not None:
        if per < 15:
            details["PER"] = 20
        elif per < 20:
            details["PER"] = 15
        elif per < 25:
            details["PER"] = 5
        else:
            details["PER"] = 0
    else:
        details["PER"] = 0

    # 내부자 보유 > 5% (15점)
    if insider is not None:
        ins_pct = insider * 100
        if ins_pct >= 10:
            details["내부자보유"] = 15
        elif ins_pct >= 5:
            details["내부자보유"] = 10
        elif ins_pct >= 1:
            details["내부자보유"] = 5
        else:
            details["내부자보유"] = 0
    else:
        details["내부자보유"] = 0

    # 부채비율 (10점)
    if de is not None:
        if de < 50:
            details["저부채"] = 10
        elif de < 100:
            details["저부채"] = 5
        else:
            details["저부채"] = 0
    else:
        details["저부채"] = 0

    score = sum(details.values())
    grade = _score_to_grade(score)

    return {"score": score, "max_score": 100, "details": details, "grade": grade}


def calculate_dividend_score(info: dict) -> dict:
    """배당 귀족 스코어 (0-100)."""
    details = {}

    div_yield = safe_get_float(info, "dividendYield")
    payout = safe_get_float(info, "payoutRatio")
    roe = safe_get_float(info, "returnOnEquity")
    de = safe_get_float(info, "debtToEquity")
    earn_growth = safe_get_float(info, "earningsGrowth")
    fcf = safe_get_float(info, "freeCashflow")
    rev_growth = safe_get_float(info, "revenueGrowth")

    # 배당수익률 2-6% (30점)
    if div_yield is not None:
        dy = div_yield * 100
        if 2 <= dy <= 4:
            details["배당수익률"] = 30
        elif 4 < dy <= 6:
            details["배당수익률"] = 25
        elif 1 <= dy < 2:
            details["배당수익률"] = 10
        elif dy > 6:
            details["배당수익률"] = 5  # 너무 높은 배당 = 지속가능성 우려
        else:
            details["배당수익률"] = 0
    else:
        details["배당수익률"] = 0

    # 배당성향 < 60% (25점)
    if payout is not None:
        pp = payout * 100
        if 20 <= pp <= 50:
            details["배당성향"] = 25
        elif 50 < pp <= 60:
            details["배당성향"] = 15
        elif pp < 20:
            details["배당성향"] = 10
        elif pp <= 80:
            details["배당성향"] = 5
        else:
            details["배당성향"] = 0
    else:
        details["배당성향"] = 0

    # ROE (15점)
    if roe is not None:
        roe_pct = roe * 100
        if roe_pct >= 15:
            details["ROE"] = 15
        elif roe_pct >= 10:
            details["ROE"] = 10
        elif roe_pct >= 5:
            details["ROE"] = 5
        else:
            details["ROE"] = 0
    else:
        details["ROE"] = 0

    # 이익 안정성/성장 (15점)
    if earn_growth is not None:
        if earn_growth > 0:
            details["이익안정"] = 15
        elif earn_growth > -0.1:
            details["이익안정"] = 8
        else:
            details["이익안정"] = 0
    else:
        details["이익안정"] = 0

    # FCF (15점)
    if fcf is not None:
        details["FCF"] = 15 if fcf > 0 else 0
    else:
        details["FCF"] = 0

    score = sum(details.values())
    grade = _score_to_grade(score)

    return {"score": score, "max_score": 100, "details": details, "grade": grade}


def calculate_momentum_quant_score(info: dict) -> dict:
    """모멘텀 퀀트 스코어 (0-100)."""
    details = {}

    hi = safe_get_float(info, "fiftyTwoWeekHigh")
    lo = safe_get_float(info, "fiftyTwoWeekLow")
    cp = safe_get_float(info, "currentPrice")
    volume = safe_get_float(info, "volume")
    avg_volume = safe_get_float(info, "averageVolume")
    roe = safe_get_float(info, "returnOnEquity")
    beta = safe_get_float(info, "beta")
    rev_growth = safe_get_float(info, "revenueGrowth")
    earn_growth = safe_get_float(info, "earningsGrowth")

    # 52주 위치 30-80% (30점)
    if hi is not None and lo is not None and cp is not None and hi > lo:
        pct = (cp - lo) / (hi - lo) * 100
        if 30 <= pct <= 60:
            details["52주위치"] = 30
        elif 60 < pct <= 80:
            details["52주위치"] = 20
        elif 20 <= pct < 30:
            details["52주위치"] = 15
        elif pct > 80:
            details["52주위치"] = 5
        else:
            details["52주위치"] = 0
    else:
        details["52주위치"] = 0

    # 거래량 비율 > 1 (20점)
    if volume is not None and avg_volume is not None and avg_volume > 0:
        vr = volume / avg_volume
        if vr >= 1.5:
            details["거래량"] = 20
        elif vr >= 1.0:
            details["거래량"] = 15
        elif vr >= 0.7:
            details["거래량"] = 8
        else:
            details["거래량"] = 0
    else:
        details["거래량"] = 0

    # ROE 퀄리티 필터 > 10% (20점)
    if roe is not None:
        roe_pct = roe * 100
        if roe_pct >= 15:
            details["ROE퀄리티"] = 20
        elif roe_pct >= 10:
            details["ROE퀄리티"] = 15
        elif roe_pct >= 5:
            details["ROE퀄리티"] = 5
        else:
            details["ROE퀄리티"] = 0
    else:
        details["ROE퀄리티"] = 0

    # 매출 성장 (15점)
    if rev_growth is not None:
        if rev_growth > 0.1:
            details["매출성장"] = 15
        elif rev_growth > 0:
            details["매출성장"] = 8
        else:
            details["매출성장"] = 0
    else:
        details["매출성장"] = 0

    # 이익 성장 (15점)
    if earn_growth is not None:
        if earn_growth > 0.1:
            details["이익성장"] = 15
        elif earn_growth > 0:
            details["이익성장"] = 8
        else:
            details["이익성장"] = 0
    else:
        details["이익성장"] = 0

    score = sum(details.values())
    grade = _score_to_grade(score)

    return {"score": score, "max_score": 100, "details": details, "grade": grade}


def calculate_multifactor_score(info: dict, weights: dict = None) -> dict:
    """종합 멀티팩터 스코어 — 기존 fundamental_score 재활용."""
    if weights is None:
        weights = DEFAULT_MULTIFACTOR_WEIGHTS

    total_weight = sum(weights.values())
    if total_weight == 0:
        total_weight = 100

    details = {}

    # 밸류 팩터 (기존 calculate_valuation_score 재활용)
    val_data = calculate_valuation_score(info)
    # 7점 만점 → 100점 환산 (score 범위: -7 ~ +7)
    val_raw = val_data.score
    val_normalized = max(0, min(100, (val_raw + 7) / 14 * 100))
    details["value"] = round(val_normalized, 1)

    # 퀄리티 팩터 (피오트로스키 F-Score 재활용)
    fscore = calculate_piotroski_fscore(info)
    if fscore["max_score"] > 0:
        qual_normalized = fscore["score"] / fscore["max_score"] * 100
    else:
        qual_normalized = 50
    details["quality"] = round(qual_normalized, 1)

    # 성장 팩터
    earn_growth = safe_get_float(info, "earningsGrowth")
    rev_growth = safe_get_float(info, "revenueGrowth")
    growth_score = 50  # 기본값
    if earn_growth is not None:
        if earn_growth > 0.2:
            growth_score = 90
        elif earn_growth > 0.1:
            growth_score = 75
        elif earn_growth > 0:
            growth_score = 60
        elif earn_growth > -0.1:
            growth_score = 35
        else:
            growth_score = 15
    if rev_growth is not None and rev_growth > 0.1:
        growth_score = min(100, growth_score + 10)
    details["growth"] = round(growth_score, 1)

    # 모멘텀 팩터
    hi = safe_get_float(info, "fiftyTwoWeekHigh")
    lo = safe_get_float(info, "fiftyTwoWeekLow")
    cp = safe_get_float(info, "currentPrice")
    momentum_score = 50
    if hi is not None and lo is not None and cp is not None and hi > lo:
        pct = (cp - lo) / (hi - lo)
        if 0.3 <= pct <= 0.7:
            momentum_score = 80
        elif 0.7 < pct <= 0.85:
            momentum_score = 60
        elif pct > 0.85:
            momentum_score = 30
        elif 0.15 <= pct < 0.3:
            momentum_score = 55
        else:
            momentum_score = 25
    details["momentum"] = round(momentum_score, 1)

    # 배당 팩터
    div_yield = safe_get_float(info, "dividendYield")
    payout = safe_get_float(info, "payoutRatio")
    div_score = 50
    if div_yield is not None:
        dy = div_yield * 100
        if 2 <= dy <= 5:
            div_score = 85
        elif 1 <= dy < 2:
            div_score = 60
        elif 5 < dy <= 8:
            div_score = 50
        elif dy > 8:
            div_score = 25
        else:
            div_score = 40
    if payout is not None and 0.2 <= payout <= 0.6:
        div_score = min(100, div_score + 10)
    details["dividend"] = round(div_score, 1)

    # 가중 평균
    composite = 0
    for factor, w in weights.items():
        composite += details.get(factor, 50) * (w / total_weight)

    grade = _score_to_grade(composite)

    return {
        "score": round(composite, 1), "max_score": 100,
        "details": details, "grade": grade,
    }


# ============================================================
# 사용자 정의 필터
# ============================================================

def apply_custom_filters(info: dict, filters: dict) -> bool:
    """사용자 지정 범위 필터. 통과하면 True."""
    _MAP = {
        "per_min": ("trailingPE", "min"),
        "per_max": ("trailingPE", "max"),
        "pbr_min": ("priceToBook", "min"),
        "pbr_max": ("priceToBook", "max"),
        "roe_min": ("returnOnEquity", "min", 100),  # * 100
        "roe_max": ("returnOnEquity", "max", 100),
        "debt_min": ("debtToEquity", "min"),
        "debt_max": ("debtToEquity", "max"),
        "div_min": ("dividendYield", "min", 100),
        "div_max": ("dividendYield", "max", 100),
        "peg_min": ("pegRatio", "min"),
        "peg_max": ("pegRatio", "max"),
        "om_min": ("operatingMargins", "min", 100),
        "om_max": ("operatingMargins", "max", 100),
        "mcap_min": ("marketCap", "min", 1e-9),  # 십억 단위 입력
        "mcap_max": ("marketCap", "max", 1e-9),
    }

    for fkey, spec in _MAP.items():
        threshold = filters.get(fkey)
        if threshold is None:
            continue
        yf_key = spec[0]
        direction = spec[1]
        multiplier = spec[2] if len(spec) > 2 else 1

        val = safe_get_float(info, yf_key)
        if val is None:
            continue  # 데이터 없으면 필터 통과

        adjusted = val * multiplier

        if direction == "min" and adjusted < threshold:
            return False
        if direction == "max" and adjusted > threshold:
            return False

    return True


# ============================================================
# 스크리닝 엔진
# ============================================================

_STRATEGY_FUNC = {
    "buffett": calculate_buffett_score,
    "graham": calculate_graham_score,
    "lynch": calculate_lynch_score,
    "dividend": calculate_dividend_score,
    "momentum_quant": calculate_momentum_quant_score,
}


def _fetch_info_cached(ticker: str) -> dict:
    """yfinance info를 캐시 경유로 가져오기."""
    try:
        from data_cache import get_cached_fundamental_or_fetch
        return get_cached_fundamental_or_fetch(ticker)
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[SCREENER] Cache miss for {ticker}: {e}")

    # 폴백: 직접 yfinance 호출
    try:
        return yf.Ticker(ticker).info
    except Exception as e:
        logger.warning(f"[SCREENER] Failed to fetch {ticker}: {e}")
        return {}


def _score_one(ticker: str, strategy: str, weights: dict = None,
               custom_filters: dict = None, min_mcap: float = None) -> dict:
    """단일 종목 스코어링. 스레드에서 호출."""
    info = _fetch_info_cached(ticker)
    if not info or info.get("quoteType") == "NONE":
        return None

    # 시가총액 필터
    mcap = safe_get_float(info, "marketCap")
    if min_mcap is not None and mcap is not None:
        if mcap < min_mcap * 1e9:
            return None

    # 사용자 정의 필터
    if custom_filters and not apply_custom_filters(info, custom_filters):
        return None

    # 전략별 스코어 계산
    if strategy == "multifactor":
        result = calculate_multifactor_score(info, weights)
    elif strategy in _STRATEGY_FUNC:
        result = _STRATEGY_FUNC[strategy](info)
    else:
        result = calculate_multifactor_score(info, weights)

    # 공통 메트릭 추출
    current_price = safe_get_float(info, "currentPrice")
    val_data = calculate_valuation_score(info)
    fair_price = val_data.fair_price

    upside_pct = None
    if fair_price is not None and current_price is not None and current_price > 0:
        upside_pct = (fair_price - current_price) / current_price * 100

    raw_metrics = {
        "per": safe_get_float(info, "trailingPE"),
        "pbr": safe_get_float(info, "priceToBook"),
        "roe": _pct(safe_get_float(info, "returnOnEquity")),
        "debt_equity": safe_get_float(info, "debtToEquity"),
        "div_yield": _pct(safe_get_float(info, "dividendYield")),
        "peg": safe_get_float(info, "pegRatio"),
        "operating_margin": _pct(safe_get_float(info, "operatingMargins")),
        "earn_growth": _pct(safe_get_float(info, "earningsGrowth")),
        "beta": safe_get_float(info, "beta"),
        "fcf": safe_get_float(info, "freeCashflow"),
        "current_ratio": safe_get_float(info, "currentRatio"),
        "payout_ratio": _pct(safe_get_float(info, "payoutRatio")),
        "insider_pct": _pct(safe_get_float(info, "heldPercentInsiders")),
        "52w_pct": val_data.fifty_two_week_pct,
    }

    company_name = info.get("shortName") or info.get("longName") or ticker
    sector = info.get("sector") or "N/A"
    mcap_display = mcap

    return {
        "ticker": ticker,
        "company_name": company_name,
        "sector": sector,
        "market_cap": mcap_display,
        "current_price": current_price,
        "composite_score": result["score"],
        "factor_scores": result.get("details", {}),
        "raw_metrics": raw_metrics,
        "fair_price": fair_price,
        "upside_pct": upside_pct,
        "judgment": result.get("grade", "N/A"),
        "strategy_result": result,
    }


def screen_universe(tickers: list, strategy: str = "multifactor",
                    weights: dict = None, custom_filters: dict = None,
                    min_mcap: float = None, top_n: int = 50,
                    progress_callback=None, cancel_event: threading.Event = None,
                    max_workers: int = 8) -> list:
    """유니버스 전체를 스크리닝하여 상위 종목 반환.

    Args:
        tickers: 스크리닝 대상 티커 리스트
        strategy: 전략 키 (buffett, graham, lynch, dividend, momentum_quant, multifactor)
        weights: multifactor 가중치 dict
        custom_filters: 사용자 정의 필터 dict
        min_mcap: 최소 시가총액 (십억 달러)
        top_n: 상위 N개 결과 반환
        progress_callback: fn(completed, total, current_ticker) — UI 업데이트
        cancel_event: 취소용 threading.Event
        max_workers: 병렬 워커 수

    Returns:
        list[ScreenerResult] — 점수 내림차순 정렬, rank 포함
    """
    results = []
    total = len(tickers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for t in tickers:
            if cancel_event and cancel_event.is_set():
                break
            f = executor.submit(_score_one, t, strategy, weights,
                                custom_filters, min_mcap)
            futures[f] = t

        completed = 0
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                # 남은 future 취소
                for remaining in futures:
                    remaining.cancel()
                break

            completed += 1
            ticker = futures[future]
            try:
                data = future.result(timeout=30)
                if data is not None:
                    results.append(data)
            except Exception as e:
                logger.debug(f"[SCREENER] Error scoring {ticker}: {e}")

            if progress_callback:
                progress_callback(completed, total, ticker)

    # 정렬 및 순위 부여
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    if top_n:
        results = results[:top_n]

    screener_results = []
    for rank, r in enumerate(results, 1):
        screener_results.append(ScreenerResult(
            ticker=r["ticker"],
            company_name=r["company_name"],
            sector=r["sector"],
            market_cap=r["market_cap"],
            current_price=r["current_price"],
            composite_score=r["composite_score"],
            rank=rank,
            factor_scores=r["factor_scores"],
            raw_metrics=r["raw_metrics"],
            fair_price=r["fair_price"],
            upside_pct=r["upside_pct"],
            judgment=r["judgment"],
        ))

    return screener_results


# ============================================================
# 유틸리티
# ============================================================

def _pct(val):
    """소수 → 퍼센트 변환. None 안전."""
    if val is None:
        return None
    return round(val * 100, 2)


def _score_to_grade(score: float) -> str:
    if score >= 75:
        return "A (우수)"
    elif score >= 55:
        return "B (양호)"
    elif score >= 35:
        return "C (보통)"
    else:
        return "D (부진)"
