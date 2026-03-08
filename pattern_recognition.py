"""
차트 패턴 인식 모듈

주가 데이터에서 기술적 차트 패턴을 자동 감지합니다.
지원 패턴: 더블탑, 더블바텀, 헤드앤숄더, 역헤드앤숄더, 상승삼각형, 하락삼각형
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)


def _find_extrema(data, order):
    """로컬 극값(고점/저점) 인덱스를 찾습니다.

    Args:
        data: numpy array of price values
        order: argrelextrema의 비교 범위 (양쪽 각 order개 포인트)

    Returns:
        (max_indices, min_indices) tuple of numpy arrays
    """
    try:
        from scipy.signal import argrelextrema
    except ImportError:
        logger.warning("scipy가 설치되지 않았습니다. 패턴 인식을 사용할 수 없습니다.")
        return np.array([]), np.array([])

    max_indices = argrelextrema(data, np.greater_equal, order=order)[0]
    min_indices = argrelextrema(data, np.less_equal, order=order)[0]

    return max_indices, min_indices


def _pct_diff(a, b):
    """두 값 사이의 백분율 차이를 반환합니다."""
    avg = (a + b) / 2.0
    if avg == 0:
        return 0.0
    return abs(a - b) / avg


def _detect_double_top(highs, lows, closes, max_indices, min_indices, tolerance):
    """더블탑 패턴을 감지합니다.

    두 개의 비슷한 고점 사이에 저점이 존재하는 패턴.
    하락 신호(매도).
    """
    patterns = []

    if len(max_indices) < 2 or len(min_indices) < 1:
        return patterns

    for i in range(len(max_indices) - 1):
        idx1 = max_indices[i]
        idx2 = max_indices[i + 1]
        peak1 = highs[idx1]
        peak2 = highs[idx2]

        # 두 고점이 tolerance 범위 내에서 비슷한지 확인
        if _pct_diff(peak1, peak2) > tolerance:
            continue

        # 두 고점 사이에 저점이 있는지 확인
        troughs_between = min_indices[(min_indices > idx1) & (min_indices < idx2)]
        if len(troughs_between) == 0:
            continue

        trough_idx = troughs_between[np.argmin(lows[troughs_between])]
        trough_val = lows[trough_idx]

        # 저점이 고점 대비 충분히 낮은지 확인 (최소 tolerance 이상 차이)
        avg_peak = (peak1 + peak2) / 2.0
        drop_pct = (avg_peak - trough_val) / avg_peak
        if drop_pct < tolerance:
            continue

        # 현재 가격이 넥라인(저점) 근처 또는 아래인지 확인하여 확정
        current_price = closes[-1]
        neckline = trough_val

        # 신뢰도 계산
        # 1. 두 고점의 유사도 (높을수록 좋음)
        peak_similarity = 1.0 - (_pct_diff(peak1, peak2) / tolerance)
        # 2. 저점의 깊이 (깊을수록 명확한 패턴)
        depth_score = min(drop_pct / (tolerance * 3), 1.0)
        # 3. 패턴 위치 (최근일수록 높은 점수)
        recency = min((idx2 / len(closes)), 1.0)

        confidence = (peak_similarity * 0.4 + depth_score * 0.35 + recency * 0.25)
        confidence = max(0.0, min(1.0, confidence))

        # 넥라인 이탈 시 신뢰도 상승
        if current_price < neckline:
            confidence = min(1.0, confidence + 0.15)

        patterns.append({
            'pattern': '더블탑',
            'pattern_en': 'Double Top',
            'signal': '매도',
            'confidence': round(confidence, 2),
            'start_idx': int(idx1),
            'end_idx': int(idx2),
            'description': (
                f"고점 {peak1:.2f}과 {peak2:.2f}에서 더블탑 형성. "
                f"넥라인 {neckline:.2f}. 하락 반전 가능성."
            )
        })

    return patterns


def _detect_double_bottom(highs, lows, closes, max_indices, min_indices, tolerance):
    """더블바텀 패턴을 감지합니다.

    두 개의 비슷한 저점 사이에 고점이 존재하는 패턴.
    상승 신호(매수).
    """
    patterns = []

    if len(min_indices) < 2 or len(max_indices) < 1:
        return patterns

    for i in range(len(min_indices) - 1):
        idx1 = min_indices[i]
        idx2 = min_indices[i + 1]
        trough1 = lows[idx1]
        trough2 = lows[idx2]

        # 두 저점이 tolerance 범위 내에서 비슷한지 확인
        if _pct_diff(trough1, trough2) > tolerance:
            continue

        # 두 저점 사이에 고점이 있는지 확인
        peaks_between = max_indices[(max_indices > idx1) & (max_indices < idx2)]
        if len(peaks_between) == 0:
            continue

        peak_idx = peaks_between[np.argmax(highs[peaks_between])]
        peak_val = highs[peak_idx]

        # 고점이 저점 대비 충분히 높은지 확인
        avg_trough = (trough1 + trough2) / 2.0
        if avg_trough == 0:
            continue
        rise_pct = (peak_val - avg_trough) / avg_trough
        if rise_pct < tolerance:
            continue

        current_price = closes[-1]
        neckline = peak_val

        # 신뢰도 계산
        trough_similarity = 1.0 - (_pct_diff(trough1, trough2) / tolerance)
        depth_score = min(rise_pct / (tolerance * 3), 1.0)
        recency = min((idx2 / len(closes)), 1.0)

        confidence = (trough_similarity * 0.4 + depth_score * 0.35 + recency * 0.25)
        confidence = max(0.0, min(1.0, confidence))

        if current_price > neckline:
            confidence = min(1.0, confidence + 0.15)

        patterns.append({
            'pattern': '더블바텀',
            'pattern_en': 'Double Bottom',
            'signal': '매수',
            'confidence': round(confidence, 2),
            'start_idx': int(idx1),
            'end_idx': int(idx2),
            'description': (
                f"저점 {trough1:.2f}과 {trough2:.2f}에서 더블바텀 형성. "
                f"넥라인 {neckline:.2f}. 상승 반전 가능성."
            )
        })

    return patterns


def _detect_head_and_shoulders(highs, lows, closes, max_indices, min_indices, tolerance):
    """헤드앤숄더 패턴을 감지합니다.

    세 개의 고점 중 가운데가 가장 높은 패턴.
    하락 신호(매도).
    """
    patterns = []

    if len(max_indices) < 3 or len(min_indices) < 2:
        return patterns

    for i in range(len(max_indices) - 2):
        left_idx = max_indices[i]
        head_idx = max_indices[i + 1]
        right_idx = max_indices[i + 2]

        left_peak = highs[left_idx]
        head_peak = highs[head_idx]
        right_peak = highs[right_idx]

        # 헤드가 양쪽 어깨보다 최소 tolerance*2 이상 높아야 함
        if head_peak <= left_peak * (1 + tolerance * 2):
            continue
        if head_peak <= right_peak * (1 + tolerance * 2):
            continue

        # 양쪽 어깨가 비슷한 높이인지 확인
        if _pct_diff(left_peak, right_peak) > tolerance * 2:
            continue

        # 어깨와 헤드 사이에 저점(넥라인) 확인
        troughs_left = min_indices[(min_indices > left_idx) & (min_indices < head_idx)]
        troughs_right = min_indices[(min_indices > head_idx) & (min_indices < right_idx)]

        if len(troughs_left) == 0 or len(troughs_right) == 0:
            continue

        neckline_left_idx = troughs_left[np.argmin(lows[troughs_left])]
        neckline_right_idx = troughs_right[np.argmin(lows[troughs_right])]
        neckline_left = lows[neckline_left_idx]
        neckline_right = lows[neckline_right_idx]
        neckline = (neckline_left + neckline_right) / 2.0

        current_price = closes[-1]

        # 신뢰도 계산
        shoulder_similarity = 1.0 - (_pct_diff(left_peak, right_peak) / (tolerance * 2))
        head_prominence = min(
            ((head_peak - left_peak) / left_peak) / (tolerance * 4), 1.0
        )
        neckline_flatness = 1.0 - min(_pct_diff(neckline_left, neckline_right) / tolerance, 1.0)
        recency = min((right_idx / len(closes)), 1.0)

        confidence = (
            shoulder_similarity * 0.3
            + head_prominence * 0.25
            + neckline_flatness * 0.2
            + recency * 0.25
        )
        confidence = max(0.0, min(1.0, confidence))

        if current_price < neckline:
            confidence = min(1.0, confidence + 0.15)

        patterns.append({
            'pattern': '헤드앤숄더',
            'pattern_en': 'Head and Shoulders',
            'signal': '매도',
            'confidence': round(confidence, 2),
            'start_idx': int(left_idx),
            'end_idx': int(right_idx),
            'description': (
                f"왼쪽어깨 {left_peak:.2f}, 헤드 {head_peak:.2f}, "
                f"오른쪽어깨 {right_peak:.2f}. "
                f"넥라인 {neckline:.2f}. 하락 반전 가능성."
            )
        })

    return patterns


def _detect_inverse_head_and_shoulders(highs, lows, closes, max_indices, min_indices, tolerance):
    """역헤드앤숄더 패턴을 감지합니다.

    세 개의 저점 중 가운데가 가장 낮은 패턴.
    상승 신호(매수).
    """
    patterns = []

    if len(min_indices) < 3 or len(max_indices) < 2:
        return patterns

    for i in range(len(min_indices) - 2):
        left_idx = min_indices[i]
        head_idx = min_indices[i + 1]
        right_idx = min_indices[i + 2]

        left_trough = lows[left_idx]
        head_trough = lows[head_idx]
        right_trough = lows[right_idx]

        # 헤드가 양쪽 어깨보다 최소 tolerance*2 이상 낮아야 함
        if head_trough >= left_trough * (1 - tolerance * 2):
            continue
        if head_trough >= right_trough * (1 - tolerance * 2):
            continue

        # 양쪽 어깨가 비슷한 높이인지 확인
        if _pct_diff(left_trough, right_trough) > tolerance * 2:
            continue

        # 어깨와 헤드 사이에 고점(넥라인) 확인
        peaks_left = max_indices[(max_indices > left_idx) & (max_indices < head_idx)]
        peaks_right = max_indices[(max_indices > head_idx) & (max_indices < right_idx)]

        if len(peaks_left) == 0 or len(peaks_right) == 0:
            continue

        neckline_left_idx = peaks_left[np.argmax(highs[peaks_left])]
        neckline_right_idx = peaks_right[np.argmax(highs[peaks_right])]
        neckline_left = highs[neckline_left_idx]
        neckline_right = highs[neckline_right_idx]
        neckline = (neckline_left + neckline_right) / 2.0

        current_price = closes[-1]

        # 신뢰도 계산
        shoulder_similarity = 1.0 - (_pct_diff(left_trough, right_trough) / (tolerance * 2))
        head_prominence = min(
            ((left_trough - head_trough) / left_trough) / (tolerance * 4), 1.0
        )
        neckline_flatness = 1.0 - min(_pct_diff(neckline_left, neckline_right) / tolerance, 1.0)
        recency = min((right_idx / len(closes)), 1.0)

        confidence = (
            shoulder_similarity * 0.3
            + head_prominence * 0.25
            + neckline_flatness * 0.2
            + recency * 0.25
        )
        confidence = max(0.0, min(1.0, confidence))

        if current_price > neckline:
            confidence = min(1.0, confidence + 0.15)

        patterns.append({
            'pattern': '역헤드앤숄더',
            'pattern_en': 'Inverse Head and Shoulders',
            'signal': '매수',
            'confidence': round(confidence, 2),
            'start_idx': int(left_idx),
            'end_idx': int(right_idx),
            'description': (
                f"왼쪽어깨 {left_trough:.2f}, 헤드 {head_trough:.2f}, "
                f"오른쪽어깨 {right_trough:.2f}. "
                f"넥라인 {neckline:.2f}. 상승 반전 가능성."
            )
        })

    return patterns


def _detect_ascending_triangle(highs, lows, closes, max_indices, min_indices, tolerance):
    """상승삼각형 패턴을 감지합니다.

    저점은 점점 높아지고 고점은 수평인 패턴.
    상승 돌파 신호(매수).
    """
    patterns = []

    if len(max_indices) < 3 or len(min_indices) < 3:
        return patterns

    # 최근 극값들로 판단 (마지막 5개씩 사용)
    recent_max = max_indices[-5:] if len(max_indices) >= 5 else max_indices
    recent_min = min_indices[-5:] if len(min_indices) >= 5 else min_indices

    if len(recent_max) < 3 or len(recent_min) < 3:
        return patterns

    peak_values = highs[recent_max]
    trough_values = lows[recent_min]

    # 고점이 수평인지 확인 (모든 고점 쌍의 차이가 tolerance 이내)
    peak_range = (np.max(peak_values) - np.min(peak_values))
    avg_peak = np.mean(peak_values)
    if avg_peak == 0:
        return patterns
    flat_highs = (peak_range / avg_peak) <= tolerance

    # 저점이 상승하는지 확인 (선형 회귀 기울기 양수)
    if len(trough_values) >= 2:
        x = np.arange(len(trough_values))
        slope = np.polyfit(x, trough_values, 1)[0]
        rising_lows = slope > 0
    else:
        return patterns

    if not (flat_highs and rising_lows):
        return patterns

    # 신뢰도 계산
    # 1. 고점 수평도
    flatness_score = 1.0 - min((peak_range / avg_peak) / tolerance, 1.0)
    # 2. 저점 상승 기울기의 강도
    avg_trough = np.mean(trough_values)
    if avg_trough == 0:
        return patterns
    slope_normalized = (slope / avg_trough) * len(trough_values)
    slope_score = min(abs(slope_normalized) / tolerance, 1.0)
    # 3. 극값 개수 (많을수록 신뢰도 높음)
    count_score = min((len(recent_max) + len(recent_min)) / 8.0, 1.0)

    confidence = (flatness_score * 0.35 + slope_score * 0.35 + count_score * 0.3)
    confidence = max(0.0, min(1.0, confidence))

    # 현재 가격이 저항선 돌파 시 신뢰도 상승
    resistance = avg_peak
    current_price = closes[-1]
    if current_price > resistance:
        confidence = min(1.0, confidence + 0.15)

    patterns.append({
        'pattern': '상승삼각형',
        'pattern_en': 'Ascending Triangle',
        'signal': '매수',
        'confidence': round(confidence, 2),
        'start_idx': int(min(recent_max[0], recent_min[0])),
        'end_idx': int(max(recent_max[-1], recent_min[-1])),
        'description': (
            f"저항선 {resistance:.2f} 부근에서 수평, "
            f"지지선 상승 중. 상방 돌파 가능성."
        )
    })

    return patterns


def _detect_descending_triangle(highs, lows, closes, max_indices, min_indices, tolerance):
    """하락삼각형 패턴을 감지합니다.

    고점은 점점 낮아지고 저점은 수평인 패턴.
    하락 돌파 신호(매도).
    """
    patterns = []

    if len(max_indices) < 3 or len(min_indices) < 3:
        return patterns

    recent_max = max_indices[-5:] if len(max_indices) >= 5 else max_indices
    recent_min = min_indices[-5:] if len(min_indices) >= 5 else min_indices

    if len(recent_max) < 3 or len(recent_min) < 3:
        return patterns

    peak_values = highs[recent_max]
    trough_values = lows[recent_min]

    # 저점이 수평인지 확인
    trough_range = (np.max(trough_values) - np.min(trough_values))
    avg_trough = np.mean(trough_values)
    if avg_trough == 0:
        return patterns
    flat_lows = (trough_range / avg_trough) <= tolerance

    # 고점이 하락하는지 확인
    if len(peak_values) >= 2:
        x = np.arange(len(peak_values))
        slope = np.polyfit(x, peak_values, 1)[0]
        falling_highs = slope < 0
    else:
        return patterns

    if not (flat_lows and falling_highs):
        return patterns

    # 신뢰도 계산
    flatness_score = 1.0 - min((trough_range / avg_trough) / tolerance, 1.0)
    avg_peak = np.mean(peak_values)
    if avg_peak == 0:
        return patterns
    slope_normalized = (slope / avg_peak) * len(peak_values)
    slope_score = min(abs(slope_normalized) / tolerance, 1.0)
    count_score = min((len(recent_max) + len(recent_min)) / 8.0, 1.0)

    confidence = (flatness_score * 0.35 + slope_score * 0.35 + count_score * 0.3)
    confidence = max(0.0, min(1.0, confidence))

    # 현재 가격이 지지선 이탈 시 신뢰도 상승
    support = avg_trough
    current_price = closes[-1]
    if current_price < support:
        confidence = min(1.0, confidence + 0.15)

    patterns.append({
        'pattern': '하락삼각형',
        'pattern_en': 'Descending Triangle',
        'signal': '매도',
        'confidence': round(confidence, 2),
        'start_idx': int(min(recent_max[0], recent_min[0])),
        'end_idx': int(max(recent_max[-1], recent_min[-1])),
        'description': (
            f"지지선 {support:.2f} 부근에서 수평, "
            f"저항선 하락 중. 하방 이탈 가능성."
        )
    })

    return patterns


def detect_patterns(historical_data, order=10, tolerance=0.03):
    """주가 데이터에서 차트 패턴을 감지합니다.

    Args:
        historical_data: pandas DataFrame with 'High', 'Low', 'Close' columns
        order: 극값 감지 시 양쪽 비교 포인트 수 (기본값 10)
        tolerance: 가격 유사도 허용 범위 (비율, 기본값 3%)

    Returns:
        list of dict, each with:
            - 'pattern': str (한국어 패턴명)
            - 'pattern_en': str (영어 패턴명)
            - 'signal': str ('매수' or '매도' or '중립')
            - 'confidence': float (0.0 ~ 1.0)
            - 'start_idx': int (패턴 시작 인덱스)
            - 'end_idx': int (패턴 종료 인덱스)
            - 'description': str (한국어 설명)
    """
    if historical_data is None or len(historical_data) < order * 3:
        logger.warning("패턴 인식에 필요한 데이터가 부족합니다.")
        return []

    try:
        highs = historical_data['High'].values.astype(float)
        lows = historical_data['Low'].values.astype(float)
        closes = historical_data['Close'].values.astype(float)
    except (KeyError, TypeError) as e:
        logger.error(f"데이터 컬럼 접근 오류: {e}")
        return []

    if len(highs) == 0:
        return []

    max_indices, min_indices = _find_extrema(closes, order)

    if len(max_indices) == 0 and len(min_indices) == 0:
        return []

    all_patterns = []

    # 각 패턴 감지 함수 실행
    detectors = [
        _detect_double_top,
        _detect_double_bottom,
        _detect_head_and_shoulders,
        _detect_inverse_head_and_shoulders,
        _detect_ascending_triangle,
        _detect_descending_triangle,
    ]

    for detector in detectors:
        try:
            found = detector(highs, lows, closes, max_indices, min_indices, tolerance)
            all_patterns.extend(found)
        except Exception as e:
            logger.error(f"패턴 감지 오류 ({detector.__name__}): {e}")

    # 신뢰도 내림차순 정렬
    all_patterns.sort(key=lambda p: p['confidence'], reverse=True)

    return all_patterns


def get_pattern_summary(historical_data, order=10, tolerance=0.03):
    """메인 테이블 표시용 짧은 패턴 요약 문자열을 반환합니다.

    Returns:
        str: 예) "더블바텀↑", "H&S↓", "-" (패턴 없음)
    """
    try:
        patterns = detect_patterns(historical_data, order=order, tolerance=tolerance)
    except Exception as e:
        logger.error(f"패턴 요약 생성 오류: {e}")
        return "-"

    if not patterns:
        return "-"

    # 가장 신뢰도 높은 패턴 선택
    best = patterns[0]

    # 짧은 표시명 매핑
    short_names = {
        '더블탑': '더블탑',
        '더블바텀': '더블바텀',
        '헤드앤숄더': 'H&S',
        '역헤드앤숄더': '역H&S',
        '상승삼각형': '상승△',
        '하락삼각형': '하락△',
    }

    name = short_names.get(best['pattern'], best['pattern'])
    arrow = '↑' if best['signal'] == '매수' else '↓' if best['signal'] == '매도' else ''

    return f"{name}{arrow}"
