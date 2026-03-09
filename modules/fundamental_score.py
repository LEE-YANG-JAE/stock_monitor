# fundamental_score.py — 밸류에이션 스코어링 공유 모듈
# backtest_popup.py와 메인 뷰(stock_score.py)에서 공통 사용

from collections import namedtuple

FundamentalData = namedtuple('FundamentalData', [
    'per', 'pbr', 'roe', 'score', 'total_criteria',
    'judgment', 'fair_price', 'fifty_two_week_pct',
    'criteria', 'volume_ratio', 'beta',
])


def safe_get_float(info, key):
    """yfinance info dict에서 안전하게 float 추출."""
    v = info.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def calculate_valuation_score(info):
    """7점 밸류에이션 스코어 + 적정가격 계산.

    Args:
        info: yfinance Ticker.info dict

    Returns:
        FundamentalData namedtuple
    """
    per = safe_get_float(info, "trailingPE")
    pbr = safe_get_float(info, "priceToBook")
    peg = safe_get_float(info, "pegRatio")
    eps_val = safe_get_float(info, "trailingEps")
    roe_val = safe_get_float(info, "returnOnEquity")
    ev_ebitda = safe_get_float(info, "enterpriseToEbitda")
    debt_equity = safe_get_float(info, "debtToEquity")
    earn_growth = safe_get_float(info, "earningsGrowth")
    current_price = safe_get_float(info, "currentPrice")
    hi = safe_get_float(info, "fiftyTwoWeekHigh")
    lo = safe_get_float(info, "fiftyTwoWeekLow")
    book_val = safe_get_float(info, "bookValue")

    # PEG 직접 계산 fallback
    if peg is None and per is not None and earn_growth is not None:
        eg_pct = earn_growth * 100
        if eg_pct > 0:
            peg = per / eg_pct

    # 거래량 비율 (향후 UI 확장용)
    volume = safe_get_float(info, "volume")
    avg_volume = safe_get_float(info, "averageVolume")
    volume_ratio = None
    if volume is not None and avg_volume is not None and avg_volume > 0:
        volume_ratio = volume / avg_volume

    # 베타 (향후 UI 확장용)
    beta = safe_get_float(info, "beta")

    # --- 52주 위치 (%) ---
    fifty_two_week_pct = None
    if hi is not None and lo is not None and current_price is not None:
        range_val = hi - lo
        if range_val > 0:
            fifty_two_week_pct = (current_price - lo) / range_val * 100

    # --- 점수 계산 ---
    criteria = {}

    if per is not None:
        if per <= 15:
            criteria["PER"] = 1
        elif per >= 30:
            criteria["PER"] = -1
        else:
            criteria["PER"] = 0
    else:
        criteria["PER"] = None

    if pbr is not None:
        if pbr <= 1.5:
            criteria["PBR"] = 1
        elif pbr >= 5:
            criteria["PBR"] = -1
        else:
            criteria["PBR"] = 0
    else:
        criteria["PBR"] = None

    if peg is not None:
        if peg <= 1.0:
            criteria["PEG"] = 1
        elif peg >= 2.0:
            criteria["PEG"] = -1
        else:
            criteria["PEG"] = 0
    else:
        criteria["PEG"] = None

    if ev_ebitda is not None:
        if ev_ebitda <= 10:
            criteria["EV/EBITDA"] = 1
        elif ev_ebitda >= 20:
            criteria["EV/EBITDA"] = -1
        else:
            criteria["EV/EBITDA"] = 0
    else:
        criteria["EV/EBITDA"] = None

    if roe_val is not None:
        roe_pct = roe_val * 100
        if roe_pct >= 15:
            criteria["ROE"] = 1
        elif roe_pct < 5:
            criteria["ROE"] = -1
        else:
            criteria["ROE"] = 0
    else:
        criteria["ROE"] = None

    if debt_equity is not None:
        if debt_equity < 100:
            criteria["부채"] = 1
        elif debt_equity >= 200:
            criteria["부채"] = -1
        else:
            criteria["부채"] = 0
    else:
        criteria["부채"] = None

    if hi is not None and current_price is not None and hi > 0:
        drop_pct = (hi - current_price) / hi * 100
        if drop_pct >= 20:
            criteria["52주"] = 1
        elif drop_pct <= 5:
            criteria["52주"] = -1
        else:
            criteria["52주"] = 0
    else:
        criteria["52주"] = None

    valid_scores = [v for v in criteria.values() if v is not None]
    score = sum(valid_scores)
    total_criteria = len(valid_scores)

    # --- 적정 가격 계산 ---
    fair_prices = []
    revenue_ps = safe_get_float(info, "priceToSalesTrailing12Months")
    fwd_eps = safe_get_float(info, "forwardEps")
    fwd_per = safe_get_float(info, "forwardPE")

    # 1) PER 기반: EPS × 적정 PER
    #    - 업종평균 PER이 있으면 사용
    #    - 없으면 성장률 기반 적정 PER (PEG=1 기준, 상한 30)
    if eps_val is not None and eps_val > 0:
        sector_per = safe_get_float(info, "sectorPE") or safe_get_float(info, "industryPE")
        if sector_per and 5 <= sector_per <= 50:
            target_per = sector_per
        elif earn_growth is not None and earn_growth > 0:
            # PEG = 1 기준: 적정 PER = 성장률(%) × 1.0, 상한 30
            target_per = min(earn_growth * 100, 30)
            target_per = max(target_per, 10)  # 최소 10
        else:
            target_per = 15
        fair_prices.append(eps_val * target_per)

    # 2) Forward PER 기반 (forward EPS가 있는 경우)
    if fwd_eps is not None and fwd_eps > 0 and fwd_per is not None and fwd_per > 0:
        # Forward EPS × 적정 Forward PER (현재 Forward PER의 중앙값 방향 보정)
        target_fwd_per = min(max(fwd_per * 0.8, 10), 30)
        fair_prices.append(fwd_eps * target_fwd_per)

    # 3) PBR 기반 (자산집약 기업만 — PBR < 5일 때)
    #    소프트웨어/테크 등 자산경량 기업은 PBR이 무의미하므로 제외
    if book_val is not None and book_val > 0 and pbr is not None and pbr < 5:
        # ROE 반영: 높은 ROE → 프리미엄 허용
        if roe_val is not None and roe_val > 0:
            # 적정 PBR = ROE / 할인율(10%), 상한 3.0
            fair_pbr = min(roe_val / 0.10, 3.0)
            fair_pbr = max(fair_pbr, 0.8)
        else:
            fair_pbr = 1.0
        fair_prices.append(book_val * fair_pbr)

    # 4) DCF 간이: EPS × (1+g)^5 × 적정PER / (1+r)^5
    #    성장률 상한 50%로 제한 (고성장주 폭주 방지)
    if eps_val is not None and eps_val > 0 and earn_growth is not None:
        growth = max(min(earn_growth, 0.50), -0.30)  # -30% ~ +50% 범위 제한
        discount_rate = 0.10  # 10% 할인율
        terminal_per = 15  # 성숙기 PER
        future_eps = eps_val * ((1 + growth) ** 5)
        dcf_price = future_eps * terminal_per / ((1 + discount_rate) ** 5)
        if dcf_price > 0:
            fair_prices.append(dcf_price)

    # 5) PSR 기반 (매출 기반 — EPS가 없거나 적자인 기업 대비)
    if revenue_ps is not None and revenue_ps > 0 and current_price is not None:
        # 적정 PSR: 업종별 다르지만 일반적으로 2~5 범위
        if earn_growth is not None and earn_growth > 0.2:
            target_ps = min(earn_growth * 100 * 0.15, 8)  # 고성장주 허용 범위 넓음
            target_ps = max(target_ps, 2)
        else:
            target_ps = 2.5
        fair_prices.append(current_price / revenue_ps * target_ps)

    # 가중 평균 (이상치 제거: 중앙값 기준 2배 이상 벗어나는 값 제외)
    if fair_prices:
        median_fp = sorted(fair_prices)[len(fair_prices) // 2]
        filtered = [p for p in fair_prices if 0.3 * median_fp <= p <= 3.0 * median_fp]
        if filtered:
            fair_price = sum(filtered) / len(filtered)
        else:
            fair_price = median_fp
    else:
        fair_price = None

    # --- 종합 판단 ---
    if total_criteria > 0:
        if score >= 3:
            judgment = "저평가"
        elif score <= -3:
            judgment = "고평가"
        else:
            judgment = "적정"
    else:
        judgment = "N/A"

    roe_display = roe_val * 100 if roe_val is not None else None

    return FundamentalData(
        per=per,
        pbr=pbr,
        roe=roe_display,
        score=score,
        total_criteria=total_criteria,
        judgment=judgment,
        fair_price=fair_price,
        fifty_two_week_pct=fifty_two_week_pct,
        criteria=criteria,
        volume_ratio=volume_ratio,
        beta=beta,
    )


def calculate_factor_score(info, momentum_signal="관망"):
    """복합 팩터 모델 점수 (밸류 + 모멘텀 + 퀄리티).

    각 팩터 0~3점, 총합 0~9점.
    - 밸류 팩터: PER, PBR, PEG 기반
    - 모멘텀 팩터: 현재 모멘텀 신호 + 52주 위치 기반
    - 퀄리티 팩터: ROE, 부채비율, 영업이익률 기반

    Returns:
        dict: {"total": 총점, "value": 밸류점수, "momentum": 모멘텀점수,
               "quality": 퀄리티점수, "grade": 등급문자열}
    """
    # --- 밸류 팩터 (0~3) ---
    value_score = 0
    per = safe_get_float(info, "trailingPE")
    pbr = safe_get_float(info, "priceToBook")
    peg = safe_get_float(info, "pegRatio")

    if per is not None and per <= 15:
        value_score += 1
    if pbr is not None and pbr <= 1.5:
        value_score += 1
    if peg is not None and peg <= 1.0:
        value_score += 1

    # --- 모멘텀 팩터 (0~3) ---
    momentum_score = 0
    if "강력 매수" in momentum_signal:
        momentum_score += 3
    elif "매수" in momentum_signal:
        momentum_score += 2
    elif "관망" in momentum_signal:
        momentum_score += 1

    # 52주 위치 보정
    hi = safe_get_float(info, "fiftyTwoWeekHigh")
    lo = safe_get_float(info, "fiftyTwoWeekLow")
    cp = safe_get_float(info, "currentPrice")
    if hi and lo and cp and hi > lo:
        pct = (cp - lo) / (hi - lo)
        if 0.3 <= pct <= 0.7:
            pass  # 중간 → 보정 없음
        elif pct > 0.7:
            momentum_score = min(3, momentum_score)  # 고점 근처 → 그대로
    momentum_score = min(3, momentum_score)

    # --- 퀄리티 팩터 (0~3) ---
    quality_score = 0
    roe = safe_get_float(info, "returnOnEquity")
    debt = safe_get_float(info, "debtToEquity")
    om = safe_get_float(info, "operatingMargins")

    if roe is not None and roe * 100 >= 15:
        quality_score += 1
    if debt is not None and debt < 100:
        quality_score += 1
    if om is not None and om * 100 >= 15:
        quality_score += 1

    total = value_score + momentum_score + quality_score

    if total >= 7:
        grade = "A (우수)"
    elif total >= 5:
        grade = "B (양호)"
    elif total >= 3:
        grade = "C (보통)"
    else:
        grade = "D (부진)"

    return {
        "total": total,
        "value": value_score,
        "momentum": momentum_score,
        "quality": quality_score,
        "grade": grade,
    }


def calculate_piotroski_fscore(info):
    """Piotroski F-Score (9항목 재무 퀄리티 점수).

    각 항목 통과 시 1점, 총합 0~9점.
    - 수익성 (4항목): ROA>0, 영업현금흐름>0, ROA증가, 영업CF>순이익
    - 레버리지/유동성 (3항목): 부채비율감소, 유동비율증가, 무증자
    - 효율성 (2항목): 매출총이익률증가, 자산회전율증가

    yfinance에서 일부 데이터만 제공하므로 가용한 항목만 계산합니다.

    Returns:
        dict: {"score": 0~9, "max_score": 계산가능항목수, "details": {항목: 0/1/None}}
    """
    details = {}

    # 1. ROA > 0 (순이익/총자산)
    net_income = safe_get_float(info, "netIncomeToCommon")
    total_assets = safe_get_float(info, "totalAssets")
    if net_income is not None and total_assets is not None and total_assets > 0:
        roa = net_income / total_assets
        details["ROA>0"] = 1 if roa > 0 else 0
    else:
        details["ROA>0"] = None

    # 2. 영업현금흐름 > 0
    ocf = safe_get_float(info, "operatingCashflow")
    if ocf is not None:
        details["OCF>0"] = 1 if ocf > 0 else 0
    else:
        details["OCF>0"] = None

    # 3. ROA 증가 (대용: 이익성장률 > 0)
    earn_growth = safe_get_float(info, "earningsGrowth")
    if earn_growth is not None:
        details["ROA증가"] = 1 if earn_growth > 0 else 0
    else:
        details["ROA증가"] = None

    # 4. OCF > 순이익 (발생주의 품질)
    if ocf is not None and net_income is not None:
        details["OCF>NI"] = 1 if ocf > net_income else 0
    else:
        details["OCF>NI"] = None

    # 5. 부채비율 < 100% (저부채)
    debt = safe_get_float(info, "debtToEquity")
    if debt is not None:
        details["저부채"] = 1 if debt < 100 else 0
    else:
        details["저부채"] = None

    # 6. 유동비율 >= 1.0
    cr = safe_get_float(info, "currentRatio")
    if cr is not None:
        details["유동성"] = 1 if cr >= 1.0 else 0
    else:
        details["유동성"] = None

    # 7. 주식 희석 없음 (대용: shares outstanding 감소 or 안정)
    # yfinance에서 직접 비교 어려움 → 자사주매입 프로그램 유무로 대체
    buyback = safe_get_float(info, "sharesOutstanding")
    float_shares = safe_get_float(info, "floatShares")
    if buyback is not None and float_shares is not None and buyback > 0:
        details["무희석"] = 1 if float_shares <= buyback else 0
    else:
        details["무희석"] = None

    # 8. 매출총이익률 양호 (대용: Operating Margin > 0)
    om = safe_get_float(info, "operatingMargins")
    if om is not None:
        details["이익률"] = 1 if om > 0 else 0
    else:
        details["이익률"] = None

    # 9. 자산회전율 (대용: 매출성장률 > 0)
    rev_growth = safe_get_float(info, "revenueGrowth")
    if rev_growth is not None:
        details["매출증가"] = 1 if rev_growth > 0 else 0
    else:
        details["매출증가"] = None

    valid = [v for v in details.values() if v is not None]
    score = sum(valid)
    max_score = len(valid)

    return {
        "score": score,
        "max_score": max_score,
        "details": details,
    }
