# holdings_manager.py — 보유 종목 관리 모듈 (거래 내역 기반)
# holdings.json CRUD + 포트폴리오 요약 계산

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

HOLDINGS_FILE = "holdings.json"
_holdings_lock = threading.Lock()


def load_holdings():
    """holdings.json 로드. 파일 없으면 빈 dict 반환.
    구형식(transactions 키 없음)이면 자동 마이그레이션."""
    with _holdings_lock:
        if not os.path.exists(HOLDINGS_FILE):
            return {}
        try:
            with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            # 구형식 마이그레이션
            migrated = False
            for ticker, info in data.items():
                if isinstance(info, dict) and "transactions" not in info:
                    data[ticker] = _migrate_legacy_holding(info)
                    migrated = True
            if migrated:
                _save_holdings_unlocked(data)
            return data
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"[HOLDINGS] Load error: {e}")
            return {}


def _migrate_legacy_holding(info):
    """구형식(quantity, avg_price) → 신형식(transactions) 변환."""
    qty = info.get("quantity", 0)
    avg_price = info.get("avg_price", 0)
    date = info.get("purchase_date", "")
    notes = info.get("notes", "")
    transactions = []
    if qty > 0 and avg_price > 0:
        transactions.append({
            "type": "buy",
            "quantity": qty,
            "price": avg_price,
            "date": date,
            "notes": notes if notes else "기존 데이터 마이그레이션",
        })
    return {"transactions": transactions}


def _save_holdings_unlocked(holdings):
    """holdings.json에 atomic write (lock 없이 — 호출자가 lock 관리)."""
    try:
        dir_name = os.path.dirname(os.path.abspath(HOLDINGS_FILE))
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(holdings, f, indent=4, ensure_ascii=False)
            os.replace(tmp_path, HOLDINGS_FILE)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    except Exception as e:
        logging.error(f"[HOLDINGS] Save error: {e}")


def save_holdings(holdings):
    """holdings.json에 atomic write."""
    with _holdings_lock:
        _save_holdings_unlocked(holdings)


def calculate_position(transactions):
    """거래 리스트 → {quantity, avg_price, total_realized_pnl} 계산 (이동평균법).
    매수: 새 평균 = (기존수량 × 기존평균 + 매수수량 × 매수가) / (기존수량 + 매수수량)
    매도: 평균단가 변동 없음, 실현손익 = (매도가 - 평균단가) × 매도수량
    """
    qty = 0.0
    avg_price = 0.0
    total_realized_pnl = 0.0

    for tx in transactions:
        tx_type = tx.get("type", "buy")
        tx_qty = tx.get("quantity", 0)
        tx_price = tx.get("price", 0)

        if tx_type == "buy":
            if qty + tx_qty > 0:
                avg_price = (qty * avg_price + tx_qty * tx_price) / (qty + tx_qty)
            qty += tx_qty
        elif tx_type == "sell":
            if tx_qty > 0 and avg_price > 0:
                total_realized_pnl += (tx_price - avg_price) * tx_qty
            qty -= tx_qty
            # 매도 시 평균단가 변동 없음
            if qty <= 0:
                qty = 0
                avg_price = 0

    return {
        "quantity": qty,
        "avg_price": round(avg_price, 4) if avg_price else 0,
        "total_realized_pnl": round(total_realized_pnl, 2),
    }


def get_holding(holdings, ticker):
    """특정 티커의 보유 정보 반환. transactions에서 자동 계산.
    반환: {quantity, avg_price, total_realized_pnl, transactions} 또는 None.
    """
    info = holdings.get(ticker)
    if info is None:
        return None
    transactions = info.get("transactions", [])
    if not transactions:
        return None
    pos = calculate_position(transactions)
    pos["transactions"] = transactions
    return pos


def add_transaction(holdings, ticker, tx_type, qty, price, date="", notes=""):
    """매수/매도 거래 추가. 매도 시 보유수량 초과 검증.
    Returns: (success: bool, error_msg: str or None)
    """
    if ticker not in holdings:
        holdings[ticker] = {"transactions": []}
    elif "transactions" not in holdings[ticker]:
        holdings[ticker] = _migrate_legacy_holding(holdings[ticker])

    if tx_type == "sell":
        pos = calculate_position(holdings[ticker]["transactions"])
        if qty > pos["quantity"]:
            return False, f"매도 수량({qty})이 보유 수량({pos['quantity']:g})을 초과합니다."

    holdings[ticker]["transactions"].append({
        "type": tx_type,
        "quantity": qty,
        "price": price,
        "date": date,
        "notes": notes,
    })
    return True, None


def remove_transaction(holdings, ticker, tx_index):
    """특정 거래 삭제. 삭제 후 포지션이 음수가 되는지 검증.
    Returns: (success: bool, error_msg: str or None)
    """
    if ticker not in holdings:
        return False, "종목을 찾을 수 없습니다."
    transactions = holdings[ticker].get("transactions", [])
    if tx_index < 0 or tx_index >= len(transactions):
        return False, "유효하지 않은 거래 인덱스입니다."

    # 삭제 후 시뮬레이션
    test_txs = transactions[:tx_index] + transactions[tx_index + 1:]
    test_pos = calculate_position(test_txs)
    if test_pos["quantity"] < 0:
        return False, "이 거래를 삭제하면 보유 수량이 음수가 됩니다."

    transactions.pop(tx_index)
    # 거래가 모두 삭제되면 종목 제거
    if not transactions:
        holdings.pop(ticker, None)
    return True, None


def remove_holding(holdings, ticker):
    """보유 정보 제거 (전체 거래 삭제)."""
    holdings.pop(ticker, None)
    return holdings


def calculate_portfolio_summary(holdings, current_prices):
    """포트폴리오 요약 계산.
    current_prices: {ticker: float} 현재가 딕셔너리
    반환: {total_value, total_cost, total_pnl, total_pnl_pct, total_realized_pnl, positions: [...]}
    """
    positions = []
    total_value = 0.0
    total_cost = 0.0
    total_realized_pnl = 0.0

    for ticker, info in holdings.items():
        holding = get_holding(holdings, ticker)
        if holding is None:
            continue
        qty = holding["quantity"]
        avg_price = holding["avg_price"]
        realized_pnl = holding["total_realized_pnl"]

        if qty <= 0 or avg_price <= 0:
            # 전량 매도된 종목도 실현손익은 기록
            if realized_pnl != 0:
                total_realized_pnl += realized_pnl
            continue

        current_price = current_prices.get(ticker)
        if current_price is None:
            continue

        cost = qty * avg_price
        value = qty * current_price
        unrealized_pnl = value - cost
        pnl_pct = (unrealized_pnl / cost * 100) if cost > 0 else 0

        positions.append({
            "ticker": ticker,
            "quantity": qty,
            "avg_price": avg_price,
            "current_price": current_price,
            "cost": cost,
            "value": value,
            "pnl": unrealized_pnl,
            "pnl_pct": pnl_pct,
            "realized_pnl": realized_pnl,
            "purchase_date": "",
        })

        total_value += value
        total_cost += cost
        total_realized_pnl += realized_pnl

    # 비중 계산
    for pos in positions:
        pos["weight"] = (pos["value"] / total_value * 100) if total_value > 0 else 0

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    return {
        "total_value": total_value,
        "total_cost": total_cost,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "total_realized_pnl": total_realized_pnl,
        "positions": sorted(positions, key=lambda x: -x["value"]),
    }


def compute_portfolio_value_history(holdings, start_date, end_date):
    """기간별 포트폴리오 가치 시계열 계산.
    반환: pd.Series (날짜 인덱스, 포트폴리오 총 가치)
    """
    active = {}
    for t, info in holdings.items():
        holding = get_holding(holdings, t)
        if holding and holding["quantity"] > 0:
            active[t] = holding

    tickers = list(active.keys())
    if not tickers:
        return pd.Series(dtype=float)

    try:
        data = yf.download(tickers, start=start_date, end=end_date, interval="1d")
        if data.empty:
            return pd.Series(dtype=float)

        closes = pd.DataFrame()
        if len(tickers) == 1:
            if 'Close' in data.columns:
                closes[tickers[0]] = data['Close']
            elif isinstance(data.columns, pd.MultiIndex):
                closes[tickers[0]] = data[(tickers[0], 'Close')]
        else:
            for t in tickers:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        closes[t] = data[(t, 'Close')]
                    else:
                        closes[t] = data['Close']
                except (KeyError, TypeError):
                    continue

        if closes.empty:
            return pd.Series(dtype=float)

        # 각 종목의 수량 * 종가 합산
        portfolio_value = pd.Series(0.0, index=closes.index)
        for t in tickers:
            if t in closes.columns:
                qty = active[t]["quantity"]
                portfolio_value += closes[t].fillna(method='ffill') * qty

        return portfolio_value

    except Exception as e:
        logging.error(f"[HOLDINGS] Portfolio history error: {e}")
        return pd.Series(dtype=float)
