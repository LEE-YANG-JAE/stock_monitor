"""
SQLite 기반 yfinance 데이터 캐싱 모듈.

yfinance 호출을 캐싱하여 네트워크 요청을 최소화합니다.
캐시 히트 시 저장된 데이터를 반환하고, 미스 또는 만료 시 델타 업데이트를 수행합니다.
"""

import json
import os
import sqlite3
import threading
import time
import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# DB 경로: 이 스크립트와 같은 디렉토리
_DB_DIR = os.path.dirname(os.path.abspath(__file__))
_DB_PATH = os.path.join(_DB_DIR, "stock_data_cache.db")

# 캐시 TTL (초)
DEFAULT_TTL_INTRADAY = 60       # 인트라데이 (1m, 5m, 15m 등): 60초
DEFAULT_TTL_DAILY = 3600        # 일봉 이상 (1d, 1wk, 1mo): 1시간

# 인트라데이 인터벌 목록
_INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}

# 스레드 안전을 위한 락
_db_lock = threading.Lock()

# 캐시 통계
_stats = {
    "hits": 0,
    "misses": 0,
    "delta_updates": 0,
    "errors": 0,
}
_stats_lock = threading.Lock()


def _get_ttl(interval: str) -> float:
    """인터벌에 따른 TTL 반환."""
    if interval in _INTRADAY_INTERVALS:
        return DEFAULT_TTL_INTRADAY
    return DEFAULT_TTL_DAILY


def _get_connection() -> sqlite3.Connection:
    """SQLite 연결 생성 및 테이블 초기화."""
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_cache (
            ticker TEXT,
            interval TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (ticker, interval, date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            ticker TEXT,
            interval TEXT,
            last_updated REAL,
            period TEXT,
            PRIMARY KEY (ticker, interval)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamental_cache (
            ticker TEXT PRIMARY KEY,
            info_json TEXT,
            last_updated REAL
        )
    """)
    conn.commit()
    return conn


# 모듈 레벨 연결 (lazy init)
_conn: sqlite3.Connection = None
_conn_init_lock = threading.Lock()


def _ensure_conn() -> sqlite3.Connection:
    """연결이 없으면 생성."""
    global _conn
    if _conn is None:
        with _conn_init_lock:
            if _conn is None:
                _conn = _get_connection()
    return _conn


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance MultiIndex 컬럼을 단일 레벨로 변환."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """컬럼명을 표준화 (Open, High, Low, Close, Volume)."""
    col_map = {}
    for col in df.columns:
        lower = col.lower().strip()
        if lower == "open":
            col_map[col] = "Open"
        elif lower == "high":
            col_map[col] = "High"
        elif lower == "low":
            col_map[col] = "Low"
        elif lower == "close":
            col_map[col] = "Close"
        elif lower == "volume":
            col_map[col] = "Volume"
        elif lower == "adj close":
            col_map[col] = "Adj Close"
    if col_map:
        df = df.rename(columns=col_map)
    return df


def _df_to_rows(df: pd.DataFrame, ticker: str, interval: str) -> list:
    """DataFrame을 SQLite INSERT용 row 리스트로 변환."""
    rows = []
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(df.columns)):
        return rows
    for idx, row in df.iterrows():
        date_str = str(idx)
        rows.append((
            ticker, interval, date_str,
            float(row["Open"]) if pd.notna(row["Open"]) else None,
            float(row["High"]) if pd.notna(row["High"]) else None,
            float(row["Low"]) if pd.notna(row["Low"]) else None,
            float(row["Close"]) if pd.notna(row["Close"]) else None,
            float(row["Volume"]) if pd.notna(row["Volume"]) else 0,
        ))
    return rows


def _rows_to_df(rows: list) -> pd.DataFrame:
    """SQLite row 리스트를 yfinance 호환 DataFrame으로 변환."""
    if not rows:
        return pd.DataFrame()

    data = []
    for row in rows:
        # row: (ticker, interval, date, open, high, low, close, volume)
        data.append({
            "Date": row[2],
            "Open": row[3],
            "High": row[4],
            "Low": row[5],
            "Close": row[6],
            "Volume": row[7],
        })

    df = pd.DataFrame(data)
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    df = df.set_index("Date")
    df = df.sort_index()
    return df


def _store_data(conn: sqlite3.Connection, ticker: str, interval: str,
                df: pd.DataFrame, period: str = None):
    """DataFrame을 SQLite에 저장 (UPSERT)."""
    rows = _df_to_rows(df, ticker, interval)
    if not rows:
        return

    conn.executemany("""
        INSERT OR REPLACE INTO price_cache
            (ticker, interval, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)

    conn.execute("""
        INSERT OR REPLACE INTO cache_meta (ticker, interval, last_updated, period)
        VALUES (?, ?, ?, ?)
    """, (ticker, interval, time.time(), period or ""))

    conn.commit()


def _load_cached(conn: sqlite3.Connection, ticker: str,
                 interval: str) -> pd.DataFrame:
    """SQLite에서 캐시된 데이터 로드."""
    cursor = conn.execute("""
        SELECT ticker, interval, date, open, high, low, close, volume
        FROM price_cache
        WHERE ticker = ? AND interval = ?
        ORDER BY date
    """, (ticker, interval))
    rows = cursor.fetchall()
    return _rows_to_df(rows)


def _get_meta(conn: sqlite3.Connection, ticker: str,
              interval: str) -> dict:
    """캐시 메타데이터 조회."""
    cursor = conn.execute("""
        SELECT last_updated, period
        FROM cache_meta
        WHERE ticker = ? AND interval = ?
    """, (ticker, interval))
    row = cursor.fetchone()
    if row:
        return {"last_updated": row[0], "period": row[1]}
    return None


def _is_fresh(meta: dict, interval: str, ttl: float = None) -> bool:
    """캐시가 아직 유효한지 확인."""
    if meta is None:
        return False
    if ttl is None:
        ttl = _get_ttl(interval)
    age = time.time() - meta["last_updated"]
    return age < ttl


def _increment_stat(key: str):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + 1


def get_cached_history(ticker: str, period: str = None, interval: str = None,
                       start=None, end=None, ttl: float = None) -> pd.DataFrame:
    """
    캐시된 yfinance 히스토리 데이터를 반환합니다.

    캐시 히트 시 저장된 DataFrame을 반환하고,
    만료 또는 미스 시 yfinance에서 다운로드하여 캐시에 저장 후 반환합니다.
    델타 업데이트: 기존 캐시가 있으면 마지막 날짜 이후 데이터만 추가 다운로드.

    Parameters
    ----------
    ticker : str
        종목 티커 심볼 (예: "AAPL")
    period : str, optional
        yfinance period (예: "1mo", "3mo", "1y")
    interval : str, optional
        yfinance interval (예: "1d", "5m"). 기본값 "1d"
    start : str or datetime, optional
        시작 날짜
    end : str or datetime, optional
        종료 날짜
    ttl : float, optional
        캐시 TTL (초). None이면 인터벌에 따라 자동 결정.

    Returns
    -------
    pd.DataFrame
        yfinance .history() 호환 DataFrame (Open, High, Low, Close, Volume)
    """
    if interval is None:
        interval = "1d"

    conn = _ensure_conn()

    with _db_lock:
        meta = _get_meta(conn, ticker, interval)

        # 캐시가 신선하면 바로 반환
        if _is_fresh(meta, interval, ttl):
            cached_df = _load_cached(conn, ticker, interval)
            if not cached_df.empty:
                logger.debug(f"[CACHE HIT] {ticker} ({interval}) - "
                             f"age: {time.time() - meta['last_updated']:.0f}s")
                _increment_stat("hits")

                # start/end 필터링
                if start is not None:
                    start_ts = pd.Timestamp(start, tz="UTC")
                    cached_df = cached_df[cached_df.index >= start_ts]
                if end is not None:
                    end_ts = pd.Timestamp(end, tz="UTC")
                    cached_df = cached_df[cached_df.index <= end_ts]

                return cached_df

    # 캐시 미스 또는 만료 - yfinance에서 다운로드
    logger.debug(f"[CACHE MISS] {ticker} ({interval}) - downloading...")
    _increment_stat("misses")

    try:
        # 델타 업데이트 시도: 기존 캐시가 있고 period 모드인 경우
        existing_df = pd.DataFrame()
        if meta is not None and start is None and end is None and period is not None:
            with _db_lock:
                existing_df = _load_cached(conn, ticker, interval)

        if not existing_df.empty and interval not in _INTRADAY_INTERVALS:
            # 마지막 캐시 날짜부터 델타 다운로드
            last_date = existing_df.index[-1]
            delta_start = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")

            if delta_start < today:
                logger.debug(f"[DELTA UPDATE] {ticker} ({interval}) "
                             f"from {delta_start}")
                _increment_stat("delta_updates")

                try:
                    delta_df = yf.download(
                        ticker, start=delta_start, end=today,
                        interval=interval, progress=False
                    )
                    delta_df = _flatten_columns(delta_df)
                    delta_df = _normalize_columns(delta_df)

                    if not delta_df.empty:
                        # 기존 + 델타 병합
                        merged = pd.concat([existing_df, delta_df])
                        merged = merged[~merged.index.duplicated(keep="last")]
                        merged = merged.sort_index()

                        with _db_lock:
                            _store_data(conn, ticker, interval, merged, period)
                        result = merged
                    else:
                        # 새 데이터 없음, 메타만 갱신
                        with _db_lock:
                            conn.execute("""
                                INSERT OR REPLACE INTO cache_meta
                                    (ticker, interval, last_updated, period)
                                VALUES (?, ?, ?, ?)
                            """, (ticker, interval, time.time(), period or ""))
                            conn.commit()
                        result = existing_df
                except Exception as e:
                    logger.warning(f"[DELTA FAIL] {ticker}: {e}, full download")
                    result = _full_download(ticker, period, interval,
                                            start, end, conn)
            else:
                # 이미 최신, 메타만 갱신
                with _db_lock:
                    conn.execute("""
                        INSERT OR REPLACE INTO cache_meta
                            (ticker, interval, last_updated, period)
                        VALUES (?, ?, ?, ?)
                    """, (ticker, interval, time.time(), period or ""))
                    conn.commit()
                result = existing_df
        else:
            # 전체 다운로드
            result = _full_download(ticker, period, interval, start, end, conn)

        # start/end 필터링
        if not result.empty:
            if start is not None:
                start_ts = pd.Timestamp(start, tz="UTC")
                result = result[result.index >= start_ts]
            if end is not None:
                end_ts = pd.Timestamp(end, tz="UTC")
                result = result[result.index <= end_ts]

        return result

    except Exception as e:
        logger.error(f"[CACHE ERROR] {ticker}: {e}")
        _increment_stat("errors")
        # 캐시에 뭐라도 있으면 반환
        with _db_lock:
            fallback = _load_cached(conn, ticker, interval)
        if not fallback.empty:
            logger.info(f"[CACHE FALLBACK] Returning stale data for {ticker}")
            return fallback
        return pd.DataFrame()


def _full_download(ticker: str, period: str, interval: str,
                   start, end, conn: sqlite3.Connection) -> pd.DataFrame:
    """yfinance에서 전체 데이터 다운로드 후 캐시에 저장."""
    kwargs = {"progress": False}

    if start is not None and end is not None:
        kwargs["start"] = str(start)
        kwargs["end"] = str(end)
    elif start is not None:
        kwargs["start"] = str(start)
    elif period is not None:
        kwargs["period"] = period
    else:
        kwargs["period"] = "1mo"

    kwargs["interval"] = interval

    df = yf.download(ticker, **kwargs)
    df = _flatten_columns(df)
    df = _normalize_columns(df)

    if df.empty:
        return df

    with _db_lock:
        _store_data(conn, ticker, interval, df, period)

    return df


def clear_cache(ticker: str = None):
    """
    캐시를 삭제합니다.

    Parameters
    ----------
    ticker : str, optional
        특정 종목만 삭제. None이면 전체 삭제.
    """
    conn = _ensure_conn()

    with _db_lock:
        if ticker is None:
            conn.execute("DELETE FROM price_cache")
            conn.execute("DELETE FROM cache_meta")
            logger.info("[CACHE] All cache cleared")
        else:
            conn.execute(
                "DELETE FROM price_cache WHERE ticker = ?", (ticker,))
            conn.execute(
                "DELETE FROM cache_meta WHERE ticker = ?", (ticker,))
            logger.info(f"[CACHE] Cache cleared for {ticker}")
        conn.commit()


# ============================================================
# 펀더멘털 캐시 (yfinance Ticker.info)
# ============================================================

FUNDAMENTAL_TTL = 86400  # 24시간


def get_cached_fundamental(ticker: str, ttl: float = None) -> dict:
    """캐시된 펀더멘털 데이터 반환. 만료 또는 미스 시 None."""
    if ttl is None:
        ttl = FUNDAMENTAL_TTL
    conn = _ensure_conn()
    with _db_lock:
        cursor = conn.execute(
            "SELECT info_json, last_updated FROM fundamental_cache WHERE ticker = ?",
            (ticker,))
        row = cursor.fetchone()
    if row is None:
        return None
    age = time.time() - row[1]
    if age > ttl:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def store_fundamental(ticker: str, info: dict):
    """펀더멘털 데이터를 캐시에 저장."""
    conn = _ensure_conn()
    try:
        info_json = json.dumps(info, default=str, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.warning(f"[CACHE] Failed to serialize fundamental for {ticker}: {e}")
        return
    with _db_lock:
        conn.execute("""
            INSERT OR REPLACE INTO fundamental_cache (ticker, info_json, last_updated)
            VALUES (?, ?, ?)
        """, (ticker, info_json, time.time()))
        conn.commit()


def get_cached_fundamental_or_fetch(ticker: str, ttl: float = None) -> dict:
    """캐시에서 펀더멘털 조회. 미스 시 yfinance 호출 후 캐싱."""
    cached = get_cached_fundamental(ticker, ttl)
    if cached is not None:
        _increment_stat("hits")
        return cached
    _increment_stat("misses")
    try:
        info = yf.Ticker(ticker).info
        if info and info.get("quoteType") != "NONE":
            store_fundamental(ticker, info)
            return info
    except Exception as e:
        logger.warning(f"[CACHE] Failed to fetch fundamental for {ticker}: {e}")
        _increment_stat("errors")
    return {}


def get_cache_stats() -> dict:
    """
    캐시 통계를 반환합니다.

    Returns
    -------
    dict
        hits, misses, delta_updates, errors, hit_rate, db_size_mb,
        cached_tickers, total_rows
    """
    conn = _ensure_conn()

    with _stats_lock:
        stats = dict(_stats)

    total = stats["hits"] + stats["misses"]
    stats["hit_rate"] = (stats["hits"] / total * 100) if total > 0 else 0.0

    # DB 파일 크기
    try:
        stats["db_size_mb"] = os.path.getsize(_DB_PATH) / (1024 * 1024)
    except OSError:
        stats["db_size_mb"] = 0.0

    # 캐시된 종목 수 및 총 행 수
    with _db_lock:
        cursor = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM price_cache")
        stats["cached_tickers"] = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM price_cache")
        stats["total_rows"] = cursor.fetchone()[0]

    return stats
