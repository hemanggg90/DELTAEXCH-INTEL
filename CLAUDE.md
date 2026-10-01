# CLAUDE.md - delta-intelligence

delta-intelligence is research-first strategy intelligence and execution for **Delta Exchange India options, BUYING ONLY**
(plan v3, 2026-10-02). It buys calls and puts, plus long straddles/strangles, on BTC, ETH and XAUT. Perp, index,
funding and OI data are signal inputs only. It is one Streamlit app, ported from `nifty-intelligence`. Read `docs/REFERENCE_README.md` for the
reference design and `docs/DELTA_API_NOTES.md` for verified exchange facts.
The approved phased plan (v3, options buying only) lives at `~/.claude/plans/replicated-tickling-pixel.md`.

The system answers one question: *which validated strategy has the strongest statistically validated, net-of-cost
edge right now, and is there a valid setup? If not, do nothing.* **NO TRADE is a first-class result.**

## Non-negotiable rules

### Safety
- **PAPER by default.** LIVE needs **both** `TRADING_MODE=LIVE` and
  `TRADING_LIVE_CONFIRM=YES_I_UNDERSTAND_THE_RISK`. `DeltaBroker` re-checks both itself.
- Live code defaults to **TESTNET** (`DELTA_ENV`).
- **No withdrawal functionality anywhere.** A test enforces this.
- **BUYING ONLY.** Allowed orders are buy-to-open, and sell-to-close of an existing long up to the held size.
  Sell-to-open is rejected in THREE places:
  - `options/structures.py`: short legs and spreads can't be constructed;
  - `brokers/order_guard.py`: called by every broker before any order;
  - the risk engine.

  Live sells also go with `reduce_only`. Max loss = premium paid + fees.
- **Exits** are managed by the engine: underlying invalidation, premium stop (−35%), strategy time stop, forced exit
  before the expiry guard (2 h).
- **Strategy acceptance:** ≥ 200 trades, positive out-of-sample net R, stable under ±20% parameter changes, works on
  ≥ 2 underlyings. Never tune a strategy before it passes. Report failures as they are.
- The risk engine is deterministic, independent of research, and has the last word. Every decision is stored with its
  reason.
- **Orders are throttled but never auto-retried**, and the circuit breaker never blocks them. If a send times out,
  reconcile through `client_order_id`.
- The dead-man's-switch heartbeat (`/v2/heartbeat`) is **off by default**: it would cancel the protective brackets.
- **Ask the user before any change to risk behaviour, risk defaults, or anything touching real funds.**

### Secrets
- Never log, print, persist or commit API keys, secrets or signatures. Logging redacts them.
- Read secrets from `st.secrets` or the environment (`.env` locally). `.env` and `.streamlit/secrets.toml` are
  gitignored.
- The sidebar key panel exists only when `APP_PRIVATE=true`. It is session-only and never writes to disk.

### Data
- Source order: user CSV → Delta → `DataUnavailableError(reason)`.
- **No synthetic data at runtime.** `data_adapters/synthetic.py` is for tests only.
- Drop the still-forming candle. Features must never look ahead, and tests prove it on truncated frames.
- Time is **UTC internally, IST (Asia/Kolkata) for display**, via `utils/timeutil.py`. Keep three separate clocks:
  - **exchange day**: 00:00 UTC, for candles, pivots and VWAP
  - **risk day**: 00:00 IST, for loss limits, trade counts and reports
  - **liquidity sessions**: Asia, Europe, US
- The market is 24x7: no sessions closing, no holidays, no forced end-of-day square-off.
- Money: the book is in USD, as the exchange settles. Display also shows INR with lakh/crore grouping at
  `USDINR_RATE`, labelled as an estimate.

### Honesty
- Mark anything not confirmed by docs or a live response as **UNVERIFIED**. Don't guess API behaviour; record new
  findings in `docs/DELTA_API_NOTES.md`.
- The reference project found **no proven edge**. Report backtest and ranker-evaluation results as they are, even when
  negative.

## Engineering standards
- Python 3.11+ (developed on 3.12), type hints everywhere, small modules mirroring the reference layout.
- Use `pytest` with mocked HTTP (`responses`). **Tests never use the network**, use temporary databases, and never touch
  real data. Target 150+ tests.
- Streamlit-only architecture:
  - `app.py` at the root, with a password gate and then `st.navigation`.
  - One `TradingEngine` via `@st.cache_resource`, running daemon threads. Threads never call `st.*`.
  - The UI reads thread-safe shared caches through `st.fragment(run_every=...)`.
  - `st.session_state` holds UI state only. Trading state lives in the DB.
- Configuration comes entirely from environment variables or secrets, with safe defaults (SQLite, PAPER, TESTNET).
- Work in phases P0–P7 and stop at each gate with test output shown.

## Commands
```bash
pip install -r requirements-dev.txt
python -m pytest            # whole suite, offline
streamlit run app.py        # dashboard (from P6)
python scripts/check_delta.py   # connectivity: time sync, auth, IP whitelist (read-only)
python scripts/fetch_candles.py BTCUSD ETHUSD .DEXBTUSD .DEETHUSD --days 60   # fill the Parquet cache (public data)
python scripts/fetch_candles.py BTCUSD ETHUSD --days 68 --series FUNDING OI --series-timeframe 1h
python scripts/regime_report.py --days 60   # features + regimes on real data, with a no-look-ahead re-check
```
