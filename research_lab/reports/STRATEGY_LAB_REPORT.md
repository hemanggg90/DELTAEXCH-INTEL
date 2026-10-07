# Strategy library research report: 10 long-option strategies on BTC, ETH and XAUT

Generated 2026-10-02 19:24 UTC by `research_lab/run_research.py`. Protocol version 1.0, protocol hash `eacf41c67f6a67ee12466efb1464b1a01cadb0d60dc43df40e7e9dd576b1490b`. Data window from 2025-12-01; signal timeframes 5m, 15m.

## Result: 0 of 10 strategies ACCEPTED

Strategy-level status is the BEST status among that strategy's tested cells (variant x timeframe). Reading it alone flatters a strategy that was given several chances, which is why the acceptance gate includes a Bonferroni adjustment over those cells. The full cell table below is the real result.

Status counts (strategies): ACCEPTED 0, EXPERIMENTAL 2, REJECTED 8, DATA-INSUFFICIENT 0

| Strategy | Status | Cells tested | ACCEPTED | EXPERIMENTAL | REJECTED | DATA-INSUFFICIENT |
|---|---|---|---|---|---|---|
| S1 Volatility Breakout + Trend | **EXPERIMENTAL** | 6 | 0 | 1 | 3 | 2 |
| S2 Donchian Breakout | **EXPERIMENTAL** | 6 | 0 | 1 | 5 | 0 |
| S3 VWAP + Momentum Breakout | **REJECTED** | 4 | 0 | 0 | 4 | 0 |
| S4 Liquidity Sweep + Market Structure Shift | **REJECTED** | 4 | 0 | 0 | 4 | 0 |
| S5 Funding/OI + Price Divergence | **REJECTED** | 4 | 0 | 0 | 1 | 3 |
| S6 Long Straddle Volatility Expansion | **REJECTED** | 6 | 0 | 0 | 6 | 0 |
| S7 Opening Range Breakout | **REJECTED** | 6 | 0 | 0 | 4 | 2 |
| S8 EMA Trend + ADX | **REJECTED** | 6 | 0 | 0 | 6 | 0 |
| S9 Supertrend + Volatility Filter | **REJECTED** | 6 | 0 | 0 | 3 | 3 |
| S10 Mean-Reversion Extreme | **REJECTED** | 6 | 0 | 0 | 6 | 0 |
| Supertrend (project's active variants, legacy) | **DATA-INSUFFICIENT** | 2 | 0 | 0 | 0 | 2 |

## Methodology

- **Question:** does any of these 10 hypotheses have a robust edge after realistic option costs? The method was frozen before the first run (hash above); no parameter was changed after seeing a result.
- **Signals** are generated on the underlying's perpetual from closed bars only (no look-ahead; unit tests prove it by truncation). Warm-up or missing inputs give NO SIGNAL.
- **Option selection is separate** and is the project's own: the chosen contract is priced by the project's option market model (Black-Scholes at the as-of ATM IV inferred from real Delta option trades, plus a fitted smile), entered at the modelled ask, exited at the modelled bid, with the breakeven gate, -35% premium stop, time stop, underlying stop/target and the 2 h expiry guard. One trade at a time per strategy.
- **Option buckets** (project selector reaches ATM and 1-ITM only): `ATM_040_060` (ATM, |delta| 0.4-0.6), `ATM_030_060` (ATM, |delta| 0.3-0.6), `ITM1_050_070` (ITM1, |delta| 0.5-0.7); DTE multiples 2.5, 4.0. The baseline bucket is `ATM_040_060` at 2.5x; the others are consistency checks, never chosen per strategy.
- **No fitting happens anywhere**, so walk-forward is frozen parameters evaluated forward: discovery before 2026-07-01, a frozen holdout after it, and 4 consecutive time folds. Parameters are shared across assets; per-asset results are reported, never tuned.
- **Gates:** >= 200 pooled trades (else DATA-INSUFFICIENT, never lowered); >= 30 holdout trades; holdout net R > 0 (else REJECTED); every +-20% perturbation of each key parameter keeps pooled net R > 0; >= 2 assets with >= 30 trades and positive net R; positive holdout net R in >= 2 of 3 option buckets; and beating 300 random-signal runs (same entry times, selector and costs; directions flipped at random; straddles use random entry times) with a Bonferroni-adjusted p <= 0.05. Cells that pass the earlier gates but not all of them are EXPERIMENTAL.
- **Staged runs:** perturbation/bucket runs happen only for cells with >= 200 trades and a positive holdout, and the random control only for cells that passed every other gate. A cell that failed an earlier gate cannot change status, so running the rest would only cost time. 'not run' means exactly that.

## Datasets

| Asset | Timeframe | Bars | From | To | Funding z coverage | OI z coverage | Index coverage | IV pct coverage |
|---|---|---|---|---|---|---|---|---|
| BTC | 5m | 88070 | 2025-12-01 | 2026-10-02 | 100% | 100% | 100% | 99% |
| BTC | 15m | 29356 | 2025-12-01 | 2026-10-02 | 100% | 100% | 100% | 99% |
| ETH | 5m | 88070 | 2025-12-01 | 2026-10-02 | 100% | 100% | 100% | 98% |
| ETH | 15m | 29356 | 2025-12-01 | 2026-10-02 | 100% | 100% | 100% | 98% |
| XAUT | 5m | 48461 | 2026-04-17 | 2026-10-02 | 99% | 98% | 100% | 35% |
| XAUT | 15m | 16153 | 2026-04-17 | 2026-10-02 | 99% | 98% | 100% | 35% |

Listed option expiries available for pricing:

- BTC: 306 expiries, 2025-12-01 to 2026-10-02
- ETH: 306 expiries, 2025-12-01 to 2026-10-02
- XAUT: 63 expiries, 2026-07-24 to 2026-10-02

## Parameters and research ranges

Each variant is a pre-declared hypothesis; the +-20% perturbation of each key parameter is the only local search, and it is used for robustness, not selection.

| Variant | Strategy | Parameters | Key parameters perturbed +-20% |
|---|---|---|---|
| S1-base | S1 Volatility Breakout + Trend | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 6.0, "cooldown_bars": 6, "squeeze_pct": 0.2, "compress_window": 12, "range_n": 20, "adx_min": 20.0, "ext_atr_pct": 0.9, "cont_atr": 0.25, "cont_rel_vol": 1.2}` | squeeze_pct, range_n, adx_min, rr |
| S1-strict | S1 Volatility Breakout + Trend | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 6.0, "cooldown_bars": 6, "squeeze_pct": 0.1, "compress_window": 12, "range_n": 20, "adx_min": 25.0, "ext_atr_pct": 0.9, "cont_atr": 0.25, "cont_rel_vol": 1.2}` | squeeze_pct, range_n, adx_min, rr |
| S1-lenient | S1 Volatility Breakout + Trend | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 6.0, "cooldown_bars": 6, "squeeze_pct": 0.3, "compress_window": 12, "range_n": 20, "adx_min": 15.0, "ext_atr_pct": 0.9, "cont_atr": 0.25, "cont_rel_vol": 1.2}` | squeeze_pct, range_n, adx_min, rr |
| S2-n20 | S2 Donchian Breakout | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 6.0, "cooldown_bars": 1, "n": 20, "trend_filter": 0, "min_rel_vol": 0.0, "atr_pct_max": 1.0}` | n, rr, stop_atr |
| S2-n40 | S2 Donchian Breakout | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 4.0, "max_hold_hours": 12.0, "cooldown_bars": 1, "n": 40, "trend_filter": 0, "min_rel_vol": 0.0, "atr_pct_max": 1.0}` | n, rr, stop_atr |
| S2-n20-filtered | S2 Donchian Breakout | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 6.0, "cooldown_bars": 1, "n": 20, "trend_filter": 1, "min_rel_vol": 1.0, "atr_pct_max": 1.0}` | n, rr, stop_atr |
| S3-base | S3 VWAP + Momentum Breakout | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 5.0, "cooldown_bars": 6, "disp_min_atr": 1.0, "max_dist_atr": 3.0, "mom_bars": 12, "min_rel_vol": 1.0, "atr_pct_max": 0.9, "warmup_min": 120.0}` | disp_min_atr, max_dist_atr, min_rel_vol, rr |
| S3-strict | S3 VWAP + Momentum Breakout | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 5.0, "cooldown_bars": 6, "disp_min_atr": 1.5, "max_dist_atr": 4.0, "mom_bars": 12, "min_rel_vol": 1.5, "atr_pct_max": 0.9, "warmup_min": 120.0}` | disp_min_atr, max_dist_atr, min_rel_vol, rr |
| S4-base | S4 Liquidity Sweep + Market Structure Shift | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 3.0, "max_hold_hours": 8.0, "cooldown_bars": 6, "swing_n": 5, "sweep_thr_atr": 0.1, "confirm_window": 6, "stop_buf_atr": 0.25}` | swing_n, sweep_thr_atr, confirm_window, rr |
| S4-slow | S4 Liquidity Sweep + Market Structure Shift | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 4.0, "max_hold_hours": 10.0, "cooldown_bars": 6, "swing_n": 8, "sweep_thr_atr": 0.1, "confirm_window": 12, "stop_buf_atr": 0.25}` | swing_n, sweep_thr_atr, confirm_window, rr |
| S5-fade | S5 Funding/OI + Price Divergence | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 3.0, "max_hold_hours": 8.0, "cooldown_bars": 12, "mode": 0, "lookback": 12, "move_atr": 1.5, "z_oi": 1.0, "z_f": 1.0}` | move_atr, z_oi, z_f, rr |
| S5-follow | S5 Funding/OI + Price Divergence | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 3.0, "max_hold_hours": 8.0, "cooldown_bars": 12, "mode": 1, "lookback": 12, "move_atr": 1.5, "z_oi": 1.0, "z_f": 1.0}` | move_atr, z_oi, z_f, rr |
| S6-base | S6 Long Straddle Volatility Expansion | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 6.0, "max_hold_hours": 12.0, "cooldown_bars": 72, "rv_pct_max": 0.15, "atr_pct_max": 0.15, "bbw_pct_max": 0.15, "min_conditions": 2, "iv_pct_max": 100.0, "move_mult": 2.0}` | rv_pct_max, atr_pct_max, bbw_pct_max, move_mult |
| S6-loose | S6 Long Straddle Volatility Expansion | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 6.0, "max_hold_hours": 12.0, "cooldown_bars": 72, "rv_pct_max": 0.25, "atr_pct_max": 0.25, "bbw_pct_max": 0.25, "min_conditions": 2, "iv_pct_max": 100.0, "move_mult": 2.0}` | rv_pct_max, atr_pct_max, bbw_pct_max, move_mult |
| S6-all3 | S6 Long Straddle Volatility Expansion | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 6.0, "max_hold_hours": 12.0, "cooldown_bars": 72, "rv_pct_max": 0.2, "atr_pct_max": 0.2, "bbw_pct_max": 0.2, "min_conditions": 3, "iv_pct_max": 100.0, "move_mult": 2.0}` | rv_pct_max, atr_pct_max, bbw_pct_max, move_mult |
| S7-us-open | S7 Opening Range Breakout | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 5.0, "cooldown_bars": 1, "session": 1, "range_min": 30, "window_hours": 4.0, "min_range_atr": 0.5, "max_range_atr": 6.0, "min_rel_vol": 0.0, "reset_inside": 0}` | range_min, window_hours, min_range_atr, rr |
| S7-london | S7 Opening Range Breakout | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 5.0, "cooldown_bars": 1, "session": 2, "range_min": 30, "window_hours": 4.0, "min_range_atr": 0.5, "max_range_atr": 6.0, "min_rel_vol": 0.0, "reset_inside": 0}` | range_min, window_hours, min_range_atr, rr |
| S7-utc-day | S7 Opening Range Breakout | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 5.0, "cooldown_bars": 1, "session": 0, "range_min": 30, "window_hours": 4.0, "min_range_atr": 0.5, "max_range_atr": 6.0, "min_rel_vol": 0.0, "reset_inside": 0}` | range_min, window_hours, min_range_atr, rr |
| S8-20-50 | S8 EMA Trend + ADX | `{"stop_atr": 2.0, "rr": 2.0, "hold_hours": 3.0, "max_hold_hours": 8.0, "cooldown_bars": 12, "fast": 20, "slow": 50, "adx_min": 20.0}` | fast, slow, adx_min, rr |
| S8-20-50-adx25 | S8 EMA Trend + ADX | `{"stop_atr": 2.0, "rr": 2.0, "hold_hours": 3.0, "max_hold_hours": 8.0, "cooldown_bars": 12, "fast": 20, "slow": 50, "adx_min": 25.0}` | fast, slow, adx_min, rr |
| S8-50-200 | S8 EMA Trend + ADX | `{"stop_atr": 2.0, "rr": 2.0, "hold_hours": 6.0, "max_hold_hours": 16.0, "cooldown_bars": 12, "fast": 50, "slow": 200, "adx_min": 20.0}` | fast, slow, adx_min, rr |
| S9-atr-band | S9 Supertrend + Volatility Filter | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 4.0, "cooldown_bars": 1, "st_period": 10, "st_mult": 3.0, "filter_kind": 1, "atr_lo": 0.2, "atr_hi": 0.8, "adx_min": 20.0, "target_atr_mult": 2.25}` | st_mult, atr_lo, atr_hi |
| S9-adx | S9 Supertrend + Volatility Filter | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 4.0, "cooldown_bars": 1, "st_period": 10, "st_mult": 3.0, "filter_kind": 2, "atr_lo": 0.2, "atr_hi": 0.8, "adx_min": 20.0, "target_atr_mult": 2.25}` | st_mult, adx_min |
| S9-control | S9 Supertrend + Volatility Filter | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 4.0, "cooldown_bars": 1, "st_period": 10, "st_mult": 3.0, "filter_kind": 0, "atr_lo": 0.2, "atr_hi": 0.8, "adx_min": 20.0, "target_atr_mult": 2.25}` | st_mult, target_atr_mult |
| S10-vwap | S10 Mean-Reversion Extreme | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 5.0, "cooldown_bars": 12, "fair": 0, "z_min": 2.5, "ext_window": 3, "confirm_atr": 0.5, "wick_frac": 0.4, "vol_exp_max": 1.5, "atr_pct_max": 0.9, "warmup_min": 120.0, "stop_buf_atr": 0.25}` | z_min, confirm_atr, wick_frac, vol_exp_max |
| S10-ema50 | S10 Mean-Reversion Extreme | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 5.0, "cooldown_bars": 12, "fair": 1, "z_min": 2.5, "ext_window": 3, "confirm_atr": 0.5, "wick_frac": 0.4, "vol_exp_max": 1.5, "atr_pct_max": 0.9, "warmup_min": 120.0, "stop_buf_atr": 0.25}` | z_min, confirm_atr, wick_frac, vol_exp_max |
| S10-vwap-z3 | S10 Mean-Reversion Extreme | `{"stop_atr": 1.5, "rr": 2.0, "hold_hours": 2.0, "max_hold_hours": 5.0, "cooldown_bars": 12, "fair": 0, "z_min": 3.0, "ext_window": 3, "confirm_atr": 0.5, "wick_frac": 0.4, "vol_exp_max": 1.5, "atr_pct_max": 0.9, "warmup_min": 120.0, "stop_buf_atr": 0.25}` | z_min, confirm_atr, wick_frac, vol_exp_max |

Legacy variants (the project's own `SupertrendFlip`, target 2.25 ATR, hold 2h/4h): `LEGACY-ST-tight-ATM` (ATM), `LEGACY-ST-tight-ITM1` (ITM1).

## All cells: status and why

Net R = net P&L / (premium paid + entry fees), after the project's modelled bid/ask spread, fees (0.01% of notional, capped at 3.5% of premium) and 18% GST (unverified), on BOTH legs for straddles. Holdout = trades entered on or after 2026-07-01 (never used to choose anything). Folds = mean net R in 4 consecutive time slices. Gate letters: perturbation, assets, buckets, control (ok / NO / - = not run).

| Variant | TF | Status | Trades | Pooled net R | Discovery net R (n) | Holdout net R (n) | Folds | Per asset net R (n) | Gates | Reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| S1-base | 15m | **EXPERIMENTAL** | 319 | -0.026 | -0.040 (215) | +0.004 (104) | -0.05 / -0.00 / -0.07 / +0.03 | BTC +0.002 (166) / ETH -0.086 (134) / XAUT +0.153 (19) | perturbation:NO assets:NO buckets:NO control:- | perturbation: 0/8 perturbations positive (failing: squeeze_pctx0.8, squeeze_pctx1.2, range_nx0.8, range_nx1.2, adx_minx0.8, adx_minx1.2, rrx0.8, rrx1.2); assets: positive on 1 asset(s) with >= 30 trades: BTC; buckets: holdout net R positive in 1/3 option buckets; control: not run |
| S1-base | 5m | **REJECTED** | 236 | -0.067 | -0.061 (180) | -0.089 (56) | -0.07 / -0.06 / -0.04 / -0.12 | BTC -0.128 (124) / ETH +0.002 (104) / XAUT -0.027 (8) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.089 (not positive after costs) |
| S1-lenient | 15m | **REJECTED** | 532 | -0.024 | -0.032 (357) | -0.007 (175) | -0.04 / -0.04 / -0.02 / -0.00 | BTC -0.014 (281) / ETH -0.057 (218) / XAUT +0.110 (33) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.007 (not positive after costs) |
| S1-lenient | 5m | **REJECTED** | 360 | -0.077 | -0.081 (261) | -0.067 (99) | -0.08 / -0.08 / -0.08 / -0.07 | BTC -0.103 (187) / ETH -0.056 (164) / XAUT +0.096 (9) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.067 (not positive after costs) |
| S1-strict | 15m | **DATA-INSUFFICIENT** | 157 | -0.077 | -0.080 (106) | -0.072 (51) | -0.04 / -0.08 / -0.10 / -0.06 | BTC -0.034 (86) / ETH -0.133 (67) / XAUT -0.073 (4) | perturbation:- assets:NO buckets:- control:- | 157 trades (need 200); 363 setups |
| S1-strict | 5m | **DATA-INSUFFICIENT** | 109 | -0.162 | -0.139 (90) | -0.272 (19) | -0.21 / -0.16 / -0.06 / -0.31 | BTC -0.169 (62) / ETH -0.146 (45) / XAUT -0.303 (2) | perturbation:- assets:NO buckets:- control:- | 109 trades (need 200); 1127 setups |
| S10-ema50 | 15m | **REJECTED** | 514 | -0.143 | -0.138 (322) | -0.150 (192) | -0.15 / -0.16 / -0.09 / -0.17 | BTC -0.129 (266) / ETH -0.157 (218) / XAUT -0.160 (30) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.150 (not positive after costs) |
| S10-ema50 | 5m | **REJECTED** | 383 | -0.105 | -0.078 (267) | -0.166 (116) | -0.09 / -0.03 / -0.15 / -0.15 | BTC -0.105 (197) / ETH -0.102 (176) / XAUT -0.151 (10) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.166 (not positive after costs) |
| S10-vwap | 15m | **REJECTED** | 436 | -0.121 | -0.124 (271) | -0.114 (165) | -0.11 / -0.17 / -0.04 / -0.15 | BTC -0.127 (218) / ETH -0.117 (188) / XAUT -0.094 (30) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.114 (not positive after costs) |
| S10-vwap | 5m | **REJECTED** | 1127 | -0.109 | -0.101 (722) | -0.123 (405) | -0.10 / -0.09 / -0.11 / -0.13 | BTC -0.122 (567) / ETH -0.099 (482) / XAUT -0.075 (78) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.123 (not positive after costs) |
| S10-vwap-z3 | 15m | **REJECTED** | 394 | -0.138 | -0.154 (250) | -0.110 (144) | -0.14 / -0.20 / -0.06 / -0.15 | BTC -0.158 (195) / ETH -0.124 (169) / XAUT -0.085 (30) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.110 (not positive after costs) |
| S10-vwap-z3 | 5m | **REJECTED** | 1133 | -0.111 | -0.103 (726) | -0.124 (407) | -0.11 / -0.09 / -0.11 / -0.13 | BTC -0.125 (571) / ETH -0.099 (483) / XAUT -0.078 (79) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.124 (not positive after costs) |
| S2-n20 | 15m | **REJECTED** | 1716 | -0.104 | -0.115 (1112) | -0.085 (604) | -0.11 / -0.13 / -0.10 / -0.09 | BTC -0.106 (867) / ETH -0.109 (731) / XAUT -0.068 (118) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.085 (not positive after costs) |
| S2-n20 | 5m | **REJECTED** | 1330 | -0.053 | -0.056 (932) | -0.047 (398) | -0.01 / -0.09 / -0.07 / -0.05 | BTC -0.048 (699) / ETH -0.060 (594) / XAUT -0.028 (37) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.047 (not positive after costs) |
| S2-n20-filtered | 15m | **REJECTED** | 1313 | -0.085 | -0.087 (832) | -0.082 (481) | -0.07 / -0.11 / -0.09 / -0.07 | BTC -0.078 (665) / ETH -0.099 (553) / XAUT -0.059 (95) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.082 (not positive after costs) |
| S2-n20-filtered | 5m | **REJECTED** | 1125 | -0.047 | -0.054 (787) | -0.031 (338) | -0.02 / -0.06 / -0.09 / -0.02 | BTC -0.038 (591) / ETH -0.060 (506) / XAUT +0.001 (28) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.031 (not positive after costs) |
| S2-n40 | 15m | **REJECTED** | 926 | -0.058 | -0.072 (611) | -0.032 (315) | -0.08 / -0.06 / -0.07 / -0.04 | BTC -0.059 (464) / ETH -0.058 (404) / XAUT -0.053 (58) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.032 (not positive after costs) |
| S2-n40 | 5m | **EXPERIMENTAL** | 661 | -0.030 | -0.045 (464) | +0.005 (197) | -0.01 / -0.06 / -0.06 / +0.01 | BTC -0.026 (347) / ETH -0.041 (291) / XAUT +0.044 (23) | perturbation:NO assets:NO buckets:ok control:- | perturbation: 2/6 perturbations positive (failing: nx0.8, nx1.2, rrx1.2, stop_atrx1.2); assets: positive on 0 asset(s) with >= 30 trades; control: not run |
| S3-base | 15m | **REJECTED** | 1368 | -0.118 | -0.123 (876) | -0.109 (492) | -0.12 / -0.14 / -0.12 / -0.10 | BTC -0.095 (679) / ETH -0.153 (584) / XAUT -0.071 (105) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.109 (not positive after costs) |
| S3-base | 5m | **REJECTED** | 691 | -0.096 | -0.103 (493) | -0.077 (198) | -0.13 / -0.11 / -0.06 / -0.08 | BTC -0.075 (369) / ETH -0.117 (315) / XAUT -0.266 (7) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.077 (not positive after costs) |
| S3-strict | 15m | **REJECTED** | 819 | -0.079 | -0.086 (529) | -0.064 (290) | -0.01 / -0.17 / -0.08 / -0.05 | BTC -0.089 (420) / ETH -0.085 (334) / XAUT +0.020 (65) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.064 (not positive after costs) |
| S3-strict | 5m | **REJECTED** | 485 | -0.073 | -0.070 (346) | -0.083 (139) | -0.04 / -0.08 / -0.08 / -0.11 | BTC -0.094 (248) / ETH -0.040 (229) / XAUT -0.376 (8) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.083 (not positive after costs) |
| S4-base | 15m | **REJECTED** | 458 | -0.134 | -0.142 (277) | -0.120 (181) | -0.08 / -0.08 / -0.24 / -0.13 | BTC -0.098 (215) / ETH -0.195 (195) / XAUT -0.043 (48) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.120 (not positive after costs) |
| S4-base | 5m | **REJECTED** | 735 | -0.120 | -0.087 (440) | -0.169 (295) | -0.08 / -0.08 / -0.13 / -0.16 | BTC -0.124 (354) / ETH -0.115 (318) / XAUT -0.126 (63) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.169 (not positive after costs) |
| S4-slow | 15m | **REJECTED** | 406 | -0.083 | -0.094 (249) | -0.066 (157) | -0.14 / +0.06 / -0.18 / -0.05 | BTC -0.038 (178) / ETH -0.136 (184) / XAUT -0.050 (44) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.066 (not positive after costs) |
| S4-slow | 5m | **REJECTED** | 757 | -0.057 | -0.060 (451) | -0.053 (306) | +0.01 / -0.06 / -0.16 / -0.02 | BTC -0.055 (370) / ETH -0.075 (324) / XAUT +0.021 (63) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.053 (not positive after costs) |
| S5-fade | 15m | **DATA-INSUFFICIENT** | 53 | -0.047 | +0.024 (35) | -0.184 (18) | +0.01 / +0.08 / +0.01 / -0.43 | BTC -0.062 (25) / ETH -0.033 (28) / XAUT - (0) | perturbation:- assets:NO buckets:- control:- | 53 trades (need 200); 85 setups |
| S5-fade | 5m | **DATA-INSUFFICIENT** | 46 | -0.016 | +0.097 (31) | -0.249 (15) | +0.07 / +0.05 / +0.06 / -0.28 | BTC +0.024 (24) / ETH -0.060 (22) / XAUT - (0) | perturbation:- assets:NO buckets:- control:- | 46 trades (need 200); 209 setups |
| S5-follow | 15m | **REJECTED** | 358 | -0.168 | -0.158 (238) | -0.187 (120) | -0.14 / -0.16 / -0.15 / -0.21 | BTC -0.144 (179) / ETH -0.196 (147) / XAUT -0.169 (32) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.187 (not positive after costs) |
| S5-follow | 5m | **DATA-INSUFFICIENT** | 146 | -0.124 | -0.138 (101) | -0.092 (45) | -0.05 / -0.32 / -0.07 / -0.11 | BTC -0.124 (78) / ETH -0.147 (62) / XAUT +0.110 (6) | perturbation:- assets:NO buckets:- control:- | 146 trades (need 200); 2314 setups |
| S6-all3 | 15m | **REJECTED** | 301 | -0.005 | +0.002 (203) | -0.020 (98) | +0.01 / -0.02 / -0.02 / +0.00 | BTC +0.058 (144) / ETH -0.054 (130) / XAUT -0.104 (27) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.020 (not positive after costs) |
| S6-all3 | 5m | **REJECTED** | 641 | -0.073 | -0.055 (422) | -0.107 (219) | -0.02 / -0.08 / -0.08 / -0.10 | BTC -0.030 (311) / ETH -0.113 (292) / XAUT -0.108 (38) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.107 (not positive after costs) |
| S6-base | 15m | **REJECTED** | 331 | -0.031 | -0.021 (218) | -0.051 (113) | -0.03 / -0.02 / -0.01 / -0.05 | BTC +0.024 (160) / ETH -0.073 (141) / XAUT -0.125 (30) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.051 (not positive after costs) |
| S6-base | 5m | **REJECTED** | 686 | -0.086 | -0.063 (460) | -0.132 (226) | -0.07 / -0.07 / -0.06 / -0.14 | BTC -0.049 (324) / ETH -0.115 (324) / XAUT -0.154 (38) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.132 (not positive after costs) |
| S6-loose | 15m | **REJECTED** | 355 | -0.023 | -0.017 (238) | -0.034 (117) | +0.01 / +0.01 / -0.11 / -0.01 | BTC +0.040 (170) / ETH -0.069 (154) / XAUT -0.138 (31) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.034 (not positive after costs) |
| S6-loose | 5m | **REJECTED** | 737 | -0.083 | -0.054 (490) | -0.140 (247) | -0.08 / -0.07 / -0.03 / -0.14 | BTC -0.051 (352) / ETH -0.114 (348) / XAUT -0.098 (37) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.140 (not positive after costs) |
| S7-london | 15m | **DATA-INSUFFICIENT** | 82 | -0.105 | -0.120 (41) | -0.090 (41) | +0.09 / -0.31 / -0.11 / -0.13 | BTC +0.062 (27) / ETH -0.147 (31) / XAUT -0.240 (24) | perturbation:- assets:NO buckets:- control:- | 82 trades (need 200); 1007 setups |
| S7-london | 5m | **DATA-INSUFFICIENT** | 43 | -0.038 | -0.068 (22) | -0.006 (21) | -0.32 / -0.27 / +0.25 / -0.01 | BTC +0.285 (12) / ETH -0.154 (18) / XAUT -0.174 (13) | perturbation:- assets:NO buckets:- control:- | 43 trades (need 200); 1089 setups |
| S7-us-open | 15m | **REJECTED** | 366 | -0.108 | -0.096 (223) | -0.128 (143) | -0.07 / -0.10 / -0.15 / -0.10 | BTC -0.099 (181) / ETH -0.096 (158) / XAUT -0.243 (27) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.128 (not positive after costs) |
| S7-us-open | 5m | **REJECTED** | 304 | -0.110 | -0.155 (181) | -0.045 (123) | -0.21 / -0.15 / -0.14 / -0.01 | BTC -0.137 (156) / ETH -0.072 (128) / XAUT -0.151 (20) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.045 (not positive after costs) |
| S7-utc-day | 15m | **REJECTED** | 447 | -0.051 | -0.060 (282) | -0.035 (165) | -0.03 / -0.08 / -0.05 / -0.05 | BTC -0.001 (229) / ETH -0.097 (188) / XAUT -0.142 (30) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.035 (not positive after costs) |
| S7-utc-day | 5m | **REJECTED** | 359 | -0.028 | -0.032 (219) | -0.022 (140) | +0.09 / -0.07 / -0.10 / -0.02 | BTC -0.020 (185) / ETH -0.036 (152) / XAUT -0.043 (22) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.022 (not positive after costs) |
| S8-20-50 | 15m | **REJECTED** | 1362 | -0.105 | -0.101 (887) | -0.114 (475) | -0.10 / -0.10 / -0.11 / -0.11 | BTC -0.085 (646) / ETH -0.124 (619) / XAUT -0.121 (97) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.114 (not positive after costs) |
| S8-20-50 | 5m | **REJECTED** | 1597 | -0.130 | -0.129 (1092) | -0.133 (505) | -0.13 / -0.14 / -0.10 / -0.15 | BTC -0.117 (818) / ETH -0.139 (720) / XAUT -0.215 (59) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.133 (not positive after costs) |
| S8-20-50-adx25 | 15m | **REJECTED** | 1105 | -0.080 | -0.076 (717) | -0.088 (388) | -0.09 / -0.08 / -0.07 / -0.09 | BTC -0.060 (526) / ETH -0.098 (505) / XAUT -0.100 (74) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.088 (not positive after costs) |
| S8-20-50-adx25 | 5m | **REJECTED** | 1348 | -0.124 | -0.124 (918) | -0.123 (430) | -0.11 / -0.14 / -0.10 / -0.14 | BTC -0.101 (679) / ETH -0.136 (614) / XAUT -0.270 (55) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.123 (not positive after costs) |
| S8-50-200 | 15m | **REJECTED** | 808 | -0.072 | -0.085 (544) | -0.044 (264) | -0.09 / -0.10 / -0.05 / -0.05 | BTC -0.078 (382) / ETH -0.060 (381) / XAUT -0.117 (45) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.044 (not positive after costs) |
| S8-50-200 | 5m | **REJECTED** | 591 | -0.105 | -0.104 (427) | -0.107 (164) | -0.09 / -0.13 / -0.13 / -0.06 | BTC -0.106 (307) / ETH -0.097 (268) / XAUT -0.206 (16) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.107 (not positive after costs) |
| S9-adx | 15m | **REJECTED** | 329 | -0.127 | -0.157 (226) | -0.060 (103) | -0.13 / -0.20 / -0.07 / -0.12 | BTC -0.145 (169) / ETH -0.102 (150) / XAUT -0.198 (10) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.060 (not positive after costs) |
| S9-adx | 5m | **DATA-INSUFFICIENT** | 103 | +0.135 | +0.172 (78) | +0.019 (25) | +0.26 / -0.00 / +0.13 / -0.01 | BTC +0.133 (57) / ETH +0.150 (45) / XAUT -0.410 (1) | perturbation:- assets:ok buckets:- control:- | 103 trades (need 200); 3776 setups |
| S9-atr-band | 15m | **REJECTED** | 278 | -0.127 | -0.155 (181) | -0.074 (97) | -0.21 / -0.15 / -0.06 / -0.12 | BTC -0.129 (147) / ETH -0.123 (125) / XAUT -0.167 (6) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.074 (not positive after costs) |
| S9-atr-band | 5m | **DATA-INSUFFICIENT** | 29 | +0.209 | +0.284 (23) | -0.077 (6) | +0.30 / - / +0.04 / +0.09 | BTC +0.289 (15) / ETH +0.124 (14) / XAUT - (0) | perturbation:- assets:NO buckets:- control:- | 29 trades (need 200); 3728 setups |
| S9-control | 15m | **REJECTED** | 549 | -0.124 | -0.148 (373) | -0.072 (176) | -0.16 / -0.17 / -0.06 / -0.12 | BTC -0.134 (289) / ETH -0.103 (246) / XAUT -0.265 (14) | perturbation:- assets:NO buckets:- control:- | holdout net R -0.072 (not positive after costs) |
| S9-control | 5m | **DATA-INSUFFICIENT** | 123 | +0.142 | +0.176 (89) | +0.054 (34) | +0.22 / +0.14 / +0.15 / -0.00 | BTC +0.181 (66) / ETH +0.107 (56) / XAUT -0.410 (1) | perturbation:- assets:ok buckets:- control:- | 123 trades (need 200); 7224 setups |
| LEGACY-ST-tight-ATM | 5m | **DATA-INSUFFICIENT** | 123 | +0.142 | +0.176 (89) | +0.054 (34) | +0.22 / +0.14 / +0.15 / -0.00 | BTC +0.181 (66) / ETH +0.107 (56) / XAUT -0.410 (1) | perturbation:- assets:ok buckets:- control:- | 123 trades (need 200); 7223 setups |
| LEGACY-ST-tight-ITM1 | 5m | **DATA-INSUFFICIENT** | 180 | +0.082 | +0.088 (133) | +0.064 (47) | +0.09 / +0.03 / +0.13 / +0.03 | BTC +0.090 (112) / ETH +0.076 (67) / XAUT -0.410 (1) | perturbation:- assets:ok buckets:- control:- | 180 trades (need 200); 7223 setups |

## Gate details per cell (why each passed or failed)

- **S1-base / 15m: EXPERIMENTAL**
  - trades: pass - 319 trades (need 200)
  - holdout_n: pass - 104 holdout trades (need 30)
  - holdout_r: pass - holdout net R +0.004
  - perturbation: FAIL - 0/8 perturbations positive (failing: squeeze_pctx0.8, squeeze_pctx1.2, range_nx0.8, range_nx1.2, adx_minx0.8, adx_minx1.2, rrx0.8, rrx1.2)
  - assets: FAIL - positive on 1 asset(s) with >= 30 trades: BTC
  - buckets: FAIL - holdout net R positive in 1/3 option buckets
  - control: not run - not run (an earlier gate already decided the status)
  - setups 818; skipped: {'breakeven': 342, 'overlap': 12, 'no_strike_in_delta_band': 75, 'no_expiry_for_hold': 70}
- **S1-base / 5m: REJECTED**
  - trades: pass - 236 trades (need 200)
  - holdout_n: pass - 56 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.089
  - perturbation: not run - not run
  - assets: FAIL - positive on 1 asset(s) with >= 30 trades: ETH
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 2632; skipped: {'breakeven': 1880, 'overlap': 23, 'no_strike_in_delta_band': 287, 'no_expiry_for_hold': 206}
- **S1-lenient / 15m: REJECTED**
  - trades: pass - 532 trades (need 200)
  - holdout_n: pass - 175 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.007
  - perturbation: not run - not run
  - assets: FAIL - positive on 1 asset(s) with >= 30 trades: XAUT
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1393; skipped: {'breakeven': 581, 'overlap': 28, 'no_strike_in_delta_band': 136, 'no_expiry_for_hold': 116}
- **S1-lenient / 5m: REJECTED**
  - trades: pass - 360 trades (need 200)
  - holdout_n: pass - 99 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.067
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 4553; skipped: {'breakeven': 3334, 'overlap': 51, 'no_strike_in_delta_band': 469, 'no_expiry_for_hold': 339}
- **S1-strict / 15m: DATA-INSUFFICIENT**
  - trades: FAIL - 157 trades (need 200)
  - holdout_n: pass - 51 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.072
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 363; skipped: {'breakeven': 143, 'overlap': 3, 'no_strike_in_delta_band': 27, 'no_expiry_for_hold': 33}
- **S1-strict / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 109 trades (need 200)
  - holdout_n: FAIL - 19 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.272
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1127; skipped: {'breakeven': 803, 'no_strike_in_delta_band': 129, 'overlap': 5, 'no_expiry_for_hold': 81}
- **S10-ema50 / 15m: REJECTED**
  - trades: pass - 514 trades (need 200)
  - holdout_n: pass - 192 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.150
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1898; skipped: {'breakeven': 1041, 'overlap': 46, 'no_strike_in_delta_band': 174, 'no_expiry_for_hold': 123}
- **S10-ema50 / 5m: REJECTED**
  - trades: pass - 383 trades (need 200)
  - holdout_n: pass - 116 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.166
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 5472; skipped: {'breakeven': 4133, 'overlap': 121, 'no_strike_in_delta_band': 508, 'no_expiry_for_hold': 326, 'no_iv_exit': 1}
- **S10-vwap / 15m: REJECTED**
  - trades: pass - 436 trades (need 200)
  - holdout_n: pass - 165 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.114
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1595; skipped: {'breakeven': 896, 'overlap': 34, 'no_expiry_for_hold': 112, 'no_strike_in_delta_band': 117}
- **S10-vwap / 5m: REJECTED**
  - trades: pass - 1127 trades (need 200)
  - holdout_n: pass - 405 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.123
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 6415; skipped: {'breakeven': 3788, 'overlap': 565, 'no_strike_in_delta_band': 546, 'no_expiry_for_hold': 388, 'no_iv_exit': 1}
- **S10-vwap-z3 / 15m: REJECTED**
  - trades: pass - 394 trades (need 200)
  - holdout_n: pass - 144 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.110
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1196; skipped: {'breakeven': 598, 'overlap': 29, 'no_expiry_for_hold': 86, 'no_strike_in_delta_band': 89}
- **S10-vwap-z3 / 5m: REJECTED**
  - trades: pass - 1133 trades (need 200)
  - holdout_n: pass - 407 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.124
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 5612; skipped: {'breakeven': 3136, 'overlap': 539, 'no_strike_in_delta_band': 455, 'no_expiry_for_hold': 348, 'no_iv_exit': 1}
- **S2-n20 / 15m: REJECTED**
  - trades: pass - 1716 trades (need 200)
  - holdout_n: pass - 604 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.085
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 4873; skipped: {'breakeven': 1674, 'overlap': 642, 'no_strike_in_delta_band': 413, 'no_expiry_for_hold': 427, 'no_iv_exit': 1}
- **S2-n20 / 5m: REJECTED**
  - trades: pass - 1330 trades (need 200)
  - holdout_n: pass - 398 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.047
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 16015; skipped: {'breakeven': 10632, 'overlap': 1083, 'no_strike_in_delta_band': 1580, 'no_expiry_for_hold': 1390}
- **S2-n20-filtered / 15m: REJECTED**
  - trades: pass - 1313 trades (need 200)
  - holdout_n: pass - 481 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.082
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 3545; skipped: {'breakeven': 1291, 'overlap': 391, 'no_strike_in_delta_band': 291, 'no_expiry_for_hold': 258, 'no_iv_exit': 1}
- **S2-n20-filtered / 5m: REJECTED**
  - trades: pass - 1125 trades (need 200)
  - holdout_n: pass - 338 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.031
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 11592; skipped: {'breakeven': 7951, 'overlap': 643, 'no_strike_in_delta_band': 1086, 'no_expiry_for_hold': 787}
- **S2-n40 / 15m: REJECTED**
  - trades: pass - 926 trades (need 200)
  - holdout_n: pass - 315 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.032
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 3077; skipped: {'breakeven': 1269, 'overlap': 361, 'no_strike_in_delta_band': 231, 'no_expiry_for_hold': 289, 'no_iv_exit': 1}
- **S2-n40 / 5m: EXPERIMENTAL**
  - trades: pass - 661 trades (need 200)
  - holdout_n: pass - 197 holdout trades (need 30)
  - holdout_r: pass - holdout net R +0.005
  - perturbation: FAIL - 2/6 perturbations positive (failing: nx0.8, nx1.2, rrx1.2, stop_atrx1.2)
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: pass - holdout net R positive in 2/3 option buckets
  - control: not run - not run (an earlier gate already decided the status)
  - setups 10046; skipped: {'breakeven': 7169, 'overlap': 450, 'no_strike_in_delta_band': 826, 'no_expiry_for_hold': 940}
- **S3-base / 15m: REJECTED**
  - trades: pass - 1368 trades (need 200)
  - holdout_n: pass - 492 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.109
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 4008; skipped: {'breakeven': 1692, 'overlap': 309, 'no_strike_in_delta_band': 385, 'no_expiry_for_hold': 254}
- **S3-base / 5m: REJECTED**
  - trades: pass - 691 trades (need 200)
  - holdout_n: pass - 198 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.077
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 7366; skipped: {'breakeven': 5212, 'overlap': 300, 'no_strike_in_delta_band': 761, 'no_expiry_for_hold': 402}
- **S3-strict / 15m: REJECTED**
  - trades: pass - 819 trades (need 200)
  - holdout_n: pass - 290 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.064
  - perturbation: not run - not run
  - assets: FAIL - positive on 1 asset(s) with >= 30 trades: XAUT
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 2485; skipped: {'overlap': 86, 'breakeven': 1177, 'no_strike_in_delta_band': 224, 'no_expiry_for_hold': 179}
- **S3-strict / 5m: REJECTED**
  - trades: pass - 485 trades (need 200)
  - holdout_n: pass - 139 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.083
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 5107; skipped: {'breakeven': 3666, 'overlap': 128, 'no_strike_in_delta_band': 519, 'no_expiry_for_hold': 309}
- **S4-base / 15m: REJECTED**
  - trades: pass - 458 trades (need 200)
  - holdout_n: pass - 181 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.120
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 609; skipped: {'overlap': 13, 'breakeven': 36, 'no_expiry_for_hold': 56, 'no_strike_in_delta_band': 46}
- **S4-base / 5m: REJECTED**
  - trades: pass - 735 trades (need 200)
  - holdout_n: pass - 295 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.169
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1588; skipped: {'overlap': 136, 'breakeven': 450, 'no_strike_in_delta_band': 127, 'no_expiry_for_hold': 140}
- **S4-slow / 15m: REJECTED**
  - trades: pass - 406 trades (need 200)
  - holdout_n: pass - 157 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.066
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 507; skipped: {'breakeven': 20, 'overlap': 10, 'no_expiry_for_hold': 35, 'no_strike_in_delta_band': 36}
- **S4-slow / 5m: REJECTED**
  - trades: pass - 757 trades (need 200)
  - holdout_n: pass - 306 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.053
  - perturbation: not run - not run
  - assets: FAIL - positive on 1 asset(s) with >= 30 trades: XAUT
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1377; skipped: {'overlap': 140, 'breakeven': 271, 'no_strike_in_delta_band': 91, 'no_expiry_for_hold': 118}
- **S5-fade / 15m: DATA-INSUFFICIENT**
  - trades: FAIL - 53 trades (need 200)
  - holdout_n: FAIL - 18 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.184
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 85; skipped: {'breakeven': 29, 'no_strike_in_delta_band': 2, 'no_expiry_for_hold': 1}
- **S5-fade / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 46 trades (need 200)
  - holdout_n: FAIL - 15 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.249
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 209; skipped: {'breakeven': 156, 'overlap': 5, 'no_strike_in_delta_band': 2}
- **S5-follow / 15m: REJECTED**
  - trades: pass - 358 trades (need 200)
  - holdout_n: pass - 120 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.187
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 927; skipped: {'breakeven': 371, 'overlap': 36, 'no_strike_in_delta_band': 101, 'no_expiry_for_hold': 61}
- **S5-follow / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 146 trades (need 200)
  - holdout_n: pass - 45 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.092
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 2314; skipped: {'breakeven': 1743, 'no_strike_in_delta_band': 230, 'overlap': 52, 'no_expiry_for_hold': 143}
- **S6-all3 / 15m: REJECTED**
  - trades: pass - 301 trades (need 200)
  - holdout_n: pass - 98 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.020
  - perturbation: not run - not run
  - assets: FAIL - positive on 1 asset(s) with >= 30 trades: BTC
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 548; skipped: {'breakeven': 168, 'no_expiry_for_hold': 43, 'no_iv': 36}
- **S6-all3 / 5m: REJECTED**
  - trades: pass - 641 trades (need 200)
  - holdout_n: pass - 219 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.107
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1570; skipped: {'breakeven': 466, 'overlap': 278, 'no_expiry_for_hold': 97, 'no_iv_exit': 2, 'no_iv': 86}
- **S6-base / 15m: REJECTED**
  - trades: pass - 331 trades (need 200)
  - holdout_n: pass - 113 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.051
  - perturbation: not run - not run
  - assets: FAIL - positive on 1 asset(s) with >= 30 trades: BTC
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 618; skipped: {'breakeven': 200, 'no_expiry_for_hold': 48, 'no_iv': 39}
- **S6-base / 5m: REJECTED**
  - trades: pass - 686 trades (need 200)
  - holdout_n: pass - 226 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.132
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1770; skipped: {'breakeven': 522, 'overlap': 346, 'no_expiry_for_hold': 111, 'no_iv_exit': 2, 'no_iv': 103}
- **S6-loose / 15m: REJECTED**
  - trades: pass - 355 trades (need 200)
  - holdout_n: pass - 117 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.034
  - perturbation: not run - not run
  - assets: FAIL - positive on 1 asset(s) with >= 30 trades: BTC
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 668; skipped: {'breakeven': 226, 'no_expiry_for_hold': 46, 'no_iv': 41}
- **S6-loose / 5m: REJECTED**
  - trades: pass - 737 trades (need 200)
  - holdout_n: pass - 247 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.140
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1973; skipped: {'breakeven': 561, 'overlap': 445, 'no_expiry_for_hold': 118, 'no_iv_exit': 2, 'no_iv': 110}
- **S7-london / 15m: DATA-INSUFFICIENT**
  - trades: FAIL - 82 trades (need 200)
  - holdout_n: pass - 41 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.090
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1007; skipped: {'breakeven': 789, 'no_expiry_for_hold': 67, 'no_strike_in_delta_band': 69}
- **S7-london / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 43 trades (need 200)
  - holdout_n: FAIL - 21 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.006
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1089; skipped: {'breakeven': 898, 'no_expiry_for_hold': 73, 'no_strike_in_delta_band': 74, 'overlap': 1}
- **S7-us-open / 15m: REJECTED**
  - trades: pass - 366 trades (need 200)
  - holdout_n: pass - 143 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.128
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 926; skipped: {'breakeven': 427, 'overlap': 3, 'no_expiry_for_hold': 67, 'no_strike_in_delta_band': 63}
- **S7-us-open / 5m: REJECTED**
  - trades: pass - 304 trades (need 200)
  - holdout_n: pass - 123 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.045
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1017; skipped: {'breakeven': 579, 'overlap': 7, 'no_expiry_for_hold': 69, 'no_strike_in_delta_band': 58}
- **S7-utc-day / 15m: REJECTED**
  - trades: pass - 447 trades (need 200)
  - holdout_n: pass - 165 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.035
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1004; skipped: {'breakeven': 358, 'overlap': 2, 'no_strike_in_delta_band': 122, 'no_expiry_for_hold': 75}
- **S7-utc-day / 5m: REJECTED**
  - trades: pass - 359 trades (need 200)
  - holdout_n: pass - 140 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.022
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1076; skipped: {'breakeven': 498, 'no_strike_in_delta_band': 136, 'overlap': 2, 'no_expiry_for_hold': 81}
- **S8-20-50 / 15m: REJECTED**
  - trades: pass - 1362 trades (need 200)
  - holdout_n: pass - 475 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.114
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 2495; skipped: {'breakeven': 583, 'overlap': 183, 'no_strike_in_delta_band': 201, 'no_expiry_for_hold': 166}
- **S8-20-50 / 5m: REJECTED**
  - trades: pass - 1597 trades (need 200)
  - holdout_n: pass - 505 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.133
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 7501; skipped: {'breakeven': 4127, 'overlap': 666, 'no_strike_in_delta_band': 591, 'no_expiry_for_hold': 519, 'no_iv_exit': 1}
- **S8-20-50-adx25 / 15m: REJECTED**
  - trades: pass - 1105 trades (need 200)
  - holdout_n: pass - 388 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.088
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1902; skipped: {'breakeven': 385, 'overlap': 133, 'no_expiry_for_hold': 132, 'no_strike_in_delta_band': 146, 'no_iv_exit': 1}
- **S8-20-50-adx25 / 5m: REJECTED**
  - trades: pass - 1348 trades (need 200)
  - holdout_n: pass - 430 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.123
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 5608; skipped: {'breakeven': 2984, 'overlap': 447, 'no_strike_in_delta_band': 441, 'no_expiry_for_hold': 387, 'no_iv_exit': 1}
- **S8-50-200 / 15m: REJECTED**
  - trades: pass - 808 trades (need 200)
  - holdout_n: pass - 264 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.044
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1678; skipped: {'breakeven': 523, 'overlap': 112, 'no_expiry_for_hold': 122, 'no_strike_in_delta_band': 113}
- **S8-50-200 / 5m: REJECTED**
  - trades: pass - 591 trades (need 200)
  - holdout_n: pass - 164 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.107
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 5060; skipped: {'breakeven': 3599, 'overlap': 173, 'no_strike_in_delta_band': 338, 'no_expiry_for_hold': 359}
- **S9-adx / 15m: REJECTED**
  - trades: pass - 329 trades (need 200)
  - holdout_n: pass - 103 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.060
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1121; skipped: {'breakeven': 568, 'overlap': 2, 'no_strike_in_delta_band': 119, 'no_expiry_for_hold': 103}
- **S9-adx / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 103 trades (need 200)
  - holdout_n: FAIL - 25 holdout trades (need 30)
  - holdout_r: pass - holdout net R +0.019
  - perturbation: not run - not run
  - assets: pass - positive on 2 asset(s) with >= 30 trades: BTC, ETH
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 3776; skipped: {'breakeven': 2780, 'overlap': 7, 'no_strike_in_delta_band': 442, 'no_expiry_for_hold': 444}
- **S9-atr-band / 15m: REJECTED**
  - trades: pass - 278 trades (need 200)
  - holdout_n: pass - 97 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.074
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 1154; skipped: {'breakeven': 660, 'no_strike_in_delta_band': 126, 'overlap': 1, 'no_expiry_for_hold': 89}
- **S9-atr-band / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 29 trades (need 200)
  - holdout_n: FAIL - 6 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.077
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 3728; skipped: {'breakeven': 2881, 'overlap': 1, 'no_strike_in_delta_band': 440, 'no_expiry_for_hold': 377}
- **S9-control / 15m: REJECTED**
  - trades: pass - 549 trades (need 200)
  - holdout_n: pass - 176 holdout trades (need 30)
  - holdout_r: FAIL - holdout net R -0.072
  - perturbation: not run - not run
  - assets: FAIL - positive on 0 asset(s) with >= 30 trades
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 2086; skipped: {'breakeven': 1129, 'overlap': 3, 'no_strike_in_delta_band': 226, 'no_expiry_for_hold': 179}
- **S9-control / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 123 trades (need 200)
  - holdout_n: pass - 34 holdout trades (need 30)
  - holdout_r: pass - holdout net R +0.054
  - perturbation: not run - not run
  - assets: pass - positive on 2 asset(s) with >= 30 trades: BTC, ETH
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 7224; skipped: {'breakeven': 5477, 'overlap': 15, 'no_strike_in_delta_band': 849, 'no_expiry_for_hold': 760}
- **LEGACY-ST-tight-ATM / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 123 trades (need 200)
  - holdout_n: pass - 34 holdout trades (need 30)
  - holdout_r: pass - holdout net R +0.054
  - perturbation: not run - not run
  - assets: pass - positive on 2 asset(s) with >= 30 trades: BTC, ETH
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 7223; skipped: {'breakeven': 5475, 'overlap': 15, 'no_strike_in_delta_band': 849, 'no_expiry_for_hold': 761}
- **LEGACY-ST-tight-ITM1 / 5m: DATA-INSUFFICIENT**
  - trades: FAIL - 180 trades (need 200)
  - holdout_n: pass - 47 holdout trades (need 30)
  - holdout_r: pass - holdout net R +0.064
  - perturbation: not run - not run
  - assets: pass - positive on 2 asset(s) with >= 30 trades: BTC, ETH
  - buckets: not run - not run
  - control: not run - not run (an earlier gate already decided the status)
  - setups 7223; skipped: {'breakeven': 5414, 'overlap': 19, 'no_strike_in_delta_band': 849, 'no_expiry_for_hold': 761}

## Detailed metrics (pooled over assets, baseline bucket)

| Variant | TF | n | Win % | Expectancy R | Avg win R | Avg loss R | Profit factor | Max DD (R) | Max consec. losses | Sharpe/trade | Sortino/trade | Avg hold (h) | Exposure | Fees/risk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1-base | 15m | 319 | 29% | -0.026 | +0.910 | -0.405 | 1.17 | -16.40 | 16 | -0.04 | -0.07 | 1.7 | 3.4% | 4.3% |
| S1-base | 5m | 236 | 29% | -0.067 | +0.718 | -0.385 | 0.52 | -17.57 | 16 | -0.13 | -0.20 | 0.8 | 1.3% | 4.7% |
| S1-lenient | 15m | 532 | 29% | -0.024 | +0.921 | -0.405 | 0.98 | -17.72 | 19 | -0.04 | -0.07 | 1.7 | 5.6% | 4.4% |
| S1-lenient | 5m | 360 | 28% | -0.077 | +0.719 | -0.391 | 0.62 | -30.80 | 10 | -0.15 | -0.23 | 0.9 | 2.0% | 4.9% |
| S1-strict | 15m | 157 | 24% | -0.077 | +0.977 | -0.402 | 0.89 | -13.18 | 14 | -0.12 | -0.22 | 1.6 | 1.6% | 4.3% |
| S1-strict | 5m | 109 | 19% | -0.162 | +0.800 | -0.392 | 0.46 | -17.65 | 18 | -0.33 | -0.45 | 0.7 | 0.5% | 4.5% |
| S10-ema50 | 15m | 514 | 21% | -0.143 | +0.743 | -0.384 | 0.57 | -73.33 | 34 | -0.27 | -0.40 | 2.0 | 6.4% | 4.4% |
| S10-ema50 | 5m | 383 | 23% | -0.105 | +0.677 | -0.335 | 0.64 | -40.11 | 20 | -0.24 | -0.34 | 1.3 | 3.2% | 4.4% |
| S10-vwap | 15m | 436 | 25% | -0.121 | +0.589 | -0.363 | 0.56 | -52.65 | 19 | -0.26 | -0.37 | 2.3 | 6.2% | 4.2% |
| S10-vwap | 5m | 1127 | 20% | -0.109 | +0.618 | -0.296 | 0.52 | -123.64 | 22 | -0.26 | -0.39 | 1.7 | 11.5% | 4.0% |
| S10-vwap-z3 | 15m | 394 | 23% | -0.138 | +0.586 | -0.358 | 0.49 | -54.30 | 20 | -0.30 | -0.42 | 2.3 | 5.8% | 4.1% |
| S10-vwap-z3 | 5m | 1133 | 20% | -0.111 | +0.614 | -0.296 | 0.49 | -126.08 | 21 | -0.27 | -0.39 | 1.6 | 11.5% | 4.1% |
| S2-n20 | 15m | 1716 | 24% | -0.104 | +0.893 | -0.413 | 0.67 | -180.59 | 38 | -0.18 | -0.28 | 1.8 | 18.9% | 4.2% |
| S2-n20 | 5m | 1330 | 30% | -0.053 | +0.736 | -0.393 | 0.87 | -73.29 | 35 | -0.10 | -0.16 | 1.1 | 9.1% | 4.4% |
| S2-n20-filtered | 15m | 1313 | 25% | -0.085 | +0.924 | -0.413 | 0.74 | -113.42 | 35 | -0.14 | -0.23 | 1.7 | 13.9% | 4.2% |
| S2-n20-filtered | 5m | 1125 | 30% | -0.047 | +0.746 | -0.393 | 0.92 | -58.03 | 29 | -0.08 | -0.14 | 1.1 | 7.3% | 4.4% |
| S2-n40 | 15m | 926 | 26% | -0.058 | +0.900 | -0.401 | 0.80 | -57.07 | 20 | -0.10 | -0.17 | 2.2 | 12.7% | 3.5% |
| S2-n40 | 5m | 661 | 31% | -0.030 | +0.769 | -0.384 | 0.91 | -30.39 | 16 | -0.05 | -0.09 | 1.2 | 5.1% | 3.6% |
| S3-base | 15m | 1368 | 24% | -0.118 | +0.859 | -0.420 | 0.67 | -161.88 | 30 | -0.20 | -0.32 | 1.8 | 14.9% | 4.6% |
| S3-base | 5m | 691 | 29% | -0.096 | +0.671 | -0.404 | 0.75 | -69.51 | 34 | -0.19 | -0.28 | 1.1 | 5.0% | 4.9% |
| S3-strict | 15m | 819 | 26% | -0.079 | +0.870 | -0.416 | 0.81 | -70.02 | 27 | -0.13 | -0.22 | 1.6 | 8.2% | 4.6% |
| S3-strict | 5m | 485 | 30% | -0.073 | +0.695 | -0.401 | 0.73 | -38.83 | 14 | -0.14 | -0.22 | 1.0 | 3.1% | 4.9% |
| S4-base | 15m | 458 | 19% | -0.134 | +1.019 | -0.411 | 0.70 | -62.95 | 21 | -0.20 | -0.35 | 3.4 | 9.6% | 3.6% |
| S4-base | 5m | 735 | 22% | -0.120 | +0.899 | -0.415 | 0.69 | -92.77 | 21 | -0.20 | -0.32 | 2.6 | 11.9% | 3.8% |
| S4-slow | 15m | 406 | 21% | -0.083 | +1.127 | -0.409 | 0.75 | -40.60 | 22 | -0.11 | -0.22 | 4.2 | 10.6% | 3.3% |
| S4-slow | 5m | 757 | 26% | -0.057 | +0.939 | -0.400 | 0.80 | -66.04 | 25 | -0.09 | -0.16 | 3.3 | 15.6% | 3.6% |
| S5-fade | 15m | 53 | 26% | -0.047 | +1.005 | -0.424 | 0.62 | -4.65 | 10 | -0.07 | -0.13 | 2.4 | 1.1% | 3.4% |
| S5-fade | 5m | 46 | 33% | -0.016 | +0.790 | -0.406 | 0.99 | -4.15 | 7 | - | - | 1.3 | 0.5% | 3.7% |
| S5-follow | 15m | 358 | 19% | -0.168 | +0.877 | -0.417 | 0.51 | -60.56 | 22 | -0.31 | -0.44 | 2.1 | 4.7% | 4.1% |
| S5-follow | 5m | 146 | 26% | -0.124 | +0.644 | -0.394 | 0.65 | -18.23 | 13 | -0.26 | -0.36 | 1.3 | 1.3% | 4.2% |
| S6-all3 | 15m | 301 | 34% | -0.005 | +0.461 | -0.248 | 1.46 | -8.73 | 15 | -0.01 | -0.02 | 11.1 | 20.8% | 3.2% |
| S6-all3 | 5m | 641 | 27% | -0.073 | +0.559 | -0.312 | 0.88 | -49.84 | 21 | -0.14 | -0.25 | 9.9 | 39.1% | 3.3% |
| S6-base | 15m | 331 | 34% | -0.031 | +0.428 | -0.262 | 1.22 | -11.14 | 10 | -0.07 | -0.13 | 10.9 | 22.4% | 3.2% |
| S6-base | 5m | 686 | 27% | -0.086 | +0.532 | -0.315 | 0.81 | -60.56 | 26 | -0.18 | -0.30 | 9.8 | 41.3% | 3.2% |
| S6-loose | 15m | 355 | 31% | -0.023 | +0.557 | -0.280 | 1.36 | -16.54 | 11 | -0.04 | -0.09 | 10.7 | 23.6% | 3.2% |
| S6-loose | 5m | 737 | 28% | -0.083 | +0.499 | -0.310 | 0.78 | -62.09 | 19 | -0.18 | -0.29 | 9.8 | 44.6% | 3.2% |
| S7-london | 15m | 82 | 27% | -0.105 | +0.616 | -0.369 | 1.24 | -10.28 | 12 | -0.22 | -0.32 | 2.6 | 1.4% | 3.7% |
| S7-london | 5m | 43 | 30% | -0.038 | +0.710 | -0.362 | 2.12 | -3.95 | 12 | - | - | 2.2 | 0.7% | 3.7% |
| S7-us-open | 15m | 366 | 25% | -0.108 | +0.734 | -0.391 | 0.73 | -41.43 | 25 | -0.19 | -0.31 | 2.0 | 4.6% | 3.1% |
| S7-us-open | 5m | 304 | 25% | -0.110 | +0.708 | -0.383 | 0.47 | -36.63 | 20 | -0.21 | -0.33 | 1.9 | 3.5% | 3.1% |
| S7-utc-day | 15m | 447 | 27% | -0.051 | +0.930 | -0.415 | 1.10 | -34.23 | 19 | -0.08 | -0.14 | 1.6 | 4.3% | 4.7% |
| S7-utc-day | 5m | 359 | 29% | -0.028 | +0.910 | -0.416 | 1.03 | -24.71 | 17 | -0.04 | -0.08 | 1.5 | 3.2% | 4.8% |
| S8-20-50 | 15m | 1362 | 23% | -0.105 | +0.936 | -0.410 | 0.72 | -149.40 | 25 | -0.17 | -0.29 | 2.7 | 22.7% | 3.7% |
| S8-20-50 | 5m | 1597 | 24% | -0.130 | +0.769 | -0.414 | 0.64 | -209.51 | 24 | -0.25 | -0.36 | 1.9 | 18.2% | 3.9% |
| S8-20-50-adx25 | 15m | 1105 | 25% | -0.080 | +0.929 | -0.408 | 0.75 | -92.19 | 27 | -0.13 | -0.22 | 2.8 | 19.2% | 3.7% |
| S8-20-50-adx25 | 5m | 1348 | 24% | -0.124 | +0.804 | -0.413 | 0.67 | -167.69 | 36 | -0.23 | -0.34 | 1.8 | 15.2% | 3.9% |
| S8-50-200 | 15m | 808 | 26% | -0.072 | +0.868 | -0.402 | 0.73 | -58.96 | 19 | -0.12 | -0.20 | 3.6 | 17.9% | 3.0% |
| S8-50-200 | 5m | 591 | 25% | -0.105 | +0.780 | -0.403 | 0.71 | -63.94 | 20 | -0.20 | -0.30 | 2.2 | 8.1% | 3.0% |
| S9-adx | 15m | 329 | 26% | -0.127 | +0.709 | -0.423 | 0.58 | -48.95 | 14 | -0.24 | -0.34 | 1.6 | 3.3% | 4.0% |
| S9-adx | 5m | 103 | 48% | +0.135 | +0.758 | -0.430 | 1.55 | -2.47 | 5 | +0.22 | +0.43 | 1.1 | 0.8% | 3.7% |
| S9-atr-band | 15m | 278 | 27% | -0.127 | +0.700 | -0.427 | 0.62 | -41.30 | 15 | -0.24 | -0.34 | 1.5 | 2.7% | 4.7% |
| S9-atr-band | 5m | 29 | 55% | +0.209 | +0.734 | -0.437 | 2.51 | -1.86 | 4 | - | - | 0.9 | 0.2% | 3.9% |
| S9-control | 15m | 549 | 27% | -0.124 | +0.704 | -0.423 | 0.58 | -76.81 | 19 | -0.24 | -0.33 | 1.6 | 5.3% | 4.3% |
| S9-control | 5m | 123 | 49% | +0.142 | +0.751 | -0.437 | 1.71 | -2.89 | 6 | +0.23 | +0.45 | 1.1 | 0.9% | 3.8% |
| LEGACY-ST-tight-ATM | 5m | 123 | 49% | +0.142 | +0.751 | -0.437 | 1.71 | -2.89 | 6 | +0.23 | +0.45 | 1.1 | 0.9% | 3.8% |
| LEGACY-ST-tight-ITM1 | 5m | 180 | 47% | +0.082 | +0.640 | -0.418 | 1.23 | -3.65 | 7 | +0.15 | +0.27 | 1.1 | 1.4% | 3.3% |

Sharpe/Sortino are per-trade ratios and shown only with >= 50 trades. Exposure = share of all bars in the window spent holding a position, summed over assets.

## Performance by market regime (project regime label at entry; pooled)

Regimes with fewer than 20 trades are shown but carry little weight.

| Variant | TF | Regime | n | Expectancy R | Win % |
|---|---|---|---|---|---|
| S1-base | 15m | COMPRESSION | 2 | -0.395 | 0% |
| S1-base | 15m | EVENT_DRIVEN | 3 | +0.028 | 33% |
| S1-base | 15m | HIGH_VOL | 47 | +0.015 | 30% |
| S1-base | 15m | LOW_VOL | 15 | -0.135 | 27% |
| S1-base | 15m | RANGE | 2 | +0.264 | 50% |
| S1-base | 15m | TREND_DOWN | 86 | -0.042 | 27% |
| S1-base | 15m | TREND_UP | 66 | -0.149 | 23% |
| S1-base | 15m | VOL_EXPANSION | 98 | +0.068 | 35% |
| S1-base | 5m | COMPRESSION | 2 | +0.080 | 50% |
| S1-base | 5m | EVENT_DRIVEN | 7 | -0.244 | 14% |
| S1-base | 5m | HIGH_VOL | 13 | -0.006 | 38% |
| S1-base | 5m | LOW_VOL | 9 | -0.286 | 11% |
| S1-base | 5m | RANGE | 2 | +0.562 | 100% |
| S1-base | 5m | TREND_DOWN | 99 | -0.056 | 29% |
| S1-base | 5m | TREND_UP | 81 | -0.047 | 30% |
| S1-base | 5m | VOL_EXPANSION | 23 | -0.149 | 22% |
| S1-lenient | 15m | COMPRESSION | 6 | -0.415 | 0% |
| S1-lenient | 15m | EVENT_DRIVEN | 6 | -0.234 | 17% |
| S1-lenient | 15m | HIGH_VOL | 76 | +0.051 | 33% |
| S1-lenient | 15m | LOW_VOL | 18 | +0.015 | 33% |
| S1-lenient | 15m | RANGE | 8 | -0.075 | 25% |
| S1-lenient | 15m | TREND_DOWN | 159 | -0.066 | 25% |
| S1-lenient | 15m | TREND_UP | 110 | -0.070 | 27% |
| S1-lenient | 15m | VOL_EXPANSION | 149 | +0.040 | 33% |
| S1-lenient | 5m | COMPRESSION | 2 | +0.080 | 50% |
| S1-lenient | 5m | EVENT_DRIVEN | 12 | -0.115 | 25% |
| S1-lenient | 5m | HIGH_VOL | 31 | -0.023 | 32% |
| S1-lenient | 5m | LOW_VOL | 19 | -0.120 | 26% |
| S1-lenient | 5m | RANGE | 4 | +0.096 | 50% |
| S1-lenient | 5m | TREND_DOWN | 145 | -0.115 | 26% |
| S1-lenient | 5m | TREND_UP | 117 | -0.060 | 30% |
| S1-lenient | 5m | VOL_EXPANSION | 30 | -0.005 | 30% |
| S1-strict | 15m | COMPRESSION | 1 | -0.415 | 0% |
| S1-strict | 15m | EVENT_DRIVEN | 1 | -0.421 | 0% |
| S1-strict | 15m | HIGH_VOL | 21 | -0.091 | 19% |
| S1-strict | 15m | LOW_VOL | 7 | -0.046 | 29% |
| S1-strict | 15m | TREND_DOWN | 47 | -0.072 | 23% |
| S1-strict | 15m | TREND_UP | 33 | -0.213 | 15% |
| S1-strict | 15m | VOL_EXPANSION | 47 | +0.029 | 32% |
| S1-strict | 5m | COMPRESSION | 1 | -0.472 | 0% |
| S1-strict | 5m | EVENT_DRIVEN | 3 | -0.418 | 0% |
| S1-strict | 5m | HIGH_VOL | 1 | -0.373 | 0% |
| S1-strict | 5m | LOW_VOL | 3 | -0.063 | 33% |
| S1-strict | 5m | TREND_DOWN | 49 | -0.058 | 27% |
| S1-strict | 5m | TREND_UP | 43 | -0.245 | 14% |
| S1-strict | 5m | VOL_EXPANSION | 9 | -0.225 | 11% |
| S10-ema50 | 15m | COMPRESSION | 41 | -0.158 | 24% |
| S10-ema50 | 15m | EVENT_DRIVEN | 15 | -0.168 | 20% |
| S10-ema50 | 15m | HIGH_VOL | 119 | -0.128 | 22% |
| S10-ema50 | 15m | LIQUIDITY_STRESS | 5 | -0.046 | 60% |
| S10-ema50 | 15m | LOW_VOL | 65 | -0.179 | 20% |
| S10-ema50 | 15m | RANGE | 83 | -0.191 | 18% |
| S10-ema50 | 15m | TREND_DOWN | 70 | -0.149 | 20% |
| S10-ema50 | 15m | TREND_UP | 61 | -0.014 | 26% |
| S10-ema50 | 15m | VOL_EXPANSION | 55 | -0.183 | 18% |
| S10-ema50 | 5m | COMPRESSION | 5 | -0.192 | 20% |
| S10-ema50 | 5m | EVENT_DRIVEN | 6 | -0.118 | 17% |
| S10-ema50 | 5m | HIGH_VOL | 36 | -0.095 | 25% |
| S10-ema50 | 5m | LIQUIDITY_STRESS | 7 | -0.272 | 14% |
| S10-ema50 | 5m | LOW_VOL | 13 | -0.146 | 23% |
| S10-ema50 | 5m | RANGE | 34 | -0.096 | 24% |
| S10-ema50 | 5m | TREND_DOWN | 154 | -0.088 | 23% |
| S10-ema50 | 5m | TREND_UP | 128 | -0.112 | 23% |
| S10-vwap | 15m | COMPRESSION | 57 | -0.182 | 21% |
| S10-vwap | 15m | EVENT_DRIVEN | 12 | -0.089 | 25% |
| S10-vwap | 15m | HIGH_VOL | 72 | -0.058 | 31% |
| S10-vwap | 15m | LIQUIDITY_STRESS | 8 | -0.020 | 50% |
| S10-vwap | 15m | LOW_VOL | 86 | -0.191 | 20% |
| S10-vwap | 15m | RANGE | 68 | -0.141 | 24% |
| S10-vwap | 15m | TREND_DOWN | 56 | -0.170 | 21% |
| S10-vwap | 15m | TREND_UP | 48 | +0.080 | 40% |
| S10-vwap | 15m | VOL_EXPANSION | 29 | -0.174 | 21% |
| S10-vwap | 5m | COMPRESSION | 98 | -0.129 | 18% |
| S10-vwap | 5m | EVENT_DRIVEN | 29 | -0.171 | 10% |
| S10-vwap | 5m | HIGH_VOL | 113 | -0.120 | 20% |
| S10-vwap | 5m | LIQUIDITY_STRESS | 16 | -0.044 | 31% |
| S10-vwap | 5m | LOW_VOL | 173 | -0.113 | 18% |
| S10-vwap | 5m | RANGE | 219 | -0.111 | 21% |
| S10-vwap | 5m | TREND_DOWN | 247 | -0.076 | 23% |
| S10-vwap | 5m | TREND_UP | 230 | -0.120 | 20% |
| S10-vwap | 5m | VOL_CONTRACTION | 1 | -0.203 | 0% |
| S10-vwap | 5m | VOL_EXPANSION | 1 | -0.391 | 0% |
| S10-vwap-z3 | 15m | COMPRESSION | 57 | -0.187 | 21% |
| S10-vwap-z3 | 15m | EVENT_DRIVEN | 10 | -0.041 | 30% |
| S10-vwap-z3 | 15m | HIGH_VOL | 63 | -0.060 | 27% |
| S10-vwap-z3 | 15m | LIQUIDITY_STRESS | 7 | +0.041 | 57% |
| S10-vwap-z3 | 15m | LOW_VOL | 84 | -0.185 | 20% |
| S10-vwap-z3 | 15m | RANGE | 64 | -0.172 | 20% |
| S10-vwap-z3 | 15m | TREND_DOWN | 43 | -0.234 | 16% |
| S10-vwap-z3 | 15m | TREND_UP | 39 | +0.024 | 33% |
| S10-vwap-z3 | 15m | VOL_EXPANSION | 27 | -0.150 | 22% |
| S10-vwap-z3 | 5m | COMPRESSION | 98 | -0.129 | 18% |
| S10-vwap-z3 | 5m | EVENT_DRIVEN | 29 | -0.171 | 10% |
| S10-vwap-z3 | 5m | HIGH_VOL | 112 | -0.127 | 20% |
| S10-vwap-z3 | 5m | LIQUIDITY_STRESS | 16 | -0.044 | 31% |
| S10-vwap-z3 | 5m | LOW_VOL | 173 | -0.114 | 18% |
| S10-vwap-z3 | 5m | RANGE | 223 | -0.112 | 22% |
| S10-vwap-z3 | 5m | TREND_DOWN | 251 | -0.078 | 23% |
| S10-vwap-z3 | 5m | TREND_UP | 229 | -0.122 | 20% |
| S10-vwap-z3 | 5m | VOL_CONTRACTION | 1 | -0.203 | 0% |
| S10-vwap-z3 | 5m | VOL_EXPANSION | 1 | -0.391 | 0% |
| S2-n20 | 15m | COMPRESSION | 26 | -0.381 | 4% |
| S2-n20 | 15m | EVENT_DRIVEN | 24 | -0.108 | 21% |
| S2-n20 | 15m | HIGH_VOL | 228 | -0.008 | 30% |
| S2-n20 | 15m | LIQUIDITY_STRESS | 1 | -0.413 | 0% |
| S2-n20 | 15m | LOW_VOL | 78 | -0.235 | 14% |
| S2-n20 | 15m | RANGE | 35 | -0.089 | 29% |
| S2-n20 | 15m | TREND_DOWN | 490 | -0.105 | 23% |
| S2-n20 | 15m | TREND_UP | 458 | -0.147 | 22% |
| S2-n20 | 15m | VOL_EXPANSION | 376 | -0.064 | 26% |
| S2-n20 | 5m | COMPRESSION | 10 | -0.204 | 20% |
| S2-n20 | 5m | EVENT_DRIVEN | 28 | -0.163 | 21% |
| S2-n20 | 5m | HIGH_VOL | 169 | -0.060 | 31% |
| S2-n20 | 5m | LOW_VOL | 42 | -0.202 | 19% |
| S2-n20 | 5m | RANGE | 18 | -0.092 | 28% |
| S2-n20 | 5m | TREND_DOWN | 403 | -0.052 | 30% |
| S2-n20 | 5m | TREND_UP | 337 | -0.117 | 27% |
| S2-n20 | 5m | VOL_EXPANSION | 323 | +0.053 | 36% |
| S2-n20-filtered | 15m | COMPRESSION | 13 | -0.335 | 8% |
| S2-n20-filtered | 15m | EVENT_DRIVEN | 14 | +0.019 | 29% |
| S2-n20-filtered | 15m | HIGH_VOL | 190 | +0.012 | 30% |
| S2-n20-filtered | 15m | LIQUIDITY_STRESS | 1 | -0.413 | 0% |
| S2-n20-filtered | 15m | LOW_VOL | 35 | -0.221 | 17% |
| S2-n20-filtered | 15m | RANGE | 29 | -0.132 | 24% |
| S2-n20-filtered | 15m | TREND_DOWN | 373 | -0.088 | 24% |
| S2-n20-filtered | 15m | TREND_UP | 344 | -0.147 | 22% |
| S2-n20-filtered | 15m | VOL_EXPANSION | 314 | -0.048 | 27% |
| S2-n20-filtered | 5m | COMPRESSION | 7 | -0.112 | 29% |
| S2-n20-filtered | 5m | EVENT_DRIVEN | 21 | -0.245 | 14% |
| S2-n20-filtered | 5m | HIGH_VOL | 110 | -0.027 | 35% |
| S2-n20-filtered | 5m | LOW_VOL | 26 | -0.155 | 23% |
| S2-n20-filtered | 5m | RANGE | 8 | +0.152 | 50% |
| S2-n20-filtered | 5m | TREND_DOWN | 371 | -0.066 | 29% |
| S2-n20-filtered | 5m | TREND_UP | 311 | -0.114 | 27% |
| S2-n20-filtered | 5m | VOL_EXPANSION | 271 | +0.069 | 37% |
| S2-n40 | 15m | EVENT_DRIVEN | 9 | -0.180 | 11% |
| S2-n40 | 15m | HIGH_VOL | 126 | +0.001 | 30% |
| S2-n40 | 15m | LIQUIDITY_STRESS | 1 | -0.413 | 0% |
| S2-n40 | 15m | LOW_VOL | 5 | -0.247 | 20% |
| S2-n40 | 15m | RANGE | 9 | +0.079 | 44% |
| S2-n40 | 15m | TREND_DOWN | 230 | -0.103 | 22% |
| S2-n40 | 15m | TREND_UP | 235 | -0.099 | 25% |
| S2-n40 | 15m | VOL_EXPANSION | 311 | -0.014 | 29% |
| S2-n40 | 5m | COMPRESSION | 1 | -0.386 | 0% |
| S2-n40 | 5m | EVENT_DRIVEN | 4 | -0.403 | 0% |
| S2-n40 | 5m | HIGH_VOL | 61 | -0.144 | 25% |
| S2-n40 | 5m | LOW_VOL | 4 | -0.123 | 25% |
| S2-n40 | 5m | RANGE | 5 | +0.290 | 60% |
| S2-n40 | 5m | TREND_DOWN | 179 | -0.103 | 25% |
| S2-n40 | 5m | TREND_UP | 143 | -0.076 | 28% |
| S2-n40 | 5m | VOL_EXPANSION | 264 | +0.073 | 38% |
| S3-base | 15m | COMPRESSION | 58 | -0.185 | 19% |
| S3-base | 15m | EVENT_DRIVEN | 31 | -0.000 | 32% |
| S3-base | 15m | HIGH_VOL | 218 | -0.085 | 27% |
| S3-base | 15m | LIQUIDITY_STRESS | 1 | +0.853 | 100% |
| S3-base | 15m | LOW_VOL | 134 | -0.146 | 21% |
| S3-base | 15m | RANGE | 105 | -0.113 | 24% |
| S3-base | 15m | TREND_DOWN | 356 | -0.123 | 22% |
| S3-base | 15m | TREND_UP | 291 | -0.153 | 22% |
| S3-base | 15m | VOL_EXPANSION | 174 | -0.077 | 27% |
| S3-base | 5m | COMPRESSION | 13 | +0.060 | 46% |
| S3-base | 5m | EVENT_DRIVEN | 23 | -0.080 | 30% |
| S3-base | 5m | HIGH_VOL | 143 | -0.039 | 34% |
| S3-base | 5m | LOW_VOL | 65 | -0.155 | 25% |
| S3-base | 5m | RANGE | 68 | -0.196 | 21% |
| S3-base | 5m | TREND_DOWN | 195 | -0.115 | 26% |
| S3-base | 5m | TREND_UP | 125 | -0.102 | 30% |
| S3-base | 5m | VOL_EXPANSION | 59 | -0.018 | 32% |
| S3-strict | 15m | COMPRESSION | 9 | -0.129 | 22% |
| S3-strict | 15m | EVENT_DRIVEN | 14 | -0.031 | 21% |
| S3-strict | 15m | HIGH_VOL | 128 | -0.028 | 30% |
| S3-strict | 15m | LIQUIDITY_STRESS | 1 | -0.413 | 0% |
| S3-strict | 15m | LOW_VOL | 41 | -0.228 | 17% |
| S3-strict | 15m | RANGE | 25 | +0.056 | 40% |
| S3-strict | 15m | TREND_DOWN | 262 | -0.086 | 24% |
| S3-strict | 15m | TREND_UP | 204 | -0.135 | 24% |
| S3-strict | 15m | VOL_EXPANSION | 135 | -0.005 | 32% |
| S3-strict | 5m | COMPRESSION | 8 | +0.089 | 50% |
| S3-strict | 5m | EVENT_DRIVEN | 18 | -0.072 | 28% |
| S3-strict | 5m | HIGH_VOL | 62 | -0.058 | 31% |
| S3-strict | 5m | LOW_VOL | 36 | -0.065 | 33% |
| S3-strict | 5m | RANGE | 20 | -0.157 | 25% |
| S3-strict | 5m | TREND_DOWN | 180 | -0.086 | 28% |
| S3-strict | 5m | TREND_UP | 122 | -0.089 | 30% |
| S3-strict | 5m | VOL_EXPANSION | 39 | +0.012 | 36% |
| S4-base | 15m | COMPRESSION | 2 | -0.406 | 0% |
| S4-base | 15m | EVENT_DRIVEN | 3 | +0.175 | 33% |
| S4-base | 15m | HIGH_VOL | 90 | -0.212 | 17% |
| S4-base | 15m | LIQUIDITY_STRESS | 2 | -0.417 | 0% |
| S4-base | 15m | LOW_VOL | 4 | -0.486 | 0% |
| S4-base | 15m | RANGE | 7 | -0.091 | 29% |
| S4-base | 15m | TREND_DOWN | 98 | -0.120 | 20% |
| S4-base | 15m | TREND_UP | 99 | -0.120 | 19% |
| S4-base | 15m | VOL_EXPANSION | 153 | -0.097 | 21% |
| S4-base | 5m | COMPRESSION | 43 | -0.197 | 21% |
| S4-base | 5m | EVENT_DRIVEN | 27 | -0.170 | 15% |
| S4-base | 5m | HIGH_VOL | 264 | -0.065 | 26% |
| S4-base | 5m | LIQUIDITY_STRESS | 1 | +0.050 | 100% |
| S4-base | 5m | LOW_VOL | 120 | -0.209 | 16% |
| S4-base | 5m | RANGE | 66 | -0.196 | 20% |
| S4-base | 5m | TREND_DOWN | 27 | -0.091 | 26% |
| S4-base | 5m | TREND_UP | 40 | -0.128 | 20% |
| S4-base | 5m | VOL_CONTRACTION | 1 | -0.371 | 0% |
| S4-base | 5m | VOL_EXPANSION | 146 | -0.083 | 24% |
| S4-slow | 15m | EVENT_DRIVEN | 2 | +0.860 | 50% |
| S4-slow | 15m | HIGH_VOL | 80 | -0.137 | 19% |
| S4-slow | 15m | LOW_VOL | 2 | +0.372 | 50% |
| S4-slow | 15m | RANGE | 7 | +0.429 | 57% |
| S4-slow | 15m | TREND_DOWN | 83 | -0.153 | 18% |
| S4-slow | 15m | TREND_UP | 91 | -0.112 | 21% |
| S4-slow | 15m | VOL_EXPANSION | 141 | -0.039 | 22% |
| S4-slow | 5m | COMPRESSION | 13 | -0.058 | 23% |
| S4-slow | 5m | EVENT_DRIVEN | 18 | -0.108 | 22% |
| S4-slow | 5m | HIGH_VOL | 204 | -0.024 | 28% |
| S4-slow | 5m | LOW_VOL | 60 | -0.225 | 15% |
| S4-slow | 5m | RANGE | 39 | -0.036 | 33% |
| S4-slow | 5m | TREND_DOWN | 120 | -0.066 | 28% |
| S4-slow | 5m | TREND_UP | 109 | -0.016 | 28% |
| S4-slow | 5m | VOL_EXPANSION | 194 | -0.058 | 22% |
| S5-fade | 15m | COMPRESSION | 1 | -0.443 | 0% |
| S5-fade | 15m | EVENT_DRIVEN | 3 | -0.429 | 0% |
| S5-fade | 15m | HIGH_VOL | 12 | -0.030 | 33% |
| S5-fade | 15m | LOW_VOL | 10 | -0.086 | 30% |
| S5-fade | 15m | RANGE | 5 | -0.122 | 20% |
| S5-fade | 15m | TREND_DOWN | 3 | +0.505 | 33% |
| S5-fade | 15m | TREND_UP | 6 | +0.426 | 50% |
| S5-fade | 15m | VOL_EXPANSION | 13 | -0.229 | 15% |
| S5-fade | 5m | EVENT_DRIVEN | 3 | +0.027 | 33% |
| S5-fade | 5m | HIGH_VOL | 8 | +0.059 | 38% |
| S5-fade | 5m | LOW_VOL | 1 | -0.515 | 0% |
| S5-fade | 5m | RANGE | 7 | -0.036 | 29% |
| S5-fade | 5m | TREND_DOWN | 4 | +0.146 | 50% |
| S5-fade | 5m | TREND_UP | 7 | +0.222 | 57% |
| S5-fade | 5m | VOL_EXPANSION | 16 | -0.166 | 19% |
| S5-follow | 15m | COMPRESSION | 30 | -0.261 | 13% |
| S5-follow | 15m | EVENT_DRIVEN | 12 | -0.218 | 17% |
| S5-follow | 15m | HIGH_VOL | 37 | -0.014 | 32% |
| S5-follow | 15m | LIQUIDITY_STRESS | 2 | +0.189 | 50% |
| S5-follow | 15m | LOW_VOL | 46 | -0.185 | 17% |
| S5-follow | 15m | RANGE | 62 | -0.219 | 15% |
| S5-follow | 15m | TREND_DOWN | 68 | -0.024 | 29% |
| S5-follow | 15m | TREND_UP | 66 | -0.283 | 11% |
| S5-follow | 15m | VOL_EXPANSION | 35 | -0.200 | 17% |
| S5-follow | 5m | EVENT_DRIVEN | 2 | -0.334 | 0% |
| S5-follow | 5m | HIGH_VOL | 37 | -0.108 | 27% |
| S5-follow | 5m | LOW_VOL | 8 | -0.166 | 25% |
| S5-follow | 5m | RANGE | 27 | -0.132 | 26% |
| S5-follow | 5m | TREND_DOWN | 28 | -0.048 | 32% |
| S5-follow | 5m | TREND_UP | 8 | -0.198 | 25% |
| S5-follow | 5m | VOL_EXPANSION | 36 | -0.155 | 22% |
| S6-all3 | 15m | COMPRESSION | 70 | +0.053 | 36% |
| S6-all3 | 15m | EVENT_DRIVEN | 8 | -0.076 | 25% |
| S6-all3 | 15m | HIGH_VOL | 3 | +0.131 | 33% |
| S6-all3 | 15m | LOW_VOL | 122 | -0.022 | 30% |
| S6-all3 | 15m | RANGE | 50 | -0.071 | 38% |
| S6-all3 | 15m | TREND_DOWN | 28 | -0.003 | 43% |
| S6-all3 | 15m | TREND_UP | 20 | +0.067 | 40% |
| S6-all3 | 5m | COMPRESSION | 93 | -0.173 | 20% |
| S6-all3 | 5m | EVENT_DRIVEN | 18 | -0.191 | 22% |
| S6-all3 | 5m | LOW_VOL | 395 | -0.043 | 28% |
| S6-all3 | 5m | RANGE | 101 | -0.087 | 29% |
| S6-all3 | 5m | TREND_DOWN | 16 | -0.098 | 25% |
| S6-all3 | 5m | TREND_UP | 18 | +0.009 | 44% |
| S6-base | 15m | COMPRESSION | 83 | +0.002 | 31% |
| S6-base | 15m | EVENT_DRIVEN | 9 | -0.018 | 33% |
| S6-base | 15m | HIGH_VOL | 5 | -0.131 | 20% |
| S6-base | 15m | LOW_VOL | 144 | -0.025 | 35% |
| S6-base | 15m | RANGE | 56 | -0.085 | 34% |
| S6-base | 15m | TREND_DOWN | 13 | +0.124 | 54% |
| S6-base | 15m | TREND_UP | 20 | -0.116 | 25% |
| S6-base | 15m | VOL_EXPANSION | 1 | -0.397 | 0% |
| S6-base | 5m | COMPRESSION | 80 | -0.124 | 22% |
| S6-base | 5m | EVENT_DRIVEN | 19 | -0.144 | 21% |
| S6-base | 5m | HIGH_VOL | 1 | -0.373 | 0% |
| S6-base | 5m | LOW_VOL | 392 | -0.077 | 28% |
| S6-base | 5m | RANGE | 135 | -0.079 | 25% |
| S6-base | 5m | TREND_DOWN | 25 | -0.065 | 36% |
| S6-base | 5m | TREND_UP | 34 | -0.091 | 29% |
| S6-loose | 15m | COMPRESSION | 95 | -0.035 | 28% |
| S6-loose | 15m | EVENT_DRIVEN | 12 | +0.134 | 58% |
| S6-loose | 15m | HIGH_VOL | 11 | -0.054 | 27% |
| S6-loose | 15m | LIQUIDITY_STRESS | 1 | -0.049 | 0% |
| S6-loose | 15m | LOW_VOL | 138 | -0.002 | 33% |
| S6-loose | 15m | RANGE | 52 | -0.135 | 21% |
| S6-loose | 15m | TREND_DOWN | 21 | -0.023 | 38% |
| S6-loose | 15m | TREND_UP | 25 | +0.082 | 28% |
| S6-loose | 5m | COMPRESSION | 45 | -0.101 | 20% |
| S6-loose | 5m | EVENT_DRIVEN | 19 | -0.125 | 32% |
| S6-loose | 5m | HIGH_VOL | 5 | -0.235 | 40% |
| S6-loose | 5m | LIQUIDITY_STRESS | 1 | -0.366 | 0% |
| S6-loose | 5m | LOW_VOL | 321 | -0.091 | 26% |
| S6-loose | 5m | RANGE | 245 | -0.058 | 32% |
| S6-loose | 5m | TREND_DOWN | 45 | +0.011 | 38% |
| S6-loose | 5m | TREND_UP | 55 | -0.168 | 20% |
| S6-loose | 5m | VOL_CONTRACTION | 1 | -0.416 | 0% |
| S7-london | 15m | HIGH_VOL | 12 | -0.307 | 8% |
| S7-london | 15m | RANGE | 1 | -0.379 | 0% |
| S7-london | 15m | TREND_DOWN | 31 | +0.037 | 42% |
| S7-london | 15m | TREND_UP | 27 | -0.119 | 26% |
| S7-london | 15m | VOL_EXPANSION | 11 | -0.227 | 9% |
| S7-london | 5m | EVENT_DRIVEN | 1 | +0.054 | 100% |
| S7-london | 5m | HIGH_VOL | 3 | +0.244 | 67% |
| S7-london | 5m | TREND_DOWN | 13 | -0.010 | 31% |
| S7-london | 5m | TREND_UP | 16 | -0.096 | 25% |
| S7-london | 5m | VOL_EXPANSION | 10 | -0.074 | 20% |
| S7-us-open | 15m | COMPRESSION | 1 | -0.447 | 0% |
| S7-us-open | 15m | EVENT_DRIVEN | 3 | -0.071 | 33% |
| S7-us-open | 15m | HIGH_VOL | 95 | -0.038 | 35% |
| S7-us-open | 15m | LOW_VOL | 1 | +0.616 | 100% |
| S7-us-open | 15m | RANGE | 8 | -0.406 | 0% |
| S7-us-open | 15m | TREND_DOWN | 47 | -0.236 | 15% |
| S7-us-open | 15m | TREND_UP | 53 | -0.081 | 26% |
| S7-us-open | 15m | VOL_EXPANSION | 158 | -0.110 | 23% |
| S7-us-open | 5m | COMPRESSION | 1 | +0.809 | 100% |
| S7-us-open | 5m | EVENT_DRIVEN | 2 | -0.279 | 0% |
| S7-us-open | 5m | HIGH_VOL | 65 | -0.095 | 29% |
| S7-us-open | 5m | LOW_VOL | 1 | -0.426 | 0% |
| S7-us-open | 5m | RANGE | 2 | -0.417 | 0% |
| S7-us-open | 5m | TREND_DOWN | 53 | -0.169 | 19% |
| S7-us-open | 5m | TREND_UP | 45 | -0.143 | 24% |
| S7-us-open | 5m | VOL_EXPANSION | 135 | -0.082 | 26% |
| S7-utc-day | 15m | COMPRESSION | 22 | -0.276 | 14% |
| S7-utc-day | 15m | EVENT_DRIVEN | 9 | -0.196 | 22% |
| S7-utc-day | 15m | HIGH_VOL | 85 | +0.043 | 35% |
| S7-utc-day | 15m | LIQUIDITY_STRESS | 1 | -0.445 | 0% |
| S7-utc-day | 15m | LOW_VOL | 45 | -0.141 | 20% |
| S7-utc-day | 15m | RANGE | 32 | -0.085 | 28% |
| S7-utc-day | 15m | TREND_DOWN | 106 | -0.004 | 30% |
| S7-utc-day | 15m | TREND_UP | 106 | -0.161 | 20% |
| S7-utc-day | 15m | VOL_EXPANSION | 41 | +0.210 | 37% |
| S7-utc-day | 5m | COMPRESSION | 11 | -0.141 | 27% |
| S7-utc-day | 5m | EVENT_DRIVEN | 10 | -0.022 | 20% |
| S7-utc-day | 5m | HIGH_VOL | 76 | +0.013 | 33% |
| S7-utc-day | 5m | LOW_VOL | 36 | -0.215 | 17% |
| S7-utc-day | 5m | RANGE | 20 | -0.139 | 20% |
| S7-utc-day | 5m | TREND_DOWN | 90 | +0.054 | 37% |
| S7-utc-day | 5m | TREND_UP | 80 | -0.135 | 24% |
| S7-utc-day | 5m | VOL_EXPANSION | 36 | +0.197 | 36% |
| S8-20-50 | 15m | COMPRESSION | 65 | -0.215 | 14% |
| S8-20-50 | 15m | EVENT_DRIVEN | 31 | -0.137 | 23% |
| S8-20-50 | 15m | HIGH_VOL | 265 | -0.013 | 29% |
| S8-20-50 | 15m | LIQUIDITY_STRESS | 11 | -0.408 | 0% |
| S8-20-50 | 15m | LOW_VOL | 212 | -0.111 | 23% |
| S8-20-50 | 15m | RANGE | 155 | -0.105 | 21% |
| S8-20-50 | 15m | TREND_DOWN | 202 | -0.117 | 20% |
| S8-20-50 | 15m | TREND_UP | 185 | -0.176 | 17% |
| S8-20-50 | 15m | VOL_EXPANSION | 236 | -0.090 | 25% |
| S8-20-50 | 5m | COMPRESSION | 42 | -0.232 | 17% |
| S8-20-50 | 5m | EVENT_DRIVEN | 39 | -0.166 | 18% |
| S8-20-50 | 5m | HIGH_VOL | 329 | -0.108 | 26% |
| S8-20-50 | 5m | LIQUIDITY_STRESS | 10 | -0.080 | 30% |
| S8-20-50 | 5m | LOW_VOL | 96 | -0.177 | 24% |
| S8-20-50 | 5m | RANGE | 245 | -0.190 | 20% |
| S8-20-50 | 5m | TREND_DOWN | 252 | -0.091 | 27% |
| S8-20-50 | 5m | TREND_UP | 223 | -0.162 | 22% |
| S8-20-50 | 5m | VOL_EXPANSION | 361 | -0.092 | 26% |
| S8-20-50-adx25 | 15m | COMPRESSION | 44 | -0.200 | 16% |
| S8-20-50-adx25 | 15m | EVENT_DRIVEN | 24 | +0.032 | 33% |
| S8-20-50-adx25 | 15m | HIGH_VOL | 236 | -0.016 | 30% |
| S8-20-50-adx25 | 15m | LIQUIDITY_STRESS | 7 | -0.133 | 14% |
| S8-20-50-adx25 | 15m | LOW_VOL | 138 | -0.102 | 22% |
| S8-20-50-adx25 | 15m | RANGE | 126 | -0.096 | 26% |
| S8-20-50-adx25 | 15m | TREND_DOWN | 137 | -0.110 | 23% |
| S8-20-50-adx25 | 15m | TREND_UP | 130 | -0.092 | 22% |
| S8-20-50-adx25 | 15m | VOL_EXPANSION | 263 | -0.085 | 24% |
| S8-20-50-adx25 | 5m | COMPRESSION | 25 | -0.046 | 32% |
| S8-20-50-adx25 | 5m | EVENT_DRIVEN | 24 | -0.002 | 29% |
| S8-20-50-adx25 | 5m | HIGH_VOL | 255 | -0.119 | 24% |
| S8-20-50-adx25 | 5m | LIQUIDITY_STRESS | 10 | +0.039 | 40% |
| S8-20-50-adx25 | 5m | LOW_VOL | 61 | -0.165 | 23% |
| S8-20-50-adx25 | 5m | RANGE | 147 | -0.189 | 18% |
| S8-20-50-adx25 | 5m | TREND_DOWN | 261 | -0.116 | 25% |
| S8-20-50-adx25 | 5m | TREND_UP | 231 | -0.125 | 25% |
| S8-20-50-adx25 | 5m | VOL_EXPANSION | 334 | -0.117 | 23% |
| S8-50-200 | 15m | COMPRESSION | 26 | -0.149 | 23% |
| S8-50-200 | 15m | EVENT_DRIVEN | 17 | +0.146 | 41% |
| S8-50-200 | 15m | HIGH_VOL | 169 | -0.004 | 31% |
| S8-50-200 | 15m | LIQUIDITY_STRESS | 7 | -0.175 | 14% |
| S8-50-200 | 15m | LOW_VOL | 69 | -0.131 | 22% |
| S8-50-200 | 15m | RANGE | 89 | -0.082 | 27% |
| S8-50-200 | 15m | TREND_DOWN | 105 | -0.118 | 22% |
| S8-50-200 | 15m | TREND_UP | 152 | -0.084 | 25% |
| S8-50-200 | 15m | VOL_EXPANSION | 174 | -0.076 | 25% |
| S8-50-200 | 5m | COMPRESSION | 2 | +0.090 | 50% |
| S8-50-200 | 5m | EVENT_DRIVEN | 12 | -0.216 | 17% |
| S8-50-200 | 5m | HIGH_VOL | 157 | -0.104 | 26% |
| S8-50-200 | 5m | LIQUIDITY_STRESS | 1 | -0.387 | 0% |
| S8-50-200 | 5m | LOW_VOL | 15 | -0.195 | 20% |
| S8-50-200 | 5m | RANGE | 66 | -0.155 | 21% |
| S8-50-200 | 5m | TREND_DOWN | 49 | -0.046 | 31% |
| S8-50-200 | 5m | TREND_UP | 51 | -0.255 | 14% |
| S8-50-200 | 5m | VOL_EXPANSION | 238 | -0.060 | 28% |
| S9-adx | 15m | COMPRESSION | 4 | +0.199 | 50% |
| S9-adx | 15m | EVENT_DRIVEN | 5 | +0.224 | 60% |
| S9-adx | 15m | HIGH_VOL | 39 | -0.101 | 28% |
| S9-adx | 15m | LOW_VOL | 9 | +0.010 | 33% |
| S9-adx | 15m | RANGE | 3 | +0.486 | 67% |
| S9-adx | 15m | TREND_DOWN | 91 | -0.102 | 27% |
| S9-adx | 15m | TREND_UP | 101 | -0.221 | 20% |
| S9-adx | 15m | VOL_EXPANSION | 77 | -0.126 | 26% |
| S9-adx | 5m | EVENT_DRIVEN | 4 | +0.108 | 50% |
| S9-adx | 5m | HIGH_VOL | 31 | +0.122 | 45% |
| S9-adx | 5m | LOW_VOL | 2 | +0.657 | 100% |
| S9-adx | 5m | RANGE | 3 | +0.730 | 100% |
| S9-adx | 5m | TREND_DOWN | 7 | +0.050 | 43% |
| S9-adx | 5m | TREND_UP | 3 | +0.019 | 33% |
| S9-adx | 5m | VOL_EXPANSION | 53 | +0.110 | 45% |
| S9-atr-band | 15m | COMPRESSION | 6 | -0.107 | 33% |
| S9-atr-band | 15m | EVENT_DRIVEN | 3 | -0.176 | 33% |
| S9-atr-band | 15m | HIGH_VOL | 34 | -0.013 | 35% |
| S9-atr-band | 15m | LIQUIDITY_STRESS | 1 | -0.422 | 0% |
| S9-atr-band | 15m | LOW_VOL | 18 | -0.104 | 28% |
| S9-atr-band | 15m | RANGE | 9 | +0.085 | 44% |
| S9-atr-band | 15m | TREND_DOWN | 99 | -0.162 | 22% |
| S9-atr-band | 15m | TREND_UP | 97 | -0.139 | 27% |
| S9-atr-band | 15m | VOL_EXPANSION | 11 | -0.235 | 18% |
| S9-atr-band | 5m | EVENT_DRIVEN | 3 | +0.293 | 67% |
| S9-atr-band | 5m | HIGH_VOL | 8 | +0.204 | 50% |
| S9-atr-band | 5m | LOW_VOL | 3 | +0.309 | 67% |
| S9-atr-band | 5m | RANGE | 2 | +0.697 | 100% |
| S9-atr-band | 5m | TREND_DOWN | 9 | +0.071 | 44% |
| S9-atr-band | 5m | TREND_UP | 2 | +0.233 | 50% |
| S9-atr-band | 5m | VOL_EXPANSION | 2 | +0.063 | 50% |
| S9-control | 15m | COMPRESSION | 8 | -0.021 | 38% |
| S9-control | 15m | EVENT_DRIVEN | 8 | -0.024 | 38% |
| S9-control | 15m | HIGH_VOL | 69 | -0.078 | 30% |
| S9-control | 15m | LIQUIDITY_STRESS | 1 | -0.422 | 0% |
| S9-control | 15m | LOW_VOL | 24 | -0.077 | 29% |
| S9-control | 15m | RANGE | 14 | +0.087 | 43% |
| S9-control | 15m | TREND_DOWN | 162 | -0.154 | 23% |
| S9-control | 15m | TREND_UP | 168 | -0.154 | 26% |
| S9-control | 15m | VOL_EXPANSION | 95 | -0.106 | 27% |
| S9-control | 5m | COMPRESSION | 1 | +0.506 | 100% |
| S9-control | 5m | EVENT_DRIVEN | 4 | +0.108 | 50% |
| S9-control | 5m | HIGH_VOL | 41 | +0.155 | 49% |
| S9-control | 5m | LOW_VOL | 3 | +0.309 | 67% |
| S9-control | 5m | RANGE | 3 | +0.730 | 100% |
| S9-control | 5m | TREND_DOWN | 10 | +0.123 | 50% |
| S9-control | 5m | TREND_UP | 3 | +0.019 | 33% |
| S9-control | 5m | VOL_EXPANSION | 58 | +0.100 | 45% |
| LEGACY-ST-tight-ATM | 5m | COMPRESSION | 1 | +0.506 | 100% |
| LEGACY-ST-tight-ATM | 5m | EVENT_DRIVEN | 4 | +0.108 | 50% |
| LEGACY-ST-tight-ATM | 5m | HIGH_VOL | 41 | +0.155 | 49% |
| LEGACY-ST-tight-ATM | 5m | LOW_VOL | 3 | +0.309 | 67% |
| LEGACY-ST-tight-ATM | 5m | RANGE | 3 | +0.730 | 100% |
| LEGACY-ST-tight-ATM | 5m | TREND_DOWN | 10 | +0.123 | 50% |
| LEGACY-ST-tight-ATM | 5m | TREND_UP | 3 | +0.019 | 33% |
| LEGACY-ST-tight-ATM | 5m | VOL_EXPANSION | 58 | +0.100 | 45% |
| LEGACY-ST-tight-ITM1 | 5m | COMPRESSION | 4 | +0.244 | 75% |
| LEGACY-ST-tight-ITM1 | 5m | EVENT_DRIVEN | 7 | +0.035 | 43% |
| LEGACY-ST-tight-ITM1 | 5m | HIGH_VOL | 60 | +0.076 | 45% |
| LEGACY-ST-tight-ITM1 | 5m | LOW_VOL | 10 | +0.148 | 60% |
| LEGACY-ST-tight-ITM1 | 5m | RANGE | 8 | +0.106 | 50% |
| LEGACY-ST-tight-ITM1 | 5m | TREND_DOWN | 11 | +0.070 | 45% |
| LEGACY-ST-tight-ITM1 | 5m | TREND_UP | 7 | +0.002 | 43% |
| LEGACY-ST-tight-ITM1 | 5m | VOL_EXPANSION | 73 | +0.079 | 47% |

## Straddle economics (both legs priced, spread and fees on both legs)

| Variant | TF | Avg combined premium (% of spot) | Avg break-even move (% of spot) | Avg max loss (USD per unit) | Avg fees (USD per unit) |
|---|---|---|---|---|---|
| S6-all3 | 15m | 1.66% | 1.70% | 543.23 | 16.37 |
| S6-all3 | 5m | 1.57% | 1.61% | 514.53 | 16.13 |
| S6-base | 15m | 1.65% | 1.70% | 556.01 | 16.28 |
| S6-base | 5m | 1.60% | 1.65% | 507.19 | 15.72 |
| S6-loose | 15m | 1.63% | 1.68% | 539.09 | 16.22 |
| S6-loose | 5m | 1.62% | 1.66% | 515.56 | 15.95 |

Break-even move = (combined premium + round-trip fees) / spot, ignoring exit spread; the strike is the spot proxy. A straddle needs the underlying to move about this much by exit to make money.

## Cost contribution (frictionless vs net)

Frictionless = same signals with zero spread, no fees/GST and no breakeven gate (cells with stability runs only). Cost drag = frictionless minus net expectancy.

| Variant | TF | Net R | Frictionless R | Cost drag (R) |
|---|---|---|---|---|
| S1-base | 15m | -0.026 | +0.055 | +0.080 |
| S2-n40 | 5m | -0.030 | +0.015 | +0.045 |

## Supertrend: previous result vs the standardised pipeline

**Previous result** (quoted from `docs/research/STRATEGY_SEARCH.md`; discovery on BTC+ETH, holdout from 2026-07-01; columns: trades, win %, net R, max DD in R):

| Candidate | Discovery | Holdout | Holdout by asset net R (n) |
|---|---|---|---|
| Supertrend Trend Following | tight | ATM | hold x1 | 89, 49%, +0.176, -2.1 | 33, 48%, +0.075, -2.9 | BTC +0.174 (16) / ETH +0.005 (16) / XAUT -0.410 (1) |
| Supertrend Trend Following | tight | ITM1 | hold x1 | 133, 46%, +0.088, -3.3 | 47, 51%, +0.069, -3.7 | BTC +0.167 (22) / ETH -0.001 (24) / XAUT -0.410 (1) |

**Same variants re-run through this pipeline** (all assets, all gates) plus the new filtered variants:

| Variant | TF | Status | Trades | Pooled net R | Holdout net R (n) | Reasons |
|---|---|---|---|---|---|---|
| LEGACY-ST-tight-ATM | 5m | **DATA-INSUFFICIENT** | 123 | +0.142 | +0.054 (34) | 123 trades (need 200); 7223 setups |
| LEGACY-ST-tight-ITM1 | 5m | **DATA-INSUFFICIENT** | 180 | +0.082 | +0.064 (47) | 180 trades (need 200); 7223 setups |
| S9-adx | 15m | **REJECTED** | 329 | -0.127 | -0.060 (103) | holdout net R -0.060 (not positive after costs) |
| S9-adx | 5m | **DATA-INSUFFICIENT** | 103 | +0.135 | +0.019 (25) | 103 trades (need 200); 3776 setups |
| S9-atr-band | 15m | **REJECTED** | 278 | -0.127 | -0.074 (97) | holdout net R -0.074 (not positive after costs) |
| S9-atr-band | 5m | **DATA-INSUFFICIENT** | 29 | +0.209 | -0.077 (6) | 29 trades (need 200); 3728 setups |
| S9-control | 15m | **REJECTED** | 549 | -0.124 | -0.072 (176) | holdout net R -0.072 (not positive after costs) |
| S9-control | 5m | **DATA-INSUFFICIENT** | 123 | +0.142 | +0.054 (34) | 123 trades (need 200); 7224 setups |

The two legacy variants are NOT promoted by this report. Their status above is whatever the standard gates say.

## Limitations (read before trusting any number)

- **Option prices are modelled, not recorded.** There is no historical option-chain quote history yet (the chain recorder started on 2026-10-02). Prices are Black-Scholes at an as-of ATM IV inferred from real Delta option trades plus a fitted smile; spreads are a pessimistic calibration from one live snapshot (UNVERIFIED historically). Net R therefore carries model risk in both directions.
- **XAUT** options were listed only around 2026-07-24, so XAUT has about two months of option history and is almost entirely inside the holdout. It rarely reaches the 30-trade per-asset minimum.
- **Funding and OI** are hourly series. Where a window lacks them the funding/OI strategy returns NO SIGNAL (see dataset coverage above); nothing is filled in. Funding units are UNVERIFIED in the project notes.
- **The selector reaches ATM and 1-ITM strikes only,** so no OTM bucket was tested.
- **Opening-range sessions** for this 24x7 perp are documented assumptions (UTC), not exchange sessions.
- **Multiple testing:** many cells were tested. A few positive cells are expected by chance, which is why ACCEPTED requires the random-signal control with a Bonferroni adjustment, and why EXPERIMENTAL is never 'proven'.
- **Sample size:** the window is about 10 months. A positive holdout of a few dozen trades is weak evidence whatever its sign.
- GST (18%) and the fee schedule are the project's production values; the 18% GST is UNVERIFIED.
- Nothing in this report changes the main project. No strategy here is registered, activated or tradeable.
