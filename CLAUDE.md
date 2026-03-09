# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

US stock real-time monitoring, backtesting, and portfolio analysis application built with Python/Tkinter. Uses Yahoo Finance (yfinance) for market data. UI and comments are in Korean.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python stock_monitor_gui.py

# Build Windows executable
pyinstaller stock_monitor_gui.spec
```

There are no tests or linting configured.

## Architecture

```
stock_monitor_gui.py              # Main Tkinter GUI, entry point (AppState singleton)
  └── modules/                    # All supporting modules (added to sys.path at startup)
      ├── __init__.py
      ├── stock_score.py          # Technical analysis (RSI, MA, MACD, Bollinger, momentum, Ichimoku)
      ├── market_trend_manager.py # US market session detection, trend caching, volatility regime
      ├── config.py               # JSON config loading with recursive default merging, atomic writes
      ├── backtest_popup.py       # Strategy backtesting engine + matplotlib charts (8 strategies)
      ├── news_panel.py           # Finviz news scraping, sentiment classification, ticker linking
      ├── data_cache.py           # SQLite cache for yfinance data (delta updates, TTL-based expiry)
      ├── pattern_recognition.py  # Chart pattern detection (double top/bottom, H&S, triangles) via scipy
      ├── fundamental_score.py    # Valuation scoring, Piotroski F-Score, factor scoring
      ├── portfolio_analysis.py   # Correlation, optimization (4 methods), Black-Litterman, Fama-French
      ├── holdings_manager.py     # Portfolio holdings CRUD (holdings.json), position/P&L calculation
      ├── quant_screener.py       # Quantitative screening (6 strategies: buffett/graham/lynch/dividend/momentum/multifactor)
      ├── screener_popup.py       # Screener UI popup with Treeview results + detail panel
      ├── stock_universe.py       # Stock universe providers (S&P500/NASDAQ100/DOW30 bundled + online + CSV)
      ├── help_texts.py           # Centralized Korean help/tooltip strings
      └── ui_components.py        # Reusable Tooltip / HelpTooltip widgets
```

`stock_monitor_gui.py` adds `modules/` to `sys.path` at startup, so all inter-module imports (`import config`, `from fundamental_score import ...`) work unchanged.

### Data Flow

GUI spawns a daemon thread (`monitor_stocks`) that refreshes every 60 seconds. Each refresh uses `ThreadPoolExecutor(max_workers=10)` to call `fetch_stock_data()` in parallel for all tickers in `watchlist.json`. Results populate a Tkinter Treeview table. Status bar shows per-ticker progress during loading (`주식 데이터: 3/10 | 뉴스: 로딩 중...`).

### Backtesting

Double-clicking a ticker row (or clicking a ticker in the news panel) opens `backtest_popup.py`, which runs one of 8 strategies (macd, rsi, bollinger, ma_cross, macd+rsi, momentum_signal, momentum_return_ma, ichimoku) against historical data and renders matplotlib charts with buy/sell markers. Includes strategy comparison and sensitivity analysis embedded in the result container.

### Configuration

`config.py` loads `config.json` with recursive merging against defaults. Three presets (short/middle/long) control period, interval, and indicator parameters. Access via `config.config` proxy (lazy-loaded, thread-safe). `get_risk_free_rate()` fetches ^TNX with 1hr cache, 4.5% fallback.

### Data Caching

`data_cache.py` provides SQLite-based caching for yfinance downloads with TTL-based expiry and delta updates. Also has `fundamental_cache` table (24hr TTL) used by the quant screener. Integrated into `fetch_stock_data()` and `_retry_download()`.

### Portfolio Analysis

`portfolio_analysis.py` provides correlation matrix, 4 optimization methods (max Sharpe, min variance, risk parity, equal weight), Black-Litterman model, and Fama-French factor analysis. Accessible from the 분석 menu.

## Threading Model

- **Main thread**: Tkinter event loop only. All UI updates must go through `root.after()` or `popup.after()`.
- **Monitor thread**: Daemon thread with 60s refresh loop using `ThreadPoolExecutor(max_workers=10)` for parallel ticker fetching.
- **News thread**: Daemon thread for 5-minute news refresh cycle.
- **Backtest/analysis**: Run in background threads with UI cleanup via `popup.after(0, callback)`.
- **Thread safety**: `app.watchlist_lock` protects watchlist access. `app.news_lock` protects cached news. Config uses lazy init with `_config_lock`. `data_cache.py` uses its own `_lock` for SQLite access.

## Key Data Files

- `config.json` — user settings (auto-created on first run, auto-saved on changes)
- `watchlist.json` — monitored ticker list (auto-created, modified via GUI)
- `holdings.json` — portfolio holdings with transactions
- `stock_data_cache.db` — SQLite cache for yfinance data
- `logs/app.log` — rotating log (5MB/file, 5 backups, 30-day retention)

## Important Constraints

- yfinance `interval`/`period` combinations have strict limits (e.g., 1m interval max 7 days, 5m max 60 days). See `auto_set_interval_by_period()` in `stock_score.py`.
- All file I/O uses UTF-8 encoding explicitly.
- The PyInstaller spec includes hidden imports for all modules — update `stock_monitor_gui.spec` when adding new dependencies.
- Backtest popup passes stock as `"CompanyName (TICKER)"` format string; `ticker_symbol` is extracted via `stock.split('(')[-1].split(')')[0]`.
- Matplotlib figures must be tracked in `open_figures[]` and closed on popup destroy to prevent memory leaks.
- `StockData` namedtuple fields must match table insert order in `update_table()`.
- Commission/slippage config: `backtest.commission_rate`, `backtest.slippage_pct` in config.json.
- Quant screener strategies reuse `fundamental_score.py` functions (`valuation_score`, `factor_score`, `piotroski_fscore`).
