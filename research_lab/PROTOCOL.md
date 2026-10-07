# Research protocol v1.0 (frozen before any result was produced)

Purpose: find out whether any of 10 long-option strategy families has a robust edge after realistic option costs.
Not to make one look good. This document and the code it describes were frozen **before the first run on real data**.
The SHA-256 of the experiment definition (all variants and parameters, buckets, split date, gates, and the source of
the files that implement the rules) is stored in `FROZEN_PROTOCOL_HASH.txt`, printed in every report, and enforced by
`tests/test_protocol_and_safety.py::test_protocol_matches_the_frozen_hash`. Any later change to a rule, parameter or
gate fails that test and must be re-frozen and disclosed.

Frozen hash: see `FROZEN_PROTOCOL_HASH.txt`.

## Scope
- Assets: BTC, ETH, XAUT (options on Delta Exchange India). Signals come from the underlying's perpetual.
- Signal timeframes: 5m (native) and 15m (causal resample of 5m; only complete 15m bars).
- Long options only: a call (long view), a put (short view), or a call+put straddle. Nothing is ever sold to open.
- Isolation: nothing here is registered with, or changes, the main project. No broker code, no orders.

## Separation of signal and option selection
1. A strategy sees closed underlying bars and returns a direction, an underlying stop and a target (or NO SIGNAL).
2. The project's selector then picks the contract, the project's option market model prices it, and the project's
   backtester applies costs (spread/tick rounding, fees with the premium cap, 18% GST), the breakeven gate, the -35%
   premium stop, the time stop and the 2 h expiry guard. A straddle prices and charges BOTH legs.
3. In live use the risk engine would have the last word. `lab/signal.py` reports its refusal as RISK VETO with its exact
   reason; research runs do not call it.

## The 10 strategies (every threshold is in `lab/config.py`; variants in `lab/library.py`)
1. **Volatility Breakout + Trend.** Bollinger-width percentile (100 bars) <= `squeeze_pct` at least once in the previous
   `compress_window` bars; then a close beyond the previous `range_n`-bar range; EMA20/EMA50, +DI/-DI and ADX >=
   `adx_min` agree. Skipped when the ATR percentile > `ext_atr_pct`, unless the close is >= `cont_atr` ATR beyond the
   range on relative volume >= `cont_rel_vol`. First bar of the breakout only. Call up, put down.
2. **Donchian Breakout.** Previous `n`-bar high/low (current bar excluded); a confirmed CLOSE outside; optional EMA50
   slope, relative-volume and ATR-percentile filters. One signal per breakout episode (re-arms after a close back inside).
3. **VWAP + Momentum Breakout.** Close above (below) the 00:00-UTC session VWAP by `disp_min_atr`..`max_dist_atr` ATR
   (the upper bound is the anti-chase rule), `mom_bars`-bar momentum agreeing, relative volume >= `min_rel_vol`, ATR
   percentile <= `atr_pct_max`, and >= `warmup_min` minutes after the VWAP reset. First bar of the condition only.
4. **Liquidity Sweep + Market Structure Shift.** `swing_n`-bar fractal swings (confirmed `swing_n` bars late). A wick
   through the last confirmed swing low (high) by >= `sweep_thr_atr` ATR that closes back inside; then, within
   `confirm_window` bars and strictly after the sweep bar, a close beyond the swing high (low) that stood at the sweep
   (break of structure). Call after the bullish sequence, put after the bearish one. Stop beyond the sweep extreme.
5. **Funding/OI + Price Divergence.** Price move >= `move_atr` ATR over `lookback` bars. `mode=0` (fade): 7-day OI
   z-score <= -`z_oi` (positioning weakening) with funding z beyond +/-`z_f` on the side of the move, then a bar closing
   against the move: buy the opposite option. `mode=1` (follow): OI z >= `z_oi` (building) with funding NOT crowded,
   then a bar closing with the move: buy the same-direction option. Both hypotheses are tested; neither is assumed.
   Missing funding or OI history means NO SIGNAL.
6. **Long Straddle Volatility Expansion.** At least `min_conditions` of 3: realised-vol percentile, ATR percentile,
   Bollinger-width percentile at or below their thresholds; and, only when ATM IV history exists, IV percentile <=
   `iv_pct_max`. First bar of the condition only; buys ATM call + put. `move_mult` (hypothesis: realised vol expands to
   that multiple of today's) feeds the breakeven gate. Reported: combined premium, break-even move, max loss, fees.
7. **Opening Range Breakout.** Range = high/low of the first `range_min` minutes after the session start (UTC; this
   24x7 perp has no official session, so these are documented assumptions): 0 = 00:00 exchange day, 1 = 13:30 US cash
   open, 2 = 07:00 London. A close beyond the range within `window_hours` of its completion, range width between
   `min_range_atr` and `max_range_atr` ATR. One signal per range per side (`reset_inside=1` allows a new one after a
   close back inside). Partial ranges (data gaps) are not used. Stop = middle of the range.
8. **EMA Trend + ADX.** EMA`fast` > EMA`slow`, close > EMA`fast`, +DI > -DI and ADX >= `adx_min` (mirror for puts); the
   first bar the whole condition becomes true. Low ADX is never traded.
9. **Supertrend + Volatility Filter.** Supertrend(`st_period`, `st_mult`) flips AND an independent filter passes
   (`filter_kind`: 0 none = control, 1 ATR-percentile band, 2 ADX). Same exit geometry as the project's two active (weak)
   variants (stop = Supertrend line, target 2.25 ATR). The two legacy variants are also re-run unchanged.
10. **Mean-Reversion Extreme.** |close - fair| / ATR >= `z_min` (fair = session VWAP or EMA50) within the last
    `ext_window` bars, AND a rejection bar (wick >= `wick_frac` of range, closing back toward fair) that recovered >=
    `confirm_atr` ATR from the extreme, AND realised vol not expanding faster than `vol_exp_max`, ATR percentile <=
    `atr_pct_max`. Buys the option AGAINST the extreme; target = fair value. Overbought/oversold alone never triggers.

## Variants (pre-declared hypotheses, 2-3 per strategy, no grid)
See `lab/library.py` (`VARIANTS`). The only local search is the +-20% perturbation of each variant's key parameters,
used for robustness, never for choosing a variant.

## Option-selection buckets (the project's selector reaches ATM and 1-ITM strikes only)
`ATM_040_060` (baseline: ATM, |delta| 0.40-0.60), `ATM_030_060`, `ITM1_050_070`; DTE multiple 2.5x (baseline) and 4.0x
(sensitivity). No OTM bucket exists in the project's selector, so none is tested. No bucket is chosen per strategy.

## Data splits (no fitting happens, so nothing is trained)
- Discovery: before 2026-07-01. Holdout: 2026-07-01 onward (frozen; the same split the project's earlier search used).
- Walk-forward: frozen parameters evaluated over 4 consecutive time slices (reported per cell).
- Parameters are shared across assets. Per-asset results are reported and never tuned.

## Gates and statuses (nothing is lowered)
- **DATA-INSUFFICIENT:** < 200 pooled trades, or < 30 trades in the holdout.
- **REJECTED:** enough trades, but pooled net R in the holdout is not positive after costs.
- **ACCEPTED:** holdout net R > 0; every +-20% perturbation of every key parameter keeps pooled net R > 0; >= 2 assets
  with >= 30 trades and positive net R; positive holdout net R in >= 2 of the 3 option buckets; AND a significant edge
  over 300 random-signal runs (same entry times, selector and costs; directions flipped at random, straddles use random
  entry times) with a Bonferroni-adjusted p <= 0.05 over the strategy's cells.
- **EXPERIMENTAL:** holdout net R > 0 but at least one other gate failed or did not run. Never "proven".
- Strategy-level status is the best status among its cells; the full cell table is the real result.
- Staging: perturbation/bucket runs only for cells with >= 200 trades and a positive holdout; the random control only
  for cells that passed every other gate. A cell that failed an earlier gate cannot change status.

## Honest limitations (also in every report)
Option prices are modelled (no recorded chain history exists yet); spreads are an unverified pessimistic calibration;
the 18% GST is unverified; XAUT has about two months of options; funding/OI coverage is reported per run; no OTM bucket;
many cells are tested, so some positives are expected by chance.
