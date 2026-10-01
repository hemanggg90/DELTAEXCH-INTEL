# P3a: implied-volatility history coverage

Built by `scripts/build_iv_history.py` from real expired Delta India option trades, 2025-12-01 to 2026-10-01. Selection: strikes within +-3% of the index range over each contract's last 32 h. 610 daily expiries; this run fetched 498 new expiries (21,151 contracts) with 0 errors, and earlier runs cached the rest.

## BTC

- 6,797,828 option bars cached, of which 3,984,249 traded; 95.6% of traded bars give a valid IV (failures are trades at or below intrinsic, mostly deep ITM or in the last minutes).

| Time to expiry | Hours with ATM IV | Obs / hour | Longest gap | Median ATM IV | p10-p90 |
|---|---|---|---|---|---|
| 0_6h | 25.0% | 158 | 19 h | 0.271 | [0.178, 0.43] |
| 6_30h | 99.8% | 155 | 2 h | 0.322 | [0.18, 0.506] |
| 30_54h | 8.3% | 106 | 23 h | 0.355 | [0.188, 0.529] |

- Smile (iv - atm = a*k + b*k^2, k = ln K/S): 0_6h: a=-0.51, b=+423.6 (resid sd 0.274); 6_30h: a=-0.79, b=+80.3 (resid sd 0.055); 30_54h: a=-0.81, b=+37.6 (resid sd 0.028)
- Settlement model (30-min TWAP of 5m index closes) vs Delta's actual settlement: median |error| 0.0094%, max 0.1257% over 305 expiries.
- ATM IV (6-30 h) / 24 h realised vol: median 0.85, p10-p90 [0.55, 1.35]; above 1.5 (your debit-veto rule) in 7.2% of hours.

## ETH

- 3,250,377 option bars cached, of which 1,884,919 traded; 95.2% of traded bars give a valid IV (failures are trades at or below intrinsic, mostly deep ITM or in the last minutes).

| Time to expiry | Hours with ATM IV | Obs / hour | Longest gap | Median ATM IV | p10-p90 |
|---|---|---|---|---|---|
| 0_6h | 25.0% | 62 | 19 h | 0.376 | [0.247, 0.569] |
| 6_30h | 99.8% | 54 | 2 h | 0.457 | [0.27, 0.692] |
| 30_54h | 8.3% | 37 | 23 h | 0.493 | [0.296, 0.714] |

- Smile (iv - atm = a*k + b*k^2, k = ln K/S): 0_6h: a=-0.39, b=+429.1 (resid sd 0.319); 6_30h: a=-0.20, b=+50.9 (resid sd 0.061); 30_54h: a=-0.23, b=+20.8 (resid sd 0.028)
- Settlement model (30-min TWAP of 5m index closes) vs Delta's actual settlement: median |error| 0.0115%, max 0.1709% over 305 expiries.
- ATM IV (6-30 h) / 24 h realised vol: median 0.9, p10-p90 [0.62, 1.36]; above 1.5 (your debit-veto rule) in 7.3% of hours.

## Notes

- The 6-30 h bucket is the one that matters: the 6-hour minimum-expiry rule means every trade is entered with 6-30 h to expiry. Its coverage is ~99.8% of hours.
- The 0-6 h bucket exists only in the 6 hours before each daily expiry (hence ~25% of hours), and the 30-54 h bucket only within the 32 h fetch window. Neither is a gap.
- IV is inferred from trade prices against the 5-minute index close of the same bar. Trades earlier within the bar add noise, which the hourly volume-weighted median absorbs.
