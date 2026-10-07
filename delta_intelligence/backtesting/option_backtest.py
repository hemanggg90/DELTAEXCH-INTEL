"""
Hybrid backtest for a BUYING-ONLY options system: signals on the underlying, P&L as bought option premium.

For each setup (one trade at a time per strategy):

1. **Entry**, at the signal bar's CLOSE:
   - expiry: the nearest listed expiry with TTE >= dte_multiple × the strategy's expected hold, whose hold ends
     before the expiry guard;
   - strike: ATM (or 1-ITM) call for LONG, put for SHORT, with |model delta| in [0.40, 0.60]; straddle/strangle for
     volatility strategies;
   - legs bought at the modelled ASK.
2. **Breakeven gate:** skip if the expected move < (extrinsic + exit spread + fees) × (1 + margin).
3. **Exits**, the first of:
   - the underlying stop (invalidation; checked before the target within a bar);
   - the underlying target;
   - the premium stop (structure bid value <= entry cost × (1 − 35%));
   - the strategy's time stop;
   - a forced exit before the expiry guard.

   Legs are sold at the modelled BID.
4. **R** = net USD P&L / (premium paid + entry fees). That is the max loss of a bought option, and it is what sizing
   uses.

**Premium source label per trade:**
- `MODEL_REAL_IV`: Black-Scholes at the as-of IV inferred from real Delta option trades;
- `MODEL_REAL_IV_ADJ_BUCKET`: as above, but the IV comes from the nearest maturity bucket because the exact one had no
  fresh observation (e.g. 30-54 h, where only daily contracts' last 32 h were fetched);
- `MODEL_RV_PROXY`: no IV observed within the age limit, so the realised volatility of the index stands in (only if
  `allow_rv_proxy`).

Validation compares the model mid with real traded prices in the same contract and bar wherever they exist.

**Pricing modes (Phase 2)** `OptionBacktestConfig.pricing_mode`:
- `MODEL_ONLY` (default): exactly the methodology above. Reproduces every earlier result.
- `REAL_ONLY`: prices, deltas, strikes and expiries come ONLY from recorded option-chain quotes (REAL_RECORDED). Entry
  at the recorded ask, exit at the recorded bid. A trade without a fresh real two-sided quote at entry or exit is
  skipped and counted (`no_real_snapshot`, `no_real_quote`, `real_mark_only`, `no_real_exit_quote`); there is never a
  silent fallback to the model. A mark without a two-sided quote is not a bid or an ask.
- `REAL_THEN_MODEL_FALLBACK`: real where a fresh recorded quote exists, the model elsewhere. Every trade carries
  `entry_price_source` and `exit_price_source` (REAL_QUOTE, MODELED or MIXED).
Lookups are as-of: a quote is used only if its snapshot was taken at or before the pricing time.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from delta_intelligence.backtesting.option_market import OptionMarketModel
from delta_intelligence.backtesting.recorded_chain import REAL_QUOTE, UNUSABLE, RecordedQuotes
from delta_intelligence.options.picking import PickContext
from delta_intelligence.options.premium_model import plan_premium
from delta_intelligence.options.pricing import bs_price, greeks, year_fraction
from delta_intelligence.options.selector import SelectorConfig, build_long, choose_expiry
from delta_intelligence.options.structures import leg_fee
from delta_intelligence.strategies.base import Setup, Strategy

BAR = pd.Timedelta(minutes=5)
MODEL_ONLY, REAL_ONLY, REAL_THEN_MODEL_FALLBACK = "MODEL_ONLY", "REAL_ONLY", "REAL_THEN_MODEL_FALLBACK"
PRICING_MODES = (MODEL_ONLY, REAL_ONLY, REAL_THEN_MODEL_FALLBACK)
CONTRACT_VALUE = {"BTC": 0.001, "ETH": 0.01, "XAUT": 0.001}  # verified 2026-10-02


@dataclass
class OptionBacktestConfig:
    policy: str | None = None  # None = the strategy's policy
    selector: SelectorConfig = field(default_factory=SelectorConfig)
    premium_stop_pct: float = 35.0
    breakeven_margin: float = 0.25
    apply_breakeven_gate: bool = True
    allow_rv_proxy: bool = False
    allow_adjacent_bucket: bool = True  # use the nearest maturity bucket's IV when the exact bucket has none (labelled)
    units: float = 1.0  # underlying units per leg (R is size-invariant up to tick/fee rounding)
    commission_rate: float = 0.0001
    premium_cap_rate: float = 0.035
    gst_rate: float = 0.18
    pricing_mode: str = MODEL_ONLY  # MODEL_ONLY / REAL_ONLY / REAL_THEN_MODEL_FALLBACK (needs `recorded=`)
    contract_picker: Callable | None = None  # option-selection research hook (see options/picking.py); None = default
    selection_name: str = ""  # label stored on each trade when a picker is used


@dataclass
class OptionTrade:
    strategy: str
    structure: str
    direction: str
    entry_idx: int
    exit_idx: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    exit_reason: str
    expiry: pd.Timestamp
    strikes: tuple
    entry_fills: list
    exit_fills: list
    entry_iv: float
    fees: float
    max_loss: float  # premium paid + entry fees (R denominator)
    net_pnl: float
    net_r: float
    underlying_r: float
    premium_source: str = "MODEL_REAL_IV"
    entry_delta: float | None = None
    validation: list = field(default_factory=list)
    # ---- Phase 2: pricing provenance and attribution (all additive; defaults keep older code working) ------------
    entry_price_source: str = "MODELED"  # REAL_QUOTE / MODELED / MIXED, for the entry legs
    exit_price_source: str = "MODELED"
    entry_mids: list = field(default_factory=list)  # mid of each leg at entry (real mid or model mid)
    exit_mids: list = field(default_factory=list)
    entry_spot: float | None = None
    exit_spot: float | None = None
    spread_cost: float = 0.0  # USD paid to the spread, entry + exit, all legs
    gross_pnl: float | None = None  # mid-to-mid P&L before spread and fees: net_pnl = gross_pnl - spread_cost - fees
    theta_cost_model: float | None = None  # model theta (per day) x days held x units: an ESTIMATE of time decay
    entry_features: dict = field(default_factory=dict)  # what the selector could see at the signal time
    selection: str = ""
    pick_detail: dict = field(default_factory=dict)

    @property
    def win(self) -> bool:
        return self.net_pnl > 0


@dataclass
class OptionBacktestResult:
    strategy: str
    underlying: str
    trades: list[OptionTrade]
    n_setups: int
    skipped: Counter
    unpriced_exits: list = field(default_factory=list)  # (entry_time, max_loss) for REAL_ONLY trades with no real exit quote

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([{k: getattr(t, k) for k in (
            "strategy", "structure", "direction", "entry_idx", "exit_idx", "entry_time", "exit_time", "exit_reason",
            "expiry", "entry_iv", "fees", "max_loss", "net_pnl", "net_r", "underlying_r", "premium_source")}
            for t in self.trades])


class RealOptionPrices:
    """Real traded 5m bars (volume > 0) per symbol, for validation. `loader(expiry)` returns that expiry's frame."""

    def __init__(self, loader):
        self.loader = loader
        self._by_expiry: dict = {}

    def price(self, symbol: str, expiry: pd.Timestamp, bar_open: pd.Timestamp) -> float | None:
        if expiry not in self._by_expiry:
            df = self.loader(expiry)
            if df is None or not len(df):
                self._by_expiry[expiry] = {}
            else:
                df = df[df["volume"].fillna(0) > 0]
                self._by_expiry[expiry] = {(s, pd.Timestamp(t)): float(c) for s, t, c in
                                           zip(df["symbol"], pd.to_datetime(df["timestamp"], utc=True), df["close"])}
        return self._by_expiry[expiry].get((symbol, pd.Timestamp(bar_open)))


def run_option_backtest(strategy: Strategy, frame: pd.DataFrame, market: OptionMarketModel,
                        cfg: OptionBacktestConfig | None = None, real: RealOptionPrices | None = None,
                        setups: list[Setup] | None = None, recorded: RecordedQuotes | None = None) -> OptionBacktestResult:
    cfg = cfg or OptionBacktestConfig()
    mode = cfg.pricing_mode
    if mode not in PRICING_MODES:
        raise ValueError(f"pricing_mode must be one of {PRICING_MODES}, got {mode!r}")
    real_mode = mode != MODEL_ONLY
    if real_mode and recorded is None:
        raise ValueError(f"pricing_mode {mode} needs recorded option quotes (recorded=...)")
    policy = cfg.policy or strategy.policy
    sel = cfg.selector
    setups = setups if setups is not None else strategy.historical_setups(frame)
    ts = pd.to_datetime(frame["timestamp"], utc=True)
    ts_list, close_t = list(ts), list(ts + BAR)
    pos = {t: i for i, t in enumerate(ts_list)}
    hi, lo, close = frame["high"].to_numpy(), frame["low"].to_numpy(), frame["close"].to_numpy()
    idx_close = frame["index_close"].to_numpy() if "index_close" in frame else np.full(len(frame), np.nan)
    rv = frame["index_realized_vol_1d"].to_numpy() if "index_realized_vol_1d" in frame else np.full(len(frame), np.nan)
    iv_pct = frame["iv_percentile"].to_numpy() if "iv_percentile" in frame else np.full(len(frame), np.nan)
    expiries = sorted(market.listed_strikes)
    cv = CONTRACT_VALUE.get(market.underlying, 0.001)
    contracts = max(1, int(round(cfg.units / cv)))
    hold_h = strategy.expected_hold_bars * 5 / 60.0
    skipped: Counter = Counter()
    trades: list[OptionTrade] = []
    unpriced: list = []
    next_free = 0
    why = {"fail": None}  # why the last real-quote lookup returned nothing (REAL_ONLY diagnostics)

    def leg_iv(at, kstrike, expiry, spot, i):
        t_h = (expiry - at).total_seconds() / 3600.0
        iv, exact = market.leg_iv_with_source(at, t_h, kstrike, spot, allow_neighbor=cfg.allow_adjacent_bucket)
        if iv is None and cfg.allow_rv_proxy and np.isfinite(rv[i]) and rv[i] > 0:
            return float(rv[i]), "MODEL_RV_PROXY"
        return iv, ("MODEL_REAL_IV" if exact else "MODEL_REAL_IV_ADJ_BUCKET")

    def fill(leg_kind, k, expiry, at, spot, buy, i):
        if real_mode:
            rq = recorded.quote(leg_kind, k, expiry, at)
            if rq is not None and rq.tier == REAL_QUOTE and rq.mark_iv:
                # long entry pays the recorded ASK, exit receives the recorded BID: never the mark or the mid
                return float(rq.ask if buy else rq.bid), float(rq.mid), float(rq.mark_iv), "REAL_QUOTE"
            if mode == REAL_ONLY:
                why["fail"] = "real_mark_only" if (rq is not None and rq.tier != UNUSABLE) else "no_real_quote"
                return None
        iv, src = leg_iv(at, k, expiry, spot, i)
        if iv is None:
            why["fail"] = "no_iv"
            return None
        t = year_fraction(max(0.0, (expiry - at).total_seconds()))
        mid = float(bs_price(spot, k, t, iv, leg_kind))
        h = max(market.half_spread(k, spot) * mid, market.tick / 2)
        px = np.ceil((mid + h) / market.tick - 1e-9) * market.tick if buy else \
            max(np.floor((mid - h) / market.tick + 1e-9) * market.tick, 0.0)
        return float(px), mid, iv, src

    def leg_greeks(kind, k, expiry, at, spot, i):
        """Real greeks when a recorded quote exists, else the model's. Units of real theta/vega are as recorded."""
        if real_mode:
            rq = recorded.quote(kind, k, expiry, at)
            if rq is not None and rq.tier != UNUSABLE and rq.delta is not None:
                return {"iv": rq.mark_iv, "delta": rq.delta, "gamma": rq.gamma, "theta_day": rq.theta, "vega": rq.vega,
                        "source": "REAL_QUOTE"}
            if mode == REAL_ONLY:
                return None
        iv, _ = leg_iv(at, k, expiry, spot, i)
        if iv is None:
            return None
        g = greeks(spot, k, year_fraction(max(0.0, (expiry - at).total_seconds())), iv, kind)
        return {"iv": iv, "delta": float(g["delta"]), "gamma": float(g["gamma"]), "theta_day": float(g["theta_day"]),
                "vega": float(g["vega"]), "source": "MODELED"}

    def model_theta_day(kind, k, expiry, at, spot, i, iv_hint=None):
        iv = iv_hint if iv_hint else leg_iv(at, k, expiry, spot, i)[0]
        if not iv or not np.isfinite(iv):
            return None
        return float(greeks(spot, k, year_fraction(max(0.0, (expiry - at).total_seconds())), iv, kind)["theta_day"])

    for s in setups:
        i = pos.get(pd.Timestamp(s.timestamp))
        if i is None:
            continue
        if i < next_free:
            skipped["overlap"] += 1
            continue
        spot = idx_close[i]
        if not np.isfinite(spot):
            skipped["no_index"] += 1
            continue
        t_entry = close_t[i]
        snap = recorded.snapshot_at(t_entry) if real_mode else None  # latest snapshot AT OR BEFORE the signal close
        if mode == REAL_ONLY and snap is None:
            skipped["no_real_snapshot"] += 1
            continue
        real_chain = snap is not None
        expiries_i = recorded.expiries(snap) if real_chain else expiries
        basis = spot / close[i]

        def strikes_for(e, _snap=snap, _real=real_chain):
            return recorded.strikes(e, _snap) if _real else market.listed_strikes.get(e, np.array([]))

        def abs_delta_at(expiry, kind, k, _i=i, _spot=spot, _t=t_entry):
            if real_mode:
                rq = recorded.quote(kind, k, expiry, _t)
                if rq is not None and rq.tier != UNUSABLE and rq.delta is not None:
                    return abs(float(rq.delta))
                if mode == REAL_ONLY:
                    return None
            iv, _ = leg_iv(_t, k, expiry, _spot, _i)
            if iv is None:
                return None
            return abs(float(greeks(_spot, k, year_fraction((expiry - _t).total_seconds()), iv, kind)["delta"]))

        pick_detail: dict = {}
        if cfg.contract_picker is not None:
            ctx = PickContext(
                direction=s.direction, policy=policy, underlying=market.underlying, spot=float(spot), t_entry=t_entry,
                hold_hours=hold_h, expiries=list(expiries_i), strikes_for=strikes_for, abs_delta=abs_delta_at,
                fill=lambda kind, k, e, buy, _t=t_entry, _s=spot, _i=i: fill(kind, k, e, _t, _s, buy, _i),
                greek=lambda kind, k, e, _t=t_entry, _s=spot, _i=i: leg_greeks(kind, k, e, _t, _s, _i),
                liquidity=lambda kind, k, e, _t=t_entry: (
                    None if not real_mode else (lambda rq: None if rq is None else {
                        "open_interest": rq.open_interest, "volume": rq.volume, "bid_size": rq.bid_size,
                        "ask_size": rq.ask_size})(recorded.quote(kind, k, e, _t))),
                row={"rv": float(rv[i]) if np.isfinite(rv[i]) else None,
                     "iv_percentile": float(iv_pct[i]) if np.isfinite(iv_pct[i]) else None},
                contracts=contracts, contract_value=cv,
                symbol_for=lambda kind, x, e: market.symbols.get((kind, x, e), ""), real_chain=real_chain)
            pk = cfg.contract_picker(ctx)
            if pk.structure is None or pk.expiry is None:
                skipped[pk.reason or "no_contract_picked"] += 1
                continue
            expiry, st, pick_detail = pk.expiry, pk.structure, pk.detail
            strikes = strikes_for(expiry)
        else:
            expiry = choose_expiry(t_entry, hold_h, expiries_i, sel)
            if expiry is None:
                skipped["no_expiry_for_hold"] += 1
                continue
            strikes = strikes_for(expiry)
            if not len(strikes):
                skipped["no_listed_options"] += 1  # e.g. XAUT before its options were listed (~Jul 2026)
                continue
            try:
                st = build_long(policy, s.direction, market.underlying, spot, expiry, strikes, contracts, cv,
                                lambda kind, k, _e=expiry: abs_delta_at(_e, kind, k), sel,
                                symbol_for=lambda k, x, e: market.symbols.get((k, x, e), ""))
            except ValueError:
                skipped["no_strike_in_delta_band"] += 1
                continue
        why["fail"] = None
        entry = [fill(leg.kind, leg.strike, expiry, t_entry, spot, True, i) for leg in st.legs]
        if any(e is None for e in entry):
            skipped[why["fail"] or "no_iv"] += 1
            continue
        srcs = {e[3] for e in entry}
        source = next((x for x in ("MODEL_RV_PROXY", "MODEL_REAL_IV_ADJ_BUCKET") if x in srcs),
                      "REAL_QUOTE" if srcs == {"REAL_QUOTE"} else "MODEL_REAL_IV")
        units = [st.units(leg) for leg in st.legs]
        entry_px = [e[0] for e in entry]
        fee_in = sum(leg_fee(p, spot, u, cfg.commission_rate, cfg.premium_cap_rate, cfg.gst_rate)
                     for p, u in zip(entry_px, units))
        paid = st.premium_paid(entry_px)
        max_loss = paid + fee_in
        if cfg.apply_breakeven_gate:
            half_spreads = [e[0] - e[1] for e in entry]  # exit spread assumed equal to the entry half-spread
            fees_per_unit = 2 * fee_in / units[0] if units[0] else 0.0  # round trip, per underlying unit
            plan = plan_premium(st, spot, t_entry.to_pydatetime(), [e[2] for e in entry], entry_px, half_spreads,
                                fees_per_unit, s.stop_price * basis, s.target_price * basis, hold_h,
                                cfg.premium_stop_pct, cfg.breakeven_margin,
                                expected_abs_move=s.meta.get("expected_abs_move"))
            if not plan.passes_breakeven:
                skipped["breakeven"] += 1
                continue
        stop_value = paid * (1 - cfg.premium_stop_pct / 100.0)
        guard = expiry - pd.Timedelta(hours=sel.expiry_guard_hours)
        j_exit, reason, level = None, None, None
        last = min(len(frame) - 1, i + strategy.max_hold_bars)
        every = max(1, int(strategy.premium_check_every))
        for j in range(i + 1, last + 1):
            if s.direction == "LONG":
                hit_stop, hit_tgt = lo[j] <= s.stop_price, hi[j] >= s.target_price
            elif s.direction == "SHORT":
                hit_stop, hit_tgt = hi[j] >= s.stop_price, lo[j] <= s.target_price
            else:  # VOL (straddle/strangle): no underlying levels
                hit_stop = hit_tgt = False
            if hit_stop:
                j_exit, reason, level = j, "UNDERLYING_STOP", s.stop_price
            elif hit_tgt:
                j_exit, reason, level = j, "TARGET", s.target_price
            elif close_t[j] >= guard:
                j_exit, reason = j, "EXPIRY_GUARD"
            elif j == last:
                j_exit, reason = j, "TIME_STOP" if j == i + strategy.max_hold_bars else "END_OF_DATA"
            elif (j - i) % every == 0 and np.isfinite(idx_close[j]):
                bids = [fill(leg.kind, leg.strike, expiry, close_t[j], idx_close[j], False, j) for leg in st.legs]
                if all(b is not None for b in bids) and sum(b[0] * u for b, u in zip(bids, units)) <= stop_value:
                    j_exit, reason = j, "PREMIUM_STOP"
            if j_exit is not None:
                break
        if j_exit is None:
            skipped["no_exit_bar"] += 1
            continue
        t_exit = close_t[j_exit]
        if not np.isfinite(idx_close[j_exit]):
            skipped["no_index"] += 1
            continue
        exit_spot = level * idx_close[j_exit] / close[j_exit] if level is not None else idx_close[j_exit]
        why["fail"] = None
        ex = [fill(leg.kind, leg.strike, expiry, t_exit, exit_spot, False, j_exit) for leg in st.legs]
        if any(e is None for e in ex):
            if mode == REAL_ONLY:
                skipped["no_real_exit_quote"] += 1
                unpriced.append((t_entry, max_loss))  # never silently dropped: reported with a worst-case bound
            else:
                skipped["no_iv_exit"] += 1
            continue
        exit_px = [e[0] for e in ex]
        fee_out = sum(leg_fee(p, exit_spot, u, cfg.commission_rate, cfg.premium_cap_rate, cfg.gst_rate)
                      for p, u in zip(exit_px, units))
        net = sum((xp - ep) * u for ep, xp, u in zip(entry_px, exit_px, units)) - fee_in - fee_out
        if s.direction in ("LONG", "SHORT") and s.risk > 0:
            under_exit = level if level is not None else close[j_exit]
            under_r = (1 if s.direction == "LONG" else -1) * (under_exit - s.entry_price) / s.risk
        else:
            under_r = 0.0
        validation = []
        if real is not None:
            for when, bar_i, fills in (("entry", i, entry), ("exit", j_exit, ex)):
                for leg, f in zip(st.legs, fills):
                    p = real.price(leg.symbol, expiry, ts_list[bar_i]) if leg.symbol else None
                    if p is not None:
                        validation.append((when, leg.symbol, f[1], p))
        d0 = abs_delta_at(expiry, st.legs[0].kind, st.legs[0].strike)

        # ---- provenance and attribution (additive; net_pnl = gross_pnl - spread_cost - fees) -----------------------
        def label(fills):
            real_n = sum(1 for f in fills if f[3] == "REAL_QUOTE")
            return "REAL_QUOTE" if real_n == len(fills) else ("MODELED" if real_n == 0 else "MIXED")

        entry_mids, exit_mids = [e[1] for e in entry], [e[1] for e in ex]
        spread_cost = sum((ep - em) * u for ep, em, u in zip(entry_px, entry_mids, units)) \
            + sum((xm - xp) * u for xp, xm, u in zip(exit_px, exit_mids, units))
        gross = sum((xm - em) * u for em, xm, u in zip(entry_mids, exit_mids, units))
        gk = [leg_greeks(leg.kind, leg.strike, expiry, t_entry, spot, i) for leg in st.legs]
        days_held = (t_exit - t_entry).total_seconds() / 86400.0
        th = [model_theta_day(leg.kind, leg.strike, expiry, t_entry, spot, i, e[2])
              for leg, e in zip(st.legs, entry)]
        theta_cost = None if any(x is None for x in th) else float(sum(-x * u for x, u in zip(th, units)) * days_held)
        spreads = [2 * (e[0] - e[1]) / e[1] * 100.0 for e in entry if e[1] > 0]
        feats = {
            "dte_days": (expiry - t_entry).total_seconds() / 86400.0,
            "premium_pct_spot": paid / (spot * units[0]) * 100.0 if units[0] else None,
            "spread_pct": float(np.mean(spreads)) if spreads else None,
            "iv": float(np.mean([e[2] for e in entry])), "rv": float(rv[i]) if np.isfinite(rv[i]) else None,
            "iv_percentile": float(iv_pct[i]) if np.isfinite(iv_pct[i]) else None,
            "strike_dist_pct": float(np.mean([abs(leg.strike - spot) / spot * 100.0 for leg in st.legs])),
            "theta_pct_premium": (float(abs(sum(x * u for x, u in zip(th, units))) / paid * 100.0)
                                  if paid > 0 and all(x is not None for x in th) else None),
            "real_greeks": bool(gk) and all(g is not None and g["source"] == "REAL_QUOTE" for g in gk),
        }
        if feats["rv"]:
            feats["iv_rv"] = feats["iv"] / feats["rv"]
        okg = [g for g in gk if g is not None]
        if okg:
            dl = [abs(g["delta"]) for g in okg if g["delta"] is not None]
            feats["abs_delta"] = float(np.mean(dl)) if dl else None
            feats["gamma"] = float(sum((g["gamma"] or 0.0) * u for g, u in zip(gk, units) if g is not None))
            feats["vega"] = float(sum((g["vega"] or 0.0) * u for g, u in zip(gk, units) if g is not None))
            feats["theta_day"] = float(sum((g["theta_day"] or 0.0) * u for g, u in zip(gk, units) if g is not None))
        if real_mode:
            liq = [recorded.quote(leg.kind, leg.strike, expiry, t_entry) for leg in st.legs]
            if all(q is not None for q in liq):
                feats["open_interest"] = float(min((q.open_interest or 0.0) for q in liq))
                feats["volume"] = float(min((q.volume or 0.0) for q in liq))
        trades.append(OptionTrade(
            strategy=strategy.name, structure=st.name, direction=s.direction, entry_idx=i, exit_idx=j_exit,
            entry_time=t_entry, exit_time=t_exit, exit_reason=reason, expiry=expiry,
            strikes=tuple(leg.strike for leg in st.legs), entry_fills=entry_px, exit_fills=exit_px,
            entry_iv=float(entry[0][2]), fees=fee_in + fee_out, max_loss=max_loss, net_pnl=net,
            net_r=net / max_loss, underlying_r=under_r, premium_source=source, entry_delta=d0, validation=validation,
            entry_price_source=label(entry), exit_price_source=label(ex), entry_mids=entry_mids, exit_mids=exit_mids,
            entry_spot=float(spot), exit_spot=float(exit_spot), spread_cost=float(spread_cost), gross_pnl=float(gross),
            theta_cost_model=theta_cost, entry_features=feats, selection=cfg.selection_name, pick_detail=pick_detail))
        next_free = j_exit + 1
    return OptionBacktestResult(strategy.name, market.underlying, trades, len(setups), skipped, unpriced)


# ---- metrics ---------------------------------------------------------------------------------------------------------
def max_drawdown_r(net_r: np.ndarray) -> float | None:
    if len(net_r) == 0:
        return None
    eq = np.cumsum(net_r)
    return float((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min())


def summarize_trades(trades: list[OptionTrade]) -> dict:
    if not trades:
        return {"n_trades": 0}
    r = np.array([t.net_r for t in trades])
    u = np.array([t.underlying_r for t in trades])
    days = pd.Series([t.entry_time.floor("D") for t in trades])
    return {
        "n_trades": len(trades), "n_days": int(days.nunique()),
        "win_rate": float((r > 0).mean()), "net_expected_r": float(r.mean()), "median_net_r": float(np.median(r)),
        "total_net_r": float(r.sum()), "max_drawdown_r": max_drawdown_r(r),
        "underlying_expected_r": float(u.mean()),
        "avg_fees_share_of_risk": float(np.mean([t.fees / t.max_loss for t in trades])),
        "avg_hold_bars": float(np.mean([t.exit_idx - t.entry_idx for t in trades])),
        "exit_reasons": dict(Counter(t.exit_reason for t in trades)),
        "premium_sources": dict(Counter(t.premium_source for t in trades)),
    }


def validation_stats(trades: list[OptionTrade]) -> dict:
    rows = [(w, m, p) for t in trades for (w, _, m, p) in t.validation if p > 0]
    if not rows:
        return {"n": 0}
    m = np.array([r[1] for r in rows])
    p = np.array([r[2] for r in rows])
    rel = (m - p) / p
    return {"n": len(rows), "median_abs_err_pct": float(np.median(np.abs(rel)) * 100),
            "median_bias_pct": float(np.median(rel) * 100), "p90_abs_err_pct": float(np.percentile(np.abs(rel), 90) * 100)}


def split_in_out(trades: list[OptionTrade], in_sample_frac: float = 0.7) -> tuple[list, list]:
    if not trades:
        return [], []
    cut = trades[0].entry_time + (trades[-1].entry_time - trades[0].entry_time) * in_sample_frac
    return [t for t in trades if t.entry_time <= cut], [t for t in trades if t.entry_time > cut]


def time_folds(trades: list[OptionTrade], n_folds: int = 4) -> list[list[OptionTrade]]:
    if not trades:
        return []
    t0, t1 = trades[0].entry_time, trades[-1].entry_time
    edges = [t0 + (t1 - t0) * k / n_folds for k in range(n_folds + 1)]
    return [[t for t in trades if (edges[k] <= t.entry_time < edges[k + 1]) or (k == n_folds - 1 and t.entry_time == t1)]
            for k in range(n_folds)]
