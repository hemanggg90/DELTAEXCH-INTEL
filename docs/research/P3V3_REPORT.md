# P3' research report: v3 strategies as bought options (BTC, ETH, XAUT)

Generated 2026-10-01 21:26 UTC from cached Delta Exchange India data since 2025-12-01. Produced by `scripts/research_report_v3.py`; regenerate rather than edit.

**Result: 0 of 10 strategies ACCEPTED** (none).

**How to read this:**
- **Net R** = net USD P&L / (premium paid + entry fees), i.e. the max loss of a bought option. It is after modelled bid/ask (75th-percentile live spreads), fees (0.01%, capped at 3.5% of premium) and GST 18% (unverified).
- **Entry:** at the signal bar close; ATM or 1-ITM call/put with |delta| 0.40-0.60; expiry with DTE >= 2.5x the expected hold.
- **Exits:** underlying stop (checked first), target, -35% premium stop, time stop, or 2 h before expiry.
- **Breakeven gate:** trades whose expected move did not cover extrinsic premium + spread + fees by 25% were skipped.
- **Premium sources:** `MODEL_REAL_IV` = Black-Scholes at as-of ATM IV inferred from real Delta option trades (+ fitted smile); `MODEL_REAL_IV_ADJ_BUCKET` = same, but using the nearest maturity bucket's IV. There is no recorded chain history yet (the chain recorder starts in P5).
- **Events:** the events calendar has 0 entries (none configured, so the Pre-Event Straddle cannot be tested).

## Verdicts

| Strategy | Verdict | Trades | Pooled net R | OOS net R | Positive on | Reasons |
|---|---|---|---|---|---|---|
| Squeeze Breakout | rejected | 5 | -0.183 | -0.373 | - | only 5 trades (< 200); out-of-sample net R -0.373 not positive; not stable: 8/8 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |
| Liquidity Sweep + MSS | rejected | 803 | -0.094 | -0.160 | - | out-of-sample net R -0.160 not positive; not stable: 6/6 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |
| BOS + Order Block Retest | rejected | 219 | -0.103 | -0.221 | - | out-of-sample net R -0.221 not positive; not stable: 6/6 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |
| FVG Retrace with HTF Bias | rejected | 488 | -0.058 | -0.051 | - | out-of-sample net R -0.051 not positive; not stable: 6/6 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |
| Killzone Breakout | rejected | 531 | -0.033 | -0.058 | BTC | out-of-sample net R -0.058 not positive; not stable: 2/2 parameter perturbations have net R <= 0; positive on 1 underlying(s) (need 2, each >= 30 trades) |
| Asia Range Sweep | rejected | 50 | +0.109 | +0.596 | - | only 50 trades (< 200); not stable: 1/4 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |
| Trend Pullback Continuation | rejected | 230 | -0.079 | -0.165 | - | out-of-sample net R -0.165 not positive; not stable: 6/6 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |
| Daily Momentum Swing | rejected | 38 | -0.256 | -0.300 | - | only 38 trades (< 200); out-of-sample net R -0.300 not positive; not stable: 6/6 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |
| Liquidation Flush Reversal | rejected | 243 | -0.108 | -0.168 | - | out-of-sample net R -0.168 not positive; not stable: 6/6 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |
| Pre-Event Long Straddle | rejected | 0 | - | - | - | only 0 trades (< 200); out-of-sample net R - not positive; not stable: 4/4 parameter perturbations have net R <= 0; positive on 0 underlying(s) (need 2, each >= 30 trades) |

## Per underlying

### Squeeze Breakout

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 24 | 1 | 100% | +0.602 | +0.602 | - (0) | - / - / - / +0.60 | +0.009 | +2.000 | 6.6% | {'MODEL_REAL_IV': 1} | {'breakeven': 23} |
| ETH | 48 | 4 | 0% | -0.379 | -0.381 | -0.373 (1) | -0.43 / -0.33 / -0.38 / -0.37 | -0.103 | -1.000 | 4.6% | {'MODEL_REAL_IV': 4} | {'breakeven': 41, 'no_strike_in_delta_band': 3} |
| XAUT | 2 | 0 | - | - | - | - | - | +0.182 | - | - | - | {'breakeven': 2} |

Parameter perturbations (pooled net R): iv_pct_maxx0.8 -0.135, iv_pct_maxx1.2 -0.214, min_squeeze_barsx0.8 -0.248, min_squeeze_barsx1.2 -0.134, rrx0.8 -0.334, rrx1.2 -0.211, stop_atrx0.8 -0.286, stop_atrx1.2 -0.249

### Liquidity Sweep + MSS

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 545 | 368 | 23% | -0.064 | -0.031 | -0.134 (115) | +0.04 / -0.04 / -0.10 / -0.15 | +0.015 | -0.068 | 4.2% | {'MODEL_REAL_IV': 364, 'MODEL_REAL_IV_ADJ_BUCKET': 4} | {'overlap': 103, 'breakeven': 72, 'no_strike_in_delta_band': 1, 'no_expiry_for_hold': 1} |
| ETH | 527 | 358 | 24% | -0.108 | -0.063 | -0.198 (120) | -0.10 / +0.03 / -0.14 / -0.18 | -0.028 | +0.036 | 3.0% | {'MODEL_REAL_IV': 355, 'MODEL_REAL_IV_ADJ_BUCKET': 3} | {'overlap': 69, 'breakeven': 81, 'no_strike_in_delta_band': 18, 'no_expiry_for_hold': 1} |
| XAUT | 397 | 77 | 17% | -0.174 | -0.210 | -0.086 (22) | -0.25 / -0.27 / -0.10 / -0.05 | -0.089 | -0.253 | 5.1% | {'MODEL_REAL_IV': 76, 'MODEL_REAL_IV_ADJ_BUCKET': 1} | {'no_expiry_for_hold': 125, 'no_strike_in_delta_band': 121, 'breakeven': 50, 'overlap': 24} |

Parameter perturbations (pooled net R): rrx0.8 -0.097, rrx1.2 -0.097, stop_buffer_atrx0.8 -0.090, stop_buffer_atrx1.2 -0.094, windowx0.8 -0.092, windowx1.2 -0.094

### BOS + Order Block Retest

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 654 | 118 | 26% | -0.076 | -0.019 | -0.199 (37) | +0.01 / -0.01 / -0.12 / -0.18 | -0.015 | -0.039 | 4.4% | {'MODEL_REAL_IV': 118} | {'breakeven': 443, 'overlap': 91, 'no_strike_in_delta_band': 1, 'no_expiry_for_hold': 1} |
| ETH | 677 | 96 | 21% | -0.153 | -0.106 | -0.267 (28) | -0.06 / -0.13 / -0.21 / -0.22 | -0.023 | -0.163 | 3.2% | {'MODEL_REAL_IV': 96} | {'breakeven': 460, 'overlap': 87, 'no_strike_in_delta_band': 34} |
| XAUT | 365 | 5 | 60% | +0.198 | +0.580 | -0.057 (3) | +0.86 / +0.30 / - / -0.06 | -0.017 | +0.529 | 6.0% | {'MODEL_REAL_IV': 5} | {'no_expiry_for_hold': 122, 'no_strike_in_delta_band': 120, 'breakeven': 114, 'overlap': 4} |

Parameter perturbations (pooled net R): max_ob_agex0.8 -0.101, max_ob_agex1.2 -0.101, rrx0.8 -0.117, rrx1.2 -0.111, stop_buffer_atrx0.8 -0.105, stop_buffer_atrx1.2 -0.107

### FVG Retrace with HTF Bias

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 1518 | 268 | 29% | -0.039 | -0.065 | +0.028 (77) | +0.00 / -0.15 / -0.03 / +0.04 | +0.020 | +0.040 | 4.6% | {'MODEL_REAL_IV': 266, 'MODEL_REAL_IV_ADJ_BUCKET': 2} | {'breakeven': 1028, 'overlap': 210, 'no_strike_in_delta_band': 8, 'no_expiry_for_hold': 4} |
| ETH | 1424 | 196 | 27% | -0.089 | -0.073 | -0.137 (48) | -0.04 / -0.17 / +0.03 / -0.20 | -0.016 | -0.064 | 3.2% | {'MODEL_REAL_IV': 195, 'MODEL_REAL_IV_ADJ_BUCKET': 1} | {'breakeven': 1014, 'overlap': 128, 'no_strike_in_delta_band': 84, 'no_expiry_for_hold': 2} |
| XAUT | 878 | 24 | 33% | -0.025 | +0.110 | -0.295 (8) | +0.13 / +0.50 / -0.27 / -0.27 | +0.002 | +0.164 | 5.7% | {'MODEL_REAL_IV': 24} | {'no_expiry_for_hold': 247, 'no_strike_in_delta_band': 285, 'breakeven': 313, 'overlap': 9} |

Parameter perturbations (pooled net R): max_fvg_agex0.8 -0.059, max_fvg_agex1.2 -0.058, rrx0.8 -0.070, rrx1.2 -0.087, stop_buffer_atrx0.8 -0.056, stop_buffer_atrx1.2 -0.071

### Killzone Breakout

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 401 | 261 | 33% | +0.020 | +0.056 | -0.053 (87) | +0.10 / -0.13 / +0.15 / -0.04 | +0.074 | +0.054 | 3.7% | {'MODEL_REAL_IV': 223, 'MODEL_REAL_IV_ADJ_BUCKET': 38} | {'breakeven': 119, 'overlap': 17, 'no_strike_in_delta_band': 3, 'no_expiry_for_hold': 1} |
| ETH | 365 | 232 | 29% | -0.085 | -0.099 | -0.053 (70) | -0.11 / -0.11 / -0.03 / -0.09 | +0.033 | -0.045 | 2.5% | {'MODEL_REAL_IV': 214, 'MODEL_REAL_IV_ADJ_BUCKET': 18} | {'breakeven': 111, 'overlap': 9, 'no_strike_in_delta_band': 13} |
| XAUT | 196 | 38 | 24% | -0.069 | -0.044 | -0.140 (10) | -0.04 / -0.23 / +0.12 / -0.14 | +0.007 | -0.130 | 5.3% | {'MODEL_REAL_IV': 38} | {'no_expiry_for_hold': 61, 'no_strike_in_delta_band': 71, 'breakeven': 24, 'overlap': 2} |

Parameter perturbations (pooled net R): rrx0.8 -0.033, rrx1.2 -0.029

### Asia Range Sweep

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 304 | 21 | 33% | +0.117 | -0.089 | +0.531 (7) | -0.20 / +0.25 / +0.11 / +0.65 | +0.022 | +0.041 | 3.7% | {'MODEL_REAL_IV': 21} | {'breakeven': 280, 'overlap': 2, 'no_expiry_for_hold': 1} |
| ETH | 298 | 25 | 32% | +0.030 | -0.114 | +0.401 (7) | -0.16 / -0.35 / +0.01 / +0.40 | +0.013 | +0.315 | 2.3% | {'MODEL_REAL_IV': 25} | {'breakeven': 265, 'overlap': 2, 'no_strike_in_delta_band': 4, 'no_expiry_for_hold': 2} |
| XAUT | 152 | 4 | 50% | +0.556 | -0.392 | +1.504 (2) | -0.44 / -0.34 / +2.32 / +0.69 | +0.035 | +0.842 | 6.8% | {'MODEL_REAL_IV': 4} | {'no_expiry_for_hold': 47, 'no_strike_in_delta_band': 53, 'breakeven': 48} |

Parameter perturbations (pooled net R): rrx0.8 +0.273, rrx1.2 -0.040, stop_buffer_atrx0.8 +0.109, stop_buffer_atrx1.2 +0.088

### Trend Pullback Continuation

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 303 | 127 | 22% | -0.034 | +0.024 | -0.127 (49) | -0.02 / +0.08 / -0.00 / -0.14 | +0.015 | -0.083 | 3.1% | {'MODEL_REAL_IV_ADJ_BUCKET': 103, 'MODEL_REAL_IV': 24} | {'breakeven': 148, 'overlap': 26, 'no_expiry_for_hold': 2} |
| ETH | 217 | 90 | 23% | -0.099 | -0.066 | -0.174 (27) | +0.10 / -0.15 / -0.08 / -0.21 | +0.029 | -0.141 | 2.2% | {'MODEL_REAL_IV_ADJ_BUCKET': 83, 'MODEL_REAL_IV': 7} | {'breakeven': 113, 'overlap': 9, 'no_strike_in_delta_band': 1, 'no_expiry_for_hold': 4} |
| XAUT | 298 | 13 | 0% | -0.387 | -0.374 | -0.397 (7) | -0.31 / -0.43 / -0.28 / -0.49 | -0.039 | -0.799 | 3.9% | {'MODEL_REAL_IV_ADJ_BUCKET': 11, 'MODEL_REAL_IV': 2} | {'no_expiry_for_hold': 88, 'no_strike_in_delta_band': 80, 'breakeven': 111, 'no_iv_exit': 1, 'overlap': 5} |

Parameter perturbations (pooled net R): rrx0.8 -0.077, rrx1.2 -0.095, stop_atrx0.8 -0.077, stop_atrx1.2 -0.100, touch_pctx0.8 -0.078, touch_pctx1.2 -0.061

### Daily Momentum Swing

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 80 | 14 | 7% | -0.235 | -0.141 | -0.406 (5) | -0.46 / -0.44 / +0.96 / -0.41 | -0.182 | +0.041 | 0.7% | {'MODEL_REAL_IV': 11, 'MODEL_REAL_IV_ADJ_BUCKET': 3} | {'no_strike_in_delta_band': 7, 'overlap': 57, 'no_expiry_for_hold': 2} |
| ETH | 95 | 23 | 13% | -0.262 | -0.276 | -0.234 (8) | -0.32 / -0.38 / -0.14 / -0.23 | -0.102 | +0.041 | 0.4% | {'MODEL_REAL_IV': 19, 'MODEL_REAL_IV_ADJ_BUCKET': 4} | {'no_strike_in_delta_band': 12, 'overlap': 57, 'no_expiry_for_hold': 3} |
| XAUT | 6 | 1 | 0% | -0.430 | -0.430 | - (0) | - / - / - / -0.43 | -0.376 | +0.117 | 0.9% | {'MODEL_REAL_IV_ADJ_BUCKET': 1} | {'no_strike_in_delta_band': 1, 'overlap': 4} |

Parameter perturbations (pooled net R): roc_minx0.8 -0.241, roc_minx1.2 -0.261, rrx0.8 -0.223, rrx1.2 -0.271, stop_datrx0.8 -0.223, stop_datrx1.2 -0.271

### Liquidation Flush Reversal

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 292 | 121 | 25% | -0.089 | -0.054 | -0.199 (29) | +0.05 / -0.09 / -0.16 / -0.17 | +0.013 | -0.092 | 4.1% | {'MODEL_REAL_IV': 121} | {'breakeven': 166, 'no_strike_in_delta_band': 3, 'overlap': 1, 'no_expiry_for_hold': 1} |
| ETH | 237 | 109 | 26% | -0.134 | -0.141 | -0.113 (29) | -0.09 / -0.23 / -0.11 / -0.12 | -0.033 | -0.029 | 2.9% | {'MODEL_REAL_IV': 109} | {'breakeven': 113, 'no_strike_in_delta_band': 12, 'overlap': 2, 'no_expiry_for_hold': 1} |
| XAUT | 356 | 13 | 31% | -0.074 | +0.023 | -0.400 (3) | +0.67 / +0.17 / -0.44 / -0.40 | +0.002 | +0.212 | 5.6% | {'MODEL_REAL_IV': 13} | {'no_expiry_for_hold': 118, 'no_strike_in_delta_band': 121, 'breakeven': 103, 'overlap': 1} |

Parameter perturbations (pooled net R): move_atrx0.8 -0.154, move_atrx1.2 -0.086, oi_drop_pctx0.8 -0.121, oi_drop_pctx1.2 -0.114, rrx0.8 -0.139, rrx1.2 -0.066

### Pre-Event Long Straddle

| Underlying | Setups | Trades | Win % | Net R | IS | OOS (n) | Folds | Frictionless | Underlying R | Fees/risk | Premium sources | Skips |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 0 | 0 | - | - | - | - | - | - | - | - | - | {} |
| ETH | 0 | 0 | - | - | - | - | - | - | - | - | - | {} |
| XAUT | 0 | 0 | - | - | - | - | - | - | - | - | - | {} |

Parameter perturbations (pooled net R): iv_pct_maxx0.8 -, iv_pct_maxx1.2 -, move_multx0.8 -, move_multx1.2 -

## Model vs real option prices

- Squeeze Breakout / BTC: 2 legs, median |model-real| 7.3%, bias +7.3%, p90 9.8%
- Squeeze Breakout / ETH: 8 legs, median |model-real| 7.9%, bias +4.1%, p90 14.7%
- Liquidity Sweep + MSS / BTC: 719 legs, median |model-real| 5.1%, bias +2.0%, p90 17.2%
- Liquidity Sweep + MSS / ETH: 670 legs, median |model-real| 4.5%, bias +1.4%, p90 15.9%
- Liquidity Sweep + MSS / XAUT: 125 legs, median |model-real| 4.9%, bias +0.2%, p90 15.6%
- BOS + Order Block Retest / BTC: 234 legs, median |model-real| 5.9%, bias +3.5%, p90 24.4%
- BOS + Order Block Retest / ETH: 187 legs, median |model-real| 4.9%, bias +2.9%, p90 15.1%
- BOS + Order Block Retest / XAUT: 10 legs, median |model-real| 7.4%, bias +5.1%, p90 17.7%
- FVG Retrace with HTF Bias / BTC: 533 legs, median |model-real| 6.0%, bias +2.6%, p90 18.9%
- FVG Retrace with HTF Bias / ETH: 384 legs, median |model-real| 5.0%, bias +1.4%, p90 17.7%
- FVG Retrace with HTF Bias / XAUT: 42 legs, median |model-real| 7.3%, bias -1.2%, p90 21.2%
- Killzone Breakout / BTC: 515 legs, median |model-real| 4.5%, bias +0.6%, p90 14.3%
- Killzone Breakout / ETH: 448 legs, median |model-real| 4.4%, bias +0.0%, p90 13.5%
- Killzone Breakout / XAUT: 65 legs, median |model-real| 4.9%, bias +1.4%, p90 11.5%
- Asia Range Sweep / BTC: 42 legs, median |model-real| 4.8%, bias +0.5%, p90 17.6%
- Asia Range Sweep / ETH: 49 legs, median |model-real| 5.6%, bias +2.0%, p90 15.0%
- Asia Range Sweep / XAUT: 6 legs, median |model-real| 7.8%, bias +5.9%, p90 14.0%
- Trend Pullback Continuation / BTC: 63 legs, median |model-real| 6.9%, bias -1.6%, p90 25.5%
- Trend Pullback Continuation / ETH: 33 legs, median |model-real| 6.2%, bias -2.6%, p90 19.8%
- Trend Pullback Continuation / XAUT: 2 legs, median |model-real| 16.2%, bias -8.3%, p90 22.9%
- Liquidation Flush Reversal / BTC: 239 legs, median |model-real| 7.3%, bias -3.9%, p90 21.7%
- Liquidation Flush Reversal / ETH: 213 legs, median |model-real| 6.2%, bias -0.6%, p90 17.0%
- Liquidation Flush Reversal / XAUT: 24 legs, median |model-real| 7.2%, bias +2.4%, p90 20.3%

## Ranker vs random (walk-forward, only already-closed trades)

- BTC: 387 decision points, NO TRADE at 218, trades taken 19; ranker net R -0.111 (CI (-0.27994112724640013, 0.0963874103193872)) vs random -0.117; lift +0.006 (CI (0.0, 0.018263378319155896))
- ETH: 327 decision points, NO TRADE at 293, trades taken 3; ranker net R -0.240 (CI (-0.34887609250587553, -0.06431484784119432)) vs random -0.240; lift +0.000 (CI (0.0, 0.0))
- XAUT: 169 decision points, NO TRADE at 111, trades taken 8; ranker net R -0.175 (CI (-0.45740795371000903, 0.10574831394696246)) vs random -0.175; lift +0.000 (CI (0.0, 0.0))

## Notes on data coverage

- **XAUT options start 2026-07-24** (first expired XAUT option in the listing).
  - XAUT setups before then can't be priced: they show as `no_expiry_for_hold` (no listed expiry within 45 days) or
    `no_strike_in_delta_band` (an expiry exists, but there is no IV yet to compute delta).
  - XAUT results therefore cover only about 2 months.
- **XAUT implied vol is very low on weekends** (around 5-6% ATM). The gold market behind the token is closed, so
  weekend XAUT options are cheap but also barely move.
- **Funding units are still UNVERIFIED.** Strategy 9 (Liquidation Flush) uses only the sign of funding plus OI changes.
