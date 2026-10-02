# delta-intelligence

A research app for **options buying on Delta Exchange India** (BTC, ETH and XAUT).
It answers one question:

> Is there a trading strategy with a statistically proven edge right now, after costs, and is there a valid setup?
> **If not, do nothing.**

"No trade" is a normal, expected answer. This is research software, not financial advice.

---

## 1. The idea in simple words

1. **Look at the market.** The app reads prices, options, funding and open interest from Delta Exchange.
2. **Test strategies honestly.** Each strategy is back-tested on past data *after* paying realistic costs (spread, fees, tax).
3. **Trust only strategies that pass strict rules.** A strategy must have at least 200 trades, make money on data it
   never saw, stay profitable when its settings are nudged by 20%, and work on at least 2 coins.
   Nothing is tuned *before* it passes.
4. **A risk engine has the last word.** It is simple and fixed, it does not depend on the research, and it can say NO
   to any trade. Every decision is stored with its reason.
5. **Paper trade first.** Practice with fake money against real prices, then testnet, and only much later real money.

## 2. What it trades

- **Only buys** call options and put options (and long straddles / strangles, which are just a call and a put bought together).
- It **never sells to open**. Selling is allowed only to *close* something it already bought.
- So the **most you can lose on a trade is the premium you paid plus fees.** There is no margin call and no short risk.
- Futures, the index, funding and open interest are used only as *signals*. They are never traded.

## 3. Honest status of the evidence

This matters more than anything else in this file.

- Of **10 strategies** tested as bought options, **0 were accepted** (`docs/research/P3V3_REPORT.md`).
- A wider search of 270 variants kept **2 Supertrend variants** that stayed slightly positive on unseen data
  (`docs/research/STRATEGY_SEARCH.md`). But the evidence is weak: only 33 to 47 test trades, about zero on ETH, and
  random signals "won" almost as often in the same search.
- So the two active strategies are a **paper experiment, not a proven edge**. The software reports failures as they are.

## 4. Safety rules (never broken)

| Rule | What it means |
|---|---|
| PAPER by default | No real orders unless you set two things on purpose |
| Two switches for LIVE | `TRADING_MODE=LIVE` **and** `TRADING_LIVE_CONFIRM=YES_I_UNDERSTAND_THE_RISK` |
| TESTNET by default | Live code points at Delta's practice exchange unless you change `DELTA_ENV` |
| Buying only | Sell-to-open is blocked in three places: option structures, the broker guard, the risk engine |
| Reduce-only sells | A live sell can only shrink a position, never create a short |
| No withdrawals | There is no withdrawal code anywhere, and a test checks that |
| Secrets stay secret | API keys are read from `.env` or secrets, never logged, printed or committed |
| No blind retries | A timed-out order is never sent again. It is looked up by its `client_order_id` instead |
| Kill switch | One button stops new trades. Exits keep running |
| Automatic exits | Underlying invalidation, a -35% premium stop, a time stop, and a forced exit 2 hours before expiry |
| No fake data | The app never invents prices. If data is missing it says so |

## 5. How it works

```
Delta data -> features -> market regime -> strategies -> trade planner
          -> RISK ENGINE (can veto) -> broker (paper or live) -> database -> dashboard
```

| Folder | Job |
|---|---|
| `delta_intelligence/data`, `data_adapters` | Fetch and cache candles (your CSV first, then Delta) |
| `features`, `regimes`, `market_state` | Indicators and "what kind of market is this" |
| `strategies` | The strategy library and the 2 active paper variants |
| `options` | Option chain, pricing, picking strikes and expiries |
| `backtesting`, `ranking`, `analogues` | Honest historical testing and comparison |
| `risk` | The deterministic risk engine |
| `execution` | The 24x7 engine, trade planner, exit monitor, restart recovery |
| `brokers` | Delta API client, paper broker, live broker, safety guards |
| `app.py`, `app_pages/` | The Streamlit dashboard (14 pages) |
| `tests/` | 429 offline tests (no network, no real data) |

## 6. Plan and status

| Phase | What | Status |
|---|---|---|
| P0 to P2 | Project setup, data, features and regimes | Done |
| P3 | Strategies and honest back-tests | Done (no strategy accepted) |
| P4 | Risk engine | Done |
| P5 | 24x7 paper engine, exits, recovery | Done |
| P6 | Streamlit dashboard | Done |
| **P7** | **Live trading (testnet first)** | **Done and verified on Delta India testnet (see `docs/P7_REPORT.md`). Production not tried, stays off** |

## 7. Where we are right now

**All phases P0 to P7 are finished.** P7 passed its gate on Delta India **testnet** (fake money). Full details and the
honest list of what is still unverified are in `docs/P7_REPORT.md`.

Proven on testnet:
- Login, IP whitelist and clock check pass.
- A real buy and a reduce-only sell work. The database P&L matched the exchange wallet to the cent.
- After a hard crash with a position open, a restart reconciled correctly and closed it.
- If the database and the exchange disagree, the system stops trading and tells you (it never adopts a position silently).

Not proven: anything on **production** (real fills, real fees), and, more importantly, **the strategies themselves still
have no proven edge**. P7 shows the plumbing is safe, not that it makes money.

## 8. What to do next (your decision)

1. **Let it run on testnet for several days.** Start the engine from the Live Trading page (or
   `python scripts/run_engine.py` with both live switches set and `DELTA_ENV=TESTNET`). Check Decisions, Risk Control
   and System Health daily. Keep the PC awake, or use a VPS.
2. **Keep collecting evidence.** The chain recorder builds real option history, so the strategy tests get better over time.
   Re-run the strategy reports (`scripts/research_report_v3.py`) when there is more data. Report results as they are.
3. **Only then think about real money.** Use a small production key, a whitelisted static IP, and change `DELTA_ENV`
   yourself. The risk limits are unchanged and stay in force.

## 9. Setup and commands

```bash
pip install -r requirements-dev.txt
python -m pytest                     # all tests, offline
streamlit run app.py                 # the dashboard
python scripts/check_delta.py        # read-only: clock, login, IP whitelist
python scripts/run_engine.py         # 24x7 engine, PAPER unless both live switches are set
python scripts/fetch_candles.py BTCUSD ETHUSD .DEXBTUSD .DEETHUSD --days 60   # fill the data cache
python scripts/ui_smoke_test.py      # open every dashboard page and check for errors
python scripts/testnet_roundtrip.py  # testnet plumbing test (dry run by default; see the script header)
```

**Settings** go in a `.env` file in this folder (it is git-ignored). Copy `.env.example` and fill in:
`DELTA_ENV`, `DELTA_API_KEY`, `DELTA_API_SECRET`, `APP_PASSWORD`. Never share or paste the key values.

Notes:
- The PC must not go to sleep while the engine runs.
- Delta accepts trading keys only from a **whitelisted static IP**. A VPS is the usual answer for always-on live use.
  Streamlit Community Cloud cannot do this, so it is paper-only.
- Times are stored in UTC and shown in IST. Money is in USD, with an INR estimate shown for reference.

## 10. Read more

- `CLAUDE.md`: the project rules, in full.
- `docs/DELTA_API_NOTES.md`: what is verified about Delta's API and what is not.
- `docs/REFERENCE_README.md`: the design this project was ported from.
- `docs/research/`: back-test and strategy-search reports.
