"""
TESTNET round trip: buy ONE contract of the cheapest liquid option through DeltaBroker, then sell it back
(reduce-only), printing the raw exchange payloads next to what the database recorded. Purpose: verify the UNVERIFIED
assumptions in docs/DELTA_API_NOTES.md section 14 (order response fields, fee, wallet, positions row, reconcile).

    # dry run (default): runs the startup gate, picks the contract, prints the plan, sends NO order
    TRADING_MODE=LIVE TRADING_LIVE_CONFIRM=YES_I_UNDERSTAND_THE_RISK DELTA_ENV=TESTNET python scripts/testnet_roundtrip.py

    # send the two orders (testnet only)
    ... python scripts/testnet_roundtrip.py --yes

    # a previous run left a position open: sell it back
    ... python scripts/testnet_roundtrip.py --yes --close-only

    # fee probe: a pricier option (premium >= $2), to tell the 10%-of-premium cap from the notional-based fee
    ... python scripts/testnet_roundtrip.py --yes --min-cost 2

    # CRASH / RESTART TEST (three separate runs, in this order):
    ... python scripts/testnet_roundtrip.py --yes --crash         # buy 1, then hard-exit WITHOUT closing (exit code 3)
    ... python scripts/testnet_roundtrip.py --orphan-check        # fresh empty DB + open exchange position: expect ORPHAN
    ... python scripts/testnet_roundtrip.py --yes --recover       # new process, real recover(): reconcile OK, then close

Safety:
- Refuses to run unless DELTA_ENV=TESTNET and BOTH live gates are set by YOU in the environment (the script never sets
  them). PRODUCTION is refused outright.
- Goes through `build_live_broker`, so the connectivity gate runs, and through the broker's order guard.
- Bypasses the strategy/risk engine on purpose (a $100 testnet wallet would veto everything): it is a plumbing test.
- Uses its OWN database (data_cache/testnet_roundtrip.db), so it never touches the engine's book.
- Prints exchange payloads and database rows only. Keys and signatures are never printed (logging redaction is on).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(ROOT / 'data_cache' / 'testnet_roundtrip.db').as_posix()}")

from delta_intelligence.brokers.delta_api_client import public_client  # noqa: E402
from delta_intelligence.brokers.live_gate import LiveGateError, build_live_broker  # noqa: E402
from delta_intelligence.brokers.paper_broker import TradePlan  # noqa: E402
from delta_intelligence.config.settings import get_settings  # noqa: E402
from delta_intelligence.config.watchlist import UNDERLYINGS, get_watchlist  # noqa: E402
from delta_intelligence.data.data_manager import DataManager  # noqa: E402
from delta_intelligence.database import db  # noqa: E402
from delta_intelligence.database.models import Fill, Order, Position, PositionLeg  # noqa: E402
from delta_intelligence.execution.engine import kill_switch_on, set_kill_switch  # noqa: E402
from delta_intelligence.execution.recovery import recover  # noqa: E402
from delta_intelligence.options.chain import fetch_chain  # noqa: E402
from delta_intelligence.options.structures import long_call, long_put  # noqa: E402
from delta_intelligence.utils.logging_utils import redact  # noqa: E402
from delta_intelligence.utils.timeutil import now_utc  # noqa: E402

MIN_HOURS_TO_EXPIRY = 8.0  # well clear of the 2 h expiry guard
MAX_SPREAD_PCT = 25.0  # a test on a junk quote (bid 0.1 / ask 1.4) proves nothing about fees or fills
PREFERRED_ASSETS = ("BTC", "ETH")  # what the engine really trades; XAUT only if nothing else qualifies
MIN_COST_USD = 0.05  # below this the fee rounds away and the fee fields can't be checked
CAPTURED: list[tuple[str, str, object]] = []


def show(title: str, obj) -> None:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=str)
    print(f"\n--- {title} ---\n{redact(text)}")


def spy_on(client) -> None:
    """Record every raw payload the client returns, without changing behaviour."""
    original = client.request

    def spy(method, path, **kw):
        try:
            payload = original(method, path, **kw)
        except Exception as exc:
            CAPTURED.append((method, path, f"EXCEPTION {type(exc).__name__}: {exc}"))
            raise
        CAPTURED.append((method, path, payload))
        return payload

    client.request = spy


def raw_for(path_part: str, method: str | None = None) -> list:
    return [p for m, path, p in CAPTURED if path_part in path and (method is None or m == method)]


def db_rows() -> dict:
    cols = lambda row: {c.name: getattr(row, c.name) for c in row.__table__.columns}  # noqa: E731
    with db.get_session() as s:
        return {"orders": [cols(o) for o in s.query(Order).order_by(Order.id).all()],
                "fills": [cols(f) for f in s.query(Fill).order_by(Fill.id).all()],
                "positions": [cols(p) for p in s.query(Position).order_by(Position.id).all()],
                "legs": [cols(lg) for lg in s.query(PositionLeg).order_by(PositionLeg.id).all()]}


def pick_contract(chain, now, min_cost: float = MIN_COST_USD):
    """Cheapest option that makes a meaningful test: two-sided quote with size, spread <= MAX_SPREAD_PCT, premium of at
    least MIN_COST_USD, and enough time to expiry. BTC/ETH are preferred over XAUT."""
    best: dict[int, tuple] = {}
    for q in chain.by_symbol.values():
        hours = (q.expiry - now).total_seconds() / 3600
        if not (q.bid and q.ask and q.bid > 0 and q.ask > 0 and q.product_id and q.contract_value):
            continue
        if (q.bid_size or 0) < 1 or (q.ask_size or 0) < 1 or hours < MIN_HOURS_TO_EXPIRY:
            continue
        spread = (q.ask - q.bid) / ((q.ask + q.bid) / 2) * 100
        cost = q.ask * q.contract_value
        if spread > MAX_SPREAD_PCT or cost < min_cost:
            continue
        tier = 0 if q.underlying in PREFERRED_ASSETS else 1
        if tier not in best or cost < best[tier][0]:
            best[tier] = (cost, q)
    return best[min(best)] if best else None


def snapshot_exchange(broker, label: str) -> None:
    show(f"{label}: wallet balances (raw)", broker.client.get_wallet_balances())
    show(f"{label}: margined positions (raw)", broker.client.get_positions())


def orphan_check(broker, s) -> int:
    """Point at a brand-new EMPTY database while the exchange still holds a position: reconcile must call it an ORPHAN
    and engage the kill switch (it never adopts or trades it silently)."""
    path = ROOT / "data_cache" / "testnet_orphan_check.db"
    path.unlink(missing_ok=True)
    db.reset_engine()
    db.init_db(f"sqlite:///{path.as_posix()}")
    held = {(p.get("product_symbol") or ""): p.get("size") for p in broker.client.get_positions() if p.get("size")}
    if not held:
        print("the exchange holds no position, so there is nothing to orphan. Run --yes --crash first.")
        return 1
    print(f"exchange holds {held}; database is empty (fresh file)")
    r = broker.reconcile()
    show("reconcile() against an empty database", r)
    ks = kill_switch_on()
    print(f"\nkill switch engaged: {ks}")
    set_kill_switch(False, "orphan-check finished")  # only in this throwaway database
    good = (not r["ok"]) and any("ORPHAN" in c for c in r["critical"]) and ks
    print("RESULT:", "PASS (orphan reported, kill switch engaged, nothing traded)" if good else "FAIL")
    return 0 if good else 1


def recover_run(broker, s, chain_fn, yes: bool) -> int:
    """A fresh process after a crash: run the engine's real restart recovery, then close what the crash left open."""
    items = broker.open_positions()
    print(f"database after the 'crash': {len(items)} OPEN live position(s): "
          f"{[(i['position'].position_id, [(lg.symbol, lg.contracts) for lg in i['legs']]) for i in items]}")
    if not items:
        print("nothing recorded as open; run --yes --crash first (same database).")
        return 1
    summary = recover(broker, DataManager.from_settings(s), now_utc())
    show("recover() summary", summary)
    rec = summary.get("reconcile") or {}
    ks = kill_switch_on()
    print(f"\nreconcile ok: {rec.get('ok')}   kill switch engaged: {ks}")
    ok = bool(rec.get("ok")) and not ks and not summary.get("errors")
    print("RESULT:", "PASS (database and exchange agree after the restart)" if ok else "FAIL (see above)")
    if not yes:
        print("DRY RUN: not closing. Re-run with --yes to sell the position back.")
        return 0 if ok else 1
    broker.chain = chain_fn
    for it in broker.open_positions():
        pnl = broker.close_position(it["position"].position_id, "ROUNDTRIP_RECOVER")
        print(f"close {it['position'].position_id}: realised P&L {pnl}")
    time.sleep(2)
    show("exchange positions AFTER close (expect [])", broker.client.get_positions())
    show("reconcile() after close", broker.reconcile())
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually send the orders (testnet only)")
    ap.add_argument("--close-only", action="store_true", help="only sell back positions left open in this script's DB")
    ap.add_argument("--min-cost", type=float, default=MIN_COST_USD,
                    help="minimum premium in USD for the contract (use ~2 to probe the notional-based fee)")
    ap.add_argument("--crash", action="store_true", help="buy 1 contract, then hard-exit without closing it")
    ap.add_argument("--recover", action="store_true", help="restart test: run the real recover(), then close")
    ap.add_argument("--orphan-check", action="store_true", help="reconcile against an EMPTY database: expect ORPHAN")
    args = ap.parse_args()

    s = get_settings()
    if s.delta_env != "TESTNET":
        print(f"REFUSED: DELTA_ENV={s.delta_env}. This script only runs on TESTNET.")
        return 2
    if not s.live_mode_fully_authorized:
        print("REFUSED: set TRADING_MODE=LIVE and TRADING_LIVE_CONFIRM=YES_I_UNDERSTAND_THE_RISK yourself in the "
              "environment for this run (the script never sets the gates).")
        return 2
    db.init_db()
    print(f"database: {s.database_url.split('///')[-1]}  env: {s.delta_env}  base: {s.rest_base_url}")

    client = public_client(s)
    assets = tuple(UNDERLYINGS[p].asset for p in get_watchlist())
    chain_fn = lambda: fetch_chain(client, assets, max_age=0)  # noqa: E731
    try:
        broker = build_live_broker(chain_fn, s)
    except LiveGateError as exc:
        print(f"STARTUP GATE FAILED: {exc}")
        return 1
    spy_on(broker.client)
    print("startup gate: PASSED")
    snapshot_exchange(broker, "BEFORE")

    if args.orphan_check:
        return orphan_check(broker, s)
    if args.recover:
        return recover_run(broker, s, chain_fn, args.yes)

    if args.close_only:
        items = broker.open_positions()
        if not items:
            print("no open LIVE position in this script's database; nothing to close")
            return 0
        for it in items:
            pnl = broker.close_position(it["position"].position_id, "ROUNDTRIP_CLOSE_ONLY")
            print(f"close {it['position'].position_id}: realised P&L {pnl}")
        show("exchange positions AFTER", broker.client.get_positions())
        return 0

    now = now_utc()
    picked = pick_contract(chain_fn(), now, args.min_cost)
    if picked is None:
        print("no eligible option (two-sided quote with size, spread <= 25%, premium >= $0.05, >= 8 h to expiry) on "
              "testnet right now; try later, or loosen MAX_SPREAD_PCT / MIN_COST_USD at the top of this script")
        return 1
    cost, q = picked
    spread = (q.ask - q.bid) / ((q.ask + q.bid) / 2) * 100
    print(f"\nplan: BUY 1 contract of {q.symbol} (product {q.product_id}) at ask {q.ask} + slippage; "
          f"bid {q.bid}, spread {spread:.1f}%, ~${cost:.4f} premium, expiry {q.expiry:%d %b %H:%M UTC}; "
          f"then SELL it back reduce-only")
    if not args.yes:
        print("\nDRY RUN: no order sent. Re-run with --yes to send the two testnet orders.")
        return 0

    build = long_call if q.kind == "C" else long_put
    structure = build(q.underlying, q.strike, q.expiry.to_pydatetime(), 1, q.contract_value, q.symbol)
    result = broker.open_structure(TradePlan("roundtrip-test", structure))
    print(f"\nOPEN result: ok={result.ok} reason={result.reason!r} fills={result.fills}")
    show("raw POST /v2/orders response(s)", raw_for("/v2/orders", "POST"))
    if not result.ok:
        show("database rows", db_rows())
        print("buy did not fill; nothing is open.")
        return 1

    if args.crash:
        print(f"\nSIMULATING A CRASH: position {result.position_id} is open on the exchange and in the database; "
              "exiting the process now without closing it (exit code 3).", flush=True)
        os._exit(3)  # no cleanup, no close: like a power cut

    time.sleep(2)  # let the exchange reflect the fill
    snapshot_exchange(broker, "AFTER BUY")
    show("raw GET order-by-client-id (for the shape of the lookup)",
         broker.client.get_order_by_client_id(db_rows()["orders"][0]["client_order_id"]))
    show("account_snapshot()", broker.account_snapshot())
    show("reconcile() while holding (expect ok=True)", broker.reconcile())

    broker.chain = chain_fn  # fresh quotes for the exit
    pnl = broker.close_position(result.position_id, "ROUNDTRIP_TEST")
    show("raw POST /v2/orders response(s) after exit", raw_for("/v2/orders", "POST")[1:])
    if pnl is None:
        print("\n!!! SELL DID NOT COMPLETE. A long position may still be open on testnet. Re-run with "
              "--yes --close-only. !!!")
    else:
        print(f"\nCLOSED: realised P&L ${pnl:.6f} (spread + fees; expected slightly negative)")
    time.sleep(2)
    snapshot_exchange(broker, "AFTER SELL")
    show("reconcile() after exit (expect ok=True, no positions)", broker.reconcile())
    show("database rows (compare with the raw payloads above)", db_rows())
    print("\nNext: compare the raw payloads with the Order/Fill/Position rows and the testnet UI (price, size, fee, "
          "wallet change), then update docs/DELTA_API_NOTES.md section 14.")
    return 0 if pnl is not None else 1


if __name__ == "__main__":
    sys.exit(main())
