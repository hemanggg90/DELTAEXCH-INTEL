# P3 research report: strategies as BTC/ETH option trades

Generated 2026-10-01 20:21 UTC from cached Delta Exchange India data since 2025-12-01. Produced by `scripts/research_report.py`; regenerate rather than edit.

**How to read this:**
- **Net R** = net USD P&L / the structure's max loss (premium or spread risk + entry fees). It is after modelled bid/ask spreads (75th-percentile live spreads), fees (0.01% capped at 3.5% of premium) and 18% GST (unverified).
- **Entry and exit rules:** entry at the signal bar close; exit at the underlying stop (checked first), the target, 4 h, or 30 min before the 17:30 IST settlement.
- **Pricing:** option prices are Black-Scholes at the as-of implied vol inferred from real Delta option trades.
- **Validation** compares the model mid with real trades in the same contract and bar.
- **Parameters** were fixed in advance; nothing was tuned on this data.

## BTC

87,796 five-minute bars, 01 Dec 2025 -> 01 Oct 2026.

### Default structure per strategy

| Strategy | Structure | Trades | Days | Win % | Net R / trade | IS net R | OOS net R (n) | Folds (net R) | Underlying R | Fees / risk | Max DD (R) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| OI Buildup Confirmation | LONG_OPTION | 1739 | 304 | 33% | -0.064 | -0.063 | -0.066 (515) | -0.07 / -0.06 / -0.06 / -0.07 | -0.016 | 4.2% | -111.6 |
| Momentum | LONG_OPTION | 3468 | 301 | 33% | -0.064 | -0.061 | -0.075 (907) | -0.06 / -0.05 / -0.07 / -0.08 | -0.028 | 4.0% | -224.5 |
| Donchian Channel Breakout | LONG_OPTION | 2415 | 305 | 33% | -0.065 | -0.058 | -0.081 (740) | -0.06 / -0.06 / -0.06 / -0.09 | -0.012 | 4.2% | -160.8 |
| MACD Signal Crossover | LONG_OPTION | 2459 | 305 | 38% | -0.067 | -0.058 | -0.088 (761) | -0.06 / -0.06 / -0.06 / -0.09 | +0.035 | 4.4% | -166.8 |
| Supertrend Trend Following | LONG_OPTION | 1580 | 305 | 47% | -0.070 | -0.074 | -0.063 (513) | -0.10 / -0.06 / -0.07 / -0.05 | +0.020 | 4.2% | -111.7 |
| Inside Bar Breakout | LONG_OPTION | 4970 | 305 | 33% | -0.071 | -0.068 | -0.076 (1452) | -0.06 / -0.06 / -0.08 / -0.08 | -0.025 | 4.4% | -351.4 |
| CPR Breakout | LONG_OPTION | 1214 | 262 | 32% | -0.076 | -0.073 | -0.083 (353) | -0.08 / -0.06 / -0.06 / -0.10 | -0.024 | 4.5% | -92.2 |
| Opening Range Breakout | LONG_OPTION | 544 | 304 | 31% | -0.083 | -0.088 | -0.071 (162) | -0.10 / -0.09 / -0.08 / -0.06 | -0.039 | 5.3% | -45.6 |
| EMA 50/200 Crossover | LONG_OPTION | 507 | 238 | 24% | -0.108 | -0.098 | -0.131 (151) | -0.06 / -0.06 / -0.16 / -0.14 | -0.099 | 4.3% | -56.6 |
| Camarilla Pivot Reversal | DEBIT_SPREAD | 2366 | 274 | 14% | -0.189 | -0.186 | -0.197 (723) | -0.21 / -0.18 / -0.17 / -0.20 | -0.017 | 11.5% | -448.0 |
| VWAP Mean Reversion | DEBIT_SPREAD | 2733 | 296 | 13% | -0.257 | -0.262 | -0.245 (764) | -0.27 / -0.27 / -0.25 / -0.25 | -0.000 | 15.6% | -703.5 |
| Funding Extreme Contrarian | DEBIT_SPREAD | 127 | 45 | 8% | -0.368 | -0.382 | -0.276 (16) | -0.39 / -0.38 / -0.36 / -0.26 | -0.017 | 23.0% | -46.8 |
| Bollinger Band Mean Reversion | DEBIT_SPREAD | 3216 | 305 | 3% | -0.406 | -0.418 | -0.378 (986) | -0.45 / -0.41 / -0.39 / -0.38 | -0.009 | 26.1% | -1305.8 |
| RSI(2) Mean Reversion | DEBIT_SPREAD | 3096 | 305 | 2% | -0.414 | -0.422 | -0.396 (885) | -0.44 / -0.42 / -0.39 / -0.40 | +0.030 | 26.6% | -1283.1 |

### All structures (net R per trade), plus a frictionless check

The frictionless column is a long option with ZERO spread and ZERO fees. It is not achievable; it shows whether any edge exists before costs.

| Strategy | LONG_OPTION | DEBIT_SPREAD | CREDIT_SPREAD | Frictionless long option |
|---|---|---|---|---|
| Opening Range Breakout | -0.083 (544) | -0.391 (544) | -0.312 (544) | -0.008 |
| Momentum | -0.064 (3468) | -0.383 (3467) | -0.385 (3468) | -0.002 |
| VWAP Mean Reversion | -0.069 (2733) | -0.257 (2733) | -0.382 (2733) | -0.009 |
| RSI(2) Mean Reversion | -0.067 (3096) | -0.414 (3096) | -0.345 (3096) | -0.002 |
| Bollinger Band Mean Reversion | -0.067 (3216) | -0.406 (3216) | -0.357 (3216) | -0.000 |
| Supertrend Trend Following | -0.070 (1580) | -0.319 (1580) | -0.247 (1580) | -0.004 |
| EMA 50/200 Crossover | -0.108 (507) | -0.229 (507) | -0.335 (507) | -0.042 |
| Donchian Channel Breakout | -0.065 (2415) | -0.284 (2415) | -0.338 (2415) | +0.001 |
| MACD Signal Crossover | -0.067 (2459) | -0.359 (2459) | -0.342 (2459) | -0.001 |
| CPR Breakout | -0.076 (1214) | -0.384 (1214) | -0.362 (1214) | -0.009 |
| Camarilla Pivot Reversal | -0.071 (2366) | -0.189 (2366) | -0.365 (2365) | -0.009 |
| Inside Bar Breakout | -0.071 (4970) | -0.384 (4970) | -0.350 (4969) | -0.004 |
| Funding Extreme Contrarian | -0.083 (127) | -0.368 (127) | -0.340 (127) | -0.016 |
| OI Buildup Confirmation | -0.064 (1739) | -0.344 (1739) | -0.362 (1739) | +0.001 |

Setups skipped (default structures): {'overlap': 8431, 'no_iv_exit': 728, 'no_listed_expiry': 80}. `no_iv` = no observed IV within 3 h; `overlap` = a trade was already open.

### Model vs real option prices

81993 legs had a real trade in the same contract and 5-minute bar: median |model - real| = 5.4%, median bias +1.5% (positive = model above real), p90 20.9%.

### Ranker vs random selection (walk-forward, only already-closed trades)

- Decision points: 774 (NO TRADE at 774; ranker picked a strategy without a setup at 0)
- Trades taken by the ranker: 0 over 0 days
- Taking every signal: -0.171 net R per trade; trades NO TRADE avoided averaged -0.171
- The ranker returned NO TRADE at every decision point, because no strategy's shrunk, net-of-cost edge cleared the minimum. That is the intended behaviour when nothing works. It also means the ranker's lift over random cannot be measured on this library.

## ETH

87,798 five-minute bars, 01 Dec 2025 -> 01 Oct 2026.

### Default structure per strategy

| Strategy | Structure | Trades | Days | Win % | Net R / trade | IS net R | OOS net R (n) | Folds (net R) | Underlying R | Fees / risk | Max DD (R) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Momentum | LONG_OPTION | 3893 | 305 | 33% | -0.075 | -0.075 | -0.075 (1088) | -0.07 / -0.08 / -0.08 / -0.07 | -0.014 | 3.0% | -293.0 |
| Inside Bar Breakout | LONG_OPTION | 4930 | 305 | 34% | -0.081 | -0.079 | -0.087 (1407) | -0.08 / -0.07 / -0.08 / -0.09 | +0.008 | 3.2% | -400.9 |
| CPR Breakout | LONG_OPTION | 1168 | 260 | 31% | -0.088 | -0.087 | -0.091 (355) | -0.09 / -0.09 / -0.09 / -0.09 | -0.073 | 3.3% | -103.0 |
| OI Buildup Confirmation | LONG_OPTION | 1645 | 304 | 32% | -0.089 | -0.087 | -0.094 (468) | -0.10 / -0.09 / -0.07 / -0.09 | -0.046 | 3.0% | -146.3 |
| Opening Range Breakout | LONG_OPTION | 533 | 305 | 31% | -0.089 | -0.082 | -0.107 (164) | -0.07 / -0.08 / -0.10 / -0.11 | -0.043 | 4.1% | -48.3 |
| MACD Signal Crossover | LONG_OPTION | 2430 | 305 | 37% | -0.092 | -0.090 | -0.096 (731) | -0.09 / -0.09 / -0.09 / -0.10 | +0.021 | 3.2% | -223.4 |
| Donchian Channel Breakout | LONG_OPTION | 2337 | 305 | 32% | -0.100 | -0.092 | -0.118 (700) | -0.09 / -0.11 / -0.09 / -0.11 | -0.047 | 3.1% | -234.9 |
| Supertrend Trend Following | LONG_OPTION | 1511 | 305 | 45% | -0.109 | -0.113 | -0.101 (468) | -0.13 / -0.12 / -0.09 / -0.10 | -0.020 | 3.1% | -165.1 |
| EMA 50/200 Crossover | LONG_OPTION | 513 | 243 | 26% | -0.121 | -0.094 | -0.178 (162) | -0.14 / -0.08 / -0.07 / -0.18 | -0.041 | 3.2% | -62.9 |
| Camarilla Pivot Reversal | DEBIT_SPREAD | 2266 | 279 | 13% | -0.235 | -0.233 | -0.240 (672) | -0.27 / -0.21 / -0.21 / -0.25 | +0.049 | 7.8% | -532.6 |
| VWAP Mean Reversion | DEBIT_SPREAD | 3087 | 299 | 7% | -0.333 | -0.321 | -0.361 (916) | -0.36 / -0.32 / -0.29 / -0.37 | +0.078 | 11.1% | -1028.5 |
| Bollinger Band Mean Reversion | DEBIT_SPREAD | 3059 | 305 | 2% | -0.357 | -0.335 | -0.406 (979) | -0.38 / -0.32 / -0.32 / -0.41 | +0.000 | 13.4% | -1093.5 |
| RSI(2) Mean Reversion | DEBIT_SPREAD | 2978 | 305 | 2% | -0.363 | -0.342 | -0.413 (896) | -0.38 / -0.33 / -0.32 / -0.42 | +0.015 | 13.1% | -1081.6 |
| Funding Extreme Contrarian | DEBIT_SPREAD | 128 | 47 | 3% | -0.392 | -0.379 | -0.424 (35) | -0.44 / -0.38 / -0.35 / -0.44 | -0.113 | 12.4% | -50.1 |

### All structures (net R per trade), plus a frictionless check

The frictionless column is a long option with ZERO spread and ZERO fees. It is not achievable; it shows whether any edge exists before costs.

| Strategy | LONG_OPTION | DEBIT_SPREAD | CREDIT_SPREAD | Frictionless long option |
|---|---|---|---|---|
| Opening Range Breakout | -0.089 (533) | -0.314 (533) | -0.192 (533) | -0.002 |
| Momentum | -0.075 (3893) | -0.366 (3893) | -0.267 (3893) | +0.005 |
| VWAP Mean Reversion | -0.080 (3087) | -0.333 (3087) | -0.271 (3087) | -0.001 |
| RSI(2) Mean Reversion | -0.087 (2978) | -0.363 (2978) | -0.255 (2978) | -0.007 |
| Bollinger Band Mean Reversion | -0.079 (3059) | -0.357 (3059) | -0.250 (3059) | +0.000 |
| Supertrend Trend Following | -0.109 (1511) | -0.351 (1511) | -0.229 (1511) | -0.024 |
| EMA 50/200 Crossover | -0.121 (513) | -0.282 (513) | -0.250 (513) | -0.032 |
| Donchian Channel Breakout | -0.100 (2337) | -0.334 (2336) | -0.263 (2337) | -0.014 |
| MACD Signal Crossover | -0.092 (2430) | -0.354 (2430) | -0.251 (2430) | -0.009 |
| CPR Breakout | -0.088 (1168) | -0.351 (1168) | -0.242 (1168) | -0.007 |
| Camarilla Pivot Reversal | -0.087 (2266) | -0.235 (2266) | -0.257 (2266) | -0.003 |
| Inside Bar Breakout | -0.081 (4930) | -0.355 (4930) | -0.251 (4930) | +0.000 |
| Funding Extreme Contrarian | -0.111 (128) | -0.392 (128) | -0.274 (128) | -0.032 |
| OI Buildup Confirmation | -0.089 (1645) | -0.361 (1645) | -0.267 (1645) | -0.007 |

Setups skipped (default structures): {'no_iv_exit': 736, 'overlap': 8640, 'no_listed_expiry': 73}. `no_iv` = no observed IV within 3 h; `overlap` = a trade was already open.

### Model vs real option prices

77188 legs had a real trade in the same contract and 5-minute bar: median |model - real| = 5.1%, median bias +1.5% (positive = model above real), p90 19.5%.

### Ranker vs random selection (walk-forward, only already-closed trades)

- Decision points: 775 (NO TRADE at 775; ranker picked a strategy without a setup at 0)
- Trades taken by the ranker: 0 over 0 days
- Taking every signal: -0.173 net R per trade; trades NO TRADE avoided averaged -0.173
- The ranker returned NO TRADE at every decision point, because no strategy's shrunk, net-of-cost edge cleared the minimum. That is the intended behaviour when nothing works. It also means the ranker's lift over random cannot be measured on this library.
