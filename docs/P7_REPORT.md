# P7 gate report: live execution (verified on India TESTNET, 2026-10-02)

**Result: P7 passes its gate on testnet. Production has NOT been tried and stays off.**

## What was built
- `brokers/delta_broker.py`: live broker. IOC limit buys, reduce-only IOC sells, order guard first, order row saved before the
  request, never retried, timeouts resolved by `client_order_id` (otherwise UNKNOWN + kill switch), partial fills tracked,
  P&L = proceeds - premium - fees.
- `brokers/live_gate.py`: the only way to get a live broker (both gates, credentials, venue match, connectivity check).
- `execution/recovery.py`: live restart runs `reconcile()` first. Orphan, short, size mismatch or unresolved order engages
  the kill switch; a database leg the exchange doesn't hold becomes STALE.
- `execution/account.py`: per-mode account state (a paper peak can't appear as a live drawdown) and per-mode trade counts.
- Dashboard: Live Trading page (requirements, read-only check, typed confirmation, start/stop, reconcile, live account
  and book). Manual paper ticket is pinned to the paper book.
- `scripts/testnet_roundtrip.py`: dry-run by default; `--yes`, `--close-only`, `--crash`, `--orphan-check`, `--recover`,
  `--min-cost`.

## Evidence (all on Delta India testnet, fake money; raw results in docs/DELTA_API_NOTES.md section 14)
| Test | Outcome |
|---|---|
| Connectivity: auth, IP whitelist, time sync, products, positions, orders | PASS |
| Buy 1 + reduce-only sell 1 (cheap option) | PASS. Order fields, wallet, positions row as assumed. DB P&L == wallet change to the cent |
| Same on a pricier option (premium ~$2) | PASS. Fee, wallet and reconcile as expected |
| Hard crash with a position open, then restart (new process) | PASS. Reconcile OK, kill switch off, position closed, exchange flat |
| Reconcile against an EMPTY database while the exchange holds a position | PASS. ORPHAN reported, kill switch engaged, nothing traded |

## Bugs found by the real exchange and fixed
1. Restart recovery asked Delta for candles over a window with no closed bar and logged `DataUnavailableError`
   (now skipped when there is no stop or the window is under 5 minutes). Test added.
2. A wrong conclusion of mine, caught before it shipped: testnet fees are about 3x production's (0.0003 / 10% cap vs
   0.0001 / 3.5% cap, both read from public product fields). The cost model's defaults are the production schedule and
   were kept; a test now pins them.

## Offline gate
`python -m pytest`: all tests pass (429). `python scripts/ui_smoke_test.py`: 14/14 pages render, in PAPER and in LIVE mode.

## Not verified (honest list)
- Anything on PRODUCTION: real fills, real fees (production fees come from public product fields only), the IP
  whitelist for a production key.
- A crash between sending an order and saving the result (offline tests only).
- The sign of a SHORT position row (we never hold shorts).
- The 404 shape of the order lookup, and the exact notional the fee uses.
- The strategies themselves: still no proven edge (0 of 10 accepted; 2 weak paper variants). P7 proves the plumbing, not profit.

## Before any real money (user decision, not automatic)
Run on testnet for days with the engine, review decisions and reconcile logs, then decide. Use a small production key, a
whitelisted VPS IP, and keep `DELTA_ENV=TESTNET` until you deliberately change it.
