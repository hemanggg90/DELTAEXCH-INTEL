# research_lab: backtest sandbox for 10 long-option strategies

An isolated research folder. It answers one question: **does any of 10 pre-declared long-option strategy families have
a robust edge after realistic option costs on BTC, ETH and XAUT?** It does not change the main project, registers
nothing, has no broker code and cannot place an order. If the answer is "no strategy", the report says so.

- Methodology (frozen before any result): `PROTOCOL.md`. Its hash is in `FROZEN_PROTOCOL_HASH.txt` and in every report.
- Strategies: `lab/strategies.py` (rules), `lab/config.py` (parameters), `lab/library.py` (the pre-declared variants).
- Option selection and costs: the project's own selector, option market model and backtester (read-only imports).
- Output: `reports/STRATEGY_LAB_REPORT.md` and `reports/results.json`.

## Commands (from the repository root)
```bash
pip install -r requirements-dev.txt

# 1. Data (public, read-only, no API keys). Writes to the git-ignored data_cache/. Takes a while (the IV step is long).
python scripts/fetch_candles.py BTCUSD ETHUSD XAUTUSD .DEXBTUSD .DEETHUSD .DEXAUTUSD --timeframe 5m --days 330
python scripts/fetch_candles.py BTCUSD ETHUSD XAUTUSD --timeframe 5m --days 330 --series FUNDING OI --series-timeframe 1h
python scripts/build_iv_history.py --since 2025-12-01 --underlyings BTC,ETH,XAUT --workers 4
python scripts/build_iv_long.py --underlyings BTC,ETH,XAUT --workers 4

# 2. Tests (offline). The main `python -m pytest` does not collect this folder.
python -m pytest research_lab/tests

# 3. The research run (full: 5m and 15m, BTC/ETH/XAUT, 300 random-control runs for cells that earn it)
python research_lab/run_research.py
python research_lab/run_research.py --reuse      # re-render from cached stages (same protocol and window only)
python research_lab/run_research.py --quick      # smoke run with 20 control runs; writes *_QUICK files, NOT the result
```

## Reading the result
Every cell (variant x timeframe) ends as one of: **ACCEPTED**, **REJECTED**, **DATA-INSUFFICIENT**, **EXPERIMENTAL**
(definitions in `PROTOCOL.md`). The report lists, for every cell, the gates it passed or failed and why, plus per-asset,
per-regime, cost and straddle-economics tables, and the previous Supertrend result next to the re-run.

## Rules this folder follows
- Frozen method: changing a parameter, rule or gate changes the protocol hash and fails
  `tests/test_protocol_and_safety.py` until it is deliberately re-frozen and disclosed.
- No look-ahead: every strategy is tested by truncation (`tests/test_integration.py`).
- Missing data means NO SIGNAL (for example funding/OI), never an invented value.
- Promoting anything into the main project is a separate decision, taken only after reading the report.
