# Delta Exchange India - API notes

Verified on **2026-10-01** against the raw HTML of <https://docs.delta.exchange> and with read-only, unauthenticated GET
requests to the public production and testnet endpoints. No `delta-exchange-mcp` server was available, so nothing here
comes from it.

Legend: **VERIFIED (docs)** = quoted from the official docs; **VERIFIED (live)** = observed in a real API response;
**UNVERIFIED** = not confirmed - code must treat it as an assumption, keep it configurable, and confirm on testnet.

> An AI summary of the docs page invented `/v2/candles`, a 500-candle cap and wrong WebSocket URLs. Only facts that
> were checked against the raw text or a live response are recorded below. Re-verify before relying on anything
> marked UNVERIFIED.

---

## 1. Environments and base URLs

| | REST | WebSocket (private) | WebSocket (public) |
|---|---|---|---|
| **India production** | `https://api.india.delta.exchange` | `wss://socket.india.delta.exchange` | `wss://public-socket.india.delta.exchange` |
| **India testnet ("Demo account")** | `https://cdn-ind.testnet.deltaex.org` | `wss://socket-ind.testnet.deltaex.org` | `wss://socket-ind-pub.testnet.deltaex.org` |

- VERIFIED (docs): `https://api.delta.exchange` is **Delta Global** and cannot be used with India accounts.
- VERIFIED (docs): keys are per environment. A Demo key against production (or the reverse) returns
  `{"success":false,"error":{"code":"invalid_api_key"}}`.
- VERIFIED (live): `GET /v2/settings` returned HTTP 200 on both REST bases.
- REST paths are prefixed `/v2/...`.

## 2. Authentication (REST)

VERIFIED (docs). Every authenticated request carries these headers:

| Header | Value |
|---|---|
| `api-key` | API key |
| `timestamp` | Unix time in **seconds**, as a string |
| `signature` | hex HMAC-SHA256 (below) |
| `User-Agent` | **Required.** Without it the CDN returns `{"error":"Forbidden","message":"Request blocked by CDN"}` |
| `Content-Type` | `application/json` (bodies must be valid JSON) |

**Signature:** `hex(HMAC_SHA256(api_secret, method + timestamp + requestPath + query_string + body))`

- `method` is uppercase (`GET`, `POST`, ...).
- `requestPath` includes `/v2`.
- `query_string` includes the leading `?` when present.
- `body` is the exact JSON string sent, or empty.

Docs example: prehash `GET1542110948/v2/orders?product_id=1&state=open`, secret
`7b6f39dcf660ec1c7c664f612c60410a2bd0c258416b498bf0311f94228f`, signature
`ad767fead0bdbe91ba1e4feb142079245fecd66aa5e47a70b40ba1a4c9b4e3db`.

**This example does not reproduce.** Checked 2026-10-01: HMAC-SHA256 of that prehash with that secret gives
`4e38dda3e6477092f360ba70399266d8145630b22bcc34c0ec7f804d5746877a`. The secret or signature on the page is probably
truncated or redacted. Don't use it as a test vector. Instead:
- Tests check the prehash **format** and compare against `hmac`/`hashlib` directly.
- The real proof is `scripts/check_delta.py` succeeding on testnet.

**Validity window:** "Signature created in the last 5 seconds is allowed." After that the API returns
`{"error":"SignatureExpired","message":"your signature has expired"}`, and the error body includes server time and
request time. Consequences:

- Every retry **must re-sign** with a fresh timestamp.
- Keep the local clock in sync. The client should measure the offset from server time and use it.

### Errors to map to typed exceptions

| Payload | Meaning |
|---|---|
| `{"error":{"code":"invalid_api_key"}}` | The key doesn't exist, was regenerated, or belongs to the wrong environment. |
| `{"error":"SignatureExpired"}` | The signature arrived more than 5 s after it was made. |
| `{"error":{"code":"Signature Mismatch"}}` | Wrong method, path, query, body or secret in the prehash. |
| `{"error":"UnauthorizedApiAccess"}` | The key lacks the permission needed (Read Data or Trading). |
| `{"error":{"code":"ip_not_whitelisted_for_api_key"}}` | The calling IP isn't whitelisted. Docs say the IP appears in the error response; surface it. |
| `{"error":"Forbidden","message":"Request blocked by CDN"}` | Missing User-Agent, or the IP is hidden or blocked. |
| `insufficient_margin`, `close_position_insufficient_margin` | Order or position errors. |

**Live testnet error responses (VERIFIED, 2026-10-01). These differ from the docs' wording:**

| Request | HTTP | Body |
|---|---|---|
| Private endpoint, no auth headers | 401 | `{"error":"unauthorized","success":false}` |
| Unknown key | 401 | `{"error":{"code":"invalid_api_key"},"success":false}` |
| Timestamp 60 s old | 401 | `{"error":{"code":"expired_signature","context":{"request_time":1790879598,"server_time":1790879659}},"success":false}`. The code is **`expired_signature`**, not `SignatureExpired`. `context.server_time` gives a precise clock-sync point. |
| No `User-Agent` | 403 | An HTML CDN page, not JSON. |

- Responses carry an HTTP `Date` header (1 s resolution), usable for coarse clock sync.
- VERIFIED (live): responses also carry `Request-In-Time` and `Request-Out-Time` headers, server times in
  **microseconds**. They give a precise clock-sync point; the client prefers `Request-In-Time`.
- Error bodies may be a string (`"error":"unauthorized"`) or an object (`"error":{"code":...}`). The parser must handle
  both.

### Key permissions and IP whitelist (VERIFIED, docs)

- There are two permissions:
  - **Read Data**: market data.
  - **Trading**: orders, positions, wallets, margin and leverage changes.
- A key with Trading permission **requires whitelisted IPs**: several are allowed, IPv4 and IPv6. Common failures:
  - the machine uses IPv6 while IPv4 was whitelisted (or the reverse)
  - the ISP rotates the home IP
  - a cloud VM has no static IP

  For production, the docs recommend a cloud host with a static IP.
- After more than 5 wrong OTP/MFA attempts, key creation is blocked for 30 minutes.

## 3. Rate limits

VERIFIED (docs):

- The quota is **20,000 units per fixed 5-minute window**, reset every 5 minutes. Unauthenticated requests are throttled
  per IP, authenticated ones per user.
- Endpoint weights:

  | Weight | Endpoints |
  |---|---|
  | 3 | Get Products, Get Orderbook, Get Tickers, Get Open Orders, Get Open Positions, Get Balances, OHLC Candles |
  | 5 | Place, Edit or Delete Order; Add Position Margin |
  | 10 | Get Order History, Get Fills, Get Txn Logs |
  | 25 | Batch order APIs |
  | 1 | Every other endpoint |

- Exceeding the quota returns HTTP **429** with header **`X-RATE-LIMIT-RESET`**: milliseconds until the next allowed
  request.
- `GET /v2/rate_limits/quota` returns `{"current_quota": 42, "remaining_time_in_milliseconds": 120632}` and needs no
  auth.
- The matching engine allows **500 operations per second per product**. A 50-order batch counts as 50. A breach can
  return 429 even when the REST quota is fine.

## 4. Products: `GET /v2/products`

VERIFIED (live). Filter with `?contract_types=perpetual_futures&states=live`. Production listed **226** live perps;
testnet listed **16**. The endpoint is paginated by cursor (`after`, `before`, `page_size`).

Product fields:

```
annualized_funding, auction_finish_time, auction_start_time, barrier_price, basis_factor_max_limit, contract_type,
contract_unit_currency, contract_value, default_leverage, description, disruption_reason, funding_method, id,
impact_notional, impact_size, initial_margin, initial_margin_scaling_factor, insurance_fund_margin_contribution,
is_quanto, launch_time, liquidation_penalty_factor, maintenance_margin, maintenance_margin_scaling_factor,
maker_commission_rate, max_leverage_notional, notional_type, position_notional_limit, position_size_limit, price_band,
product_specs, quoting_asset, settlement_price, settlement_time, settling_asset, short_description, spot_index, state,
strike_price, symbol, taker_commission_rate, tick_size, trading_status, ui_config, underlying_asset
```

`product_specs` contains `rate_exchange_interval: 28800` (8 h funding), `funding_clamp_value: 0.05`,
`isolated_liq_penalty_factor` and `only_reduce_only_orders_allowed`.

Default watchlist, as returned on 2026-10-01. Numbers are strings in the API; margins are in **percent**.

| Symbol | id | contract_value | tick_size | initial_margin | maintenance_margin | default_leverage | maker | taker | position_size_limit |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSD | 27 | 0.001 BTC | 0.5 | 0.5 | 0.25 | 200 | 0.0002 | 0.0005 | 125000 |
| ETHUSD | 3136 | 0.01 ETH | 0.05 | 0.5 | 0.25 | 200 | 0.0002 | 0.0005 | 162683 |
| SOLUSD | 14823 | 1 SOL | 0.0001 | 1 | 0.5 | 100 | 0.0002 | 0.0005 | 11111 |
| XRPUSD | 14969 | 1 XRP | 0.0001 | 1 | 0.5 | 100 | 0.0002 | 0.0005 | 300000 |

- `settling_asset` and `quoting_asset` are **USD** for all four: P&L and margin are in USD.
- All four, plus DOGEUSD, also exist on testnet.
- Other liquid production perps by 24 h turnover on 2026-10-01: XAUTUSD and PAXGUSD (gold tokens, not on testnet),
  DOGEUSD, BNBUSD.
- **Product ids differ between environments.** VERIFIED (live), 2026-10-01: testnet BTCUSD=84, ETHUSD=1699,
  SOLUSD=92572, XRPUSD=93723; production BTCUSD=27, ETHUSD=3136. Always resolve symbol → id from `/v2/products` at
  runtime; never hard-code ids.

## 5. Tickers: `GET /v2/tickers`, `GET /v2/tickers/{symbol}`

VERIFIED (live). Fields:

```
close, contract_type, contract_value, description, funding_rate, greeks, high, leverage, low, ltp_change_24h,
mark_basis, mark_change_24h, mark_high_24h, mark_low_24h, mark_price, oi, oi_change_usd_6h, oi_contracts,
oi_reduce_only_mode, oi_value, oi_value_symbol, oi_value_usd, open, price_band{lower_limit,upper_limit},
product_id, product_trading_status, quotes{best_bid,best_ask,bid_size,ask_size,...}, size, spot_price, symbol,
tags, tick_size, time, timestamp, turnover, turnover_symbol, turnover_usd, underlying_asset_symbol, volume
```

- `timestamp` is in microseconds. In one sample the ISO string `time` read **24 h behind** `timestamp`. **Use
  `timestamp`.**
- Filter the list call with `?contract_types=perpetual_futures`.

## 6. Historical candles: `GET /v2/history/candles`

VERIFIED (docs + live). This endpoint is public.

- Params: `resolution`, `symbol`, `start`, `end`. Times are Unix **seconds**.
- Resolutions come from the API's validation error: `5s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d, 1w`.
- Each item is `{time (s, bar open), open, high, low, close, volume}`.
- Results are **newest first**.
- The response **includes the still-forming bar**: it must be dropped.
- Per-response cap:
  - Docs: "it can return only upto 2000 candles maximum in a response".
  - Live: one 5m request returned **4000**.
  - The client pages in **≤2000-bar windows**, which is safe under either.
- Symbol prefixes (VERIFIED live, `success: true`). Volume is `null` for all three.
  - `MARK:BTCUSD`: mark-price OHLC.
  - `FUNDING:BTCUSD`: funding rate as OHLC. Constant within each 8 h period, and some hourly bars are missing.
    Values can be **negative** (VERIFIED live), so a positive-price check must not be applied.
  - `OI:BTCUSD`: open-interest OHLC.
  - Index prices use `.DE...` symbols; BTCUSD's index is `.DEXBTUSD` (docs).
- **Daily boundary:** `1d` bars open at **00:00 UTC (05:30 IST)** (VERIFIED live).

## 7. Funding

- VERIFIED (live): `product_specs.rate_exchange_interval = 28800` s, so funding is every 8 h.
- VERIFIED (live, 3 days of `FUNDING:BTCUSD` 1h bars): the series steps at **00:00, 08:00 and 16:00 UTC**, which is
  **05:30, 13:30 and 21:30 IST**.
- VERIFIED (docs): the WebSocket `funding_rate` channel carries `next_funding_realization` (field `nfr`, in µs).
  Prefer it over computing the time.
- **UNVERIFIED:** the units of `funding_rate`. Values look like `0.0052`; the working assumption is *percent per 8 h*.
  Supporting evidence (2026-10-02): `FUNDING:BTCUSD` sat at exactly **0.01** for most of Aug-Sep 2026. That is the
  common 0.01%-per-8h baseline (interest component), consistent with percent units. It is still not confirmed.
  Confirm against the exchange UI or a real funding transaction on testnet before using in P&L.
- **UNVERIFIED:** whether the value during period *[t, t+8h)* is the rate charged at *t* or the rate that will be
  charged at *t+8h*. Funding P&L in the backtest must state which convention it uses.

## 8. Orders, positions, leverage, margin

VERIFIED (docs).

**`POST /v2/orders`** (`CreateOrderRequest`)
- Required: `product_id` **or** `product_symbol`, `size` (integer contracts), `side` (`buy`/`sell`) and `order_type`
  (`limit_order`/`market_order`).
- Optional:
  - `limit_price`
  - `stop_order_type` (`stop_loss_order`/`take_profit_order`), with `stop_price`, `trail_amount` and
    `stop_trigger_method` (`mark_price`/`last_traded_price`/`spot_price`)
  - `time_in_force` (`gtc`/`ioc`), `post_only`, `reduce_only`
  - `client_order_id`: at most **32 characters**, unique among open orders
  - `cancel_orders_accepted`, `mmp`
- Bracket fields on the entry order:
  - `bracket_stop_loss_price`, `bracket_stop_loss_limit_price`
  - `bracket_take_profit_price`, `bracket_take_profit_limit_price`
  - `bracket_trail_amount`, `bracket_stop_trigger_method`
- Prices are sent as **strings**.

**Other order endpoints**
- `POST /v2/orders/bracket` creates a bracket (`stop_loss_order` and `take_profit_order` objects) on an existing
  **position**. `PUT /v2/orders/bracket` edits it. It applies to the whole position, so no size is sent.
- `DELETE /v2/orders` cancels; `PUT /v2/orders` edits.
- `GET /v2/orders` lists open orders; `/v2/orders/history` returns history.
- Lookup by id or by client order id: `/v2/orders/{order_id}` and `/v2/orders/client_order_id/{client_oid}`. Both
  are VERIFIED from the docs' endpoint list.
- Batch create and edit exist (weight 25).
- `DELETE /v2/orders/all` cancels everything (`CancelAllFilterObject`).

**Leverage and margin mode**
- `POST /v2/products/{product_id}/orders/leverage` with body `{"leverage": 10}` sets leverage; `GET` reads it.
- `PUT /v2/users/margin_mode` with body `{"margin_mode": "isolated" | "portfolio"}` sets the margin mode.

**Positions** (`GET /v2/positions/margined`, `GET /v2/positions`)
- Fields: `size`, `entry_price`, `margin`, `liquidation_price`, `bankruptcy_price`, `adl_level`, `realized_funding`,
  `realized_pnl`, `unrealized_pnl`, `mark_price`, `margin_mode`, `auto_topup`.
- `POST /v2/positions/close_all` closes all positions.

**Wallet:** `GET /v2/wallet/balances`.

**Order book:** `GET /v2/l2orderbook/{symbol}?depth=N` (public). VERIFIED (live) response:
`{"symbol","buy":[{"size":int,"depth":str,"price":str}],"sell":[...],"last_updated_at":µs}`. `depth` is the cumulative
size as a string and may use exponent notation (e.g. `"1.754E+4"`).

**Liquidation formula:** **UNVERIFIED.** Live trading uses the exchange's `liquidation_price`. Paper mode uses an
isolated-margin approximation that is labelled as such.

**Withdrawals:** there are withdrawal-related asset fields. This project implements **no** withdrawal calls.

## 9. Dead-man's switch: `POST /v2/heartbeat/create`, `POST /v2/heartbeat`

VERIFIED (docs).

- Create with `{heartbeat_id, impact: low|medium|high, contract_types?, underlying_assets?, product_symbols?,
  config: [{action: "cancel_orders", unhealthy_count, tag}]}`.
- Acknowledge with `{heartbeat_id, ttl}`.
- If acknowledgements stop, the exchange runs the action.
- The only documented action is `cancel_orders`. It would also cancel protective bracket stop and target orders,
  leaving positions unprotected.
- **Project decision (2026-10-01): OFF by default**, available only as an opt-in with a warning.
- **UNVERIFIED:** whether `tag` can scope the action to entry orders only.

## 10. WebSocket

VERIFIED (docs).

**Connection limits**
- **150 connections per 5 minutes per IP.** Beyond that you get HTTP 429; wait 5–10 minutes.
- A connection with no activity within **60 s** of opening is disconnected.

**Subscribing**
- Send `{"type":"subscribe","payload":{"channels":[{"name":"<ch>","symbols":["BTCUSD"]}]}}`.
- `["all"]` subscribes to every symbol, but no snapshots are sent for `"all"`.
- The ticker channel sends nothing without a symbol list.

**Auth (current method)**
- Send `{"type":"key-auth","payload":{"api-key":KEY,"timestamp":TS,"signature":SIG}}`, where `TS` is Unix seconds as a
  number and `SIG = hex HMAC_SHA256(secret, "GET" + str(TS) + "/live")`. The same 5 s window applies.
- The old `"type":"auth"` method stopped working on 2025-12-31.
- Responses always have `type: "key-auth"`:

  | status_code | status | Meaning |
  |---|---|---|
  | 200 | `authenticated` | Success. |
  | 400 | `incomplete_payload` | A required field is missing. |
  | 408 | `request_expired` | The timestamp is outside the allowed window. |
  | 404 | `api_key_not_found` | Unknown key. |
  | 401 | `invalid_signature` | Signature doesn't match. |
  | 401 | `ip_not_whitelisted` | The message includes the caller's IP, e.g. "IP address not whitelisted. Your IP: x.x.x.x". |
  | 500 | `internal_server_error` | Exchange-side error. |

**Liveness**
- Send `{"type":"enable_heartbeat"}` after each connect. The server sends `{"type":"heartbeat"}` every **30 s**.
  Reconnect if none arrives within **35 s**.
- Alternative: send `{"type":"ping"}` about every 30 s and expect `pong` within 5 s.

**Channels**
- Public: `ticker` (every 5 s), `ob_l1`, `ob_l2`, `ob_updates`, `trades`, `mark_price`, `candlesticks`, `spot_price`,
  `spot_30mtwap_price`, `funding_rate`, `product_updates`, `system_status`.
- Private: `margins`, `positions`, `orders`, `user_trades`, `v2/user_trades`, `portfolio_margins`, `mmp_trigger`.
- Several legacy channels were moved to the new public endpoint.
- **UNVERIFIED:** the exact subscription name strings (e.g. `v2/ticker` vs `ticker`, `l2_orderbook` vs `ob_l2`) and
  payload schemas. Confirm against testnet in P5 and record the result here.

## 11. Fees and GST

| Item | Status | Value |
|---|---|---|
| Perp maker / taker fee | VERIFIED (live, product fields) | `maker_commission_rate=0.0002` (0.02%), `taker_commission_rate=0.0005` (0.05%) for the four watchlist perps. Read these from `/v2/products`; don't hard-code them. |
| Option fee | VERIFIED (live) | Products show 0.0001 maker/taker. A press article says 0.03% with a 3.5%-of-premium cap; the product field is authoritative, and the cap is UNVERIFIED. |
| GST on fees | **UNVERIFIED** | 18% is stated only by press articles (outlookbusiness.com and others). Configurable as `GST_RATE=0.18`. Confirm on a testnet or real fill. |
| Funding payments | — | Treated as P&L, not a fee. Whether GST applies to funding is UNVERIFIED; assume no. |
| Indian taxes (TDS/VDA) on derivatives | Out of scope | Not modelled; noted in Known Limitations. |

## 12. Options

**Listed option underlyings** (VERIFIED live, 2026-10-02, production AND testnet): **BTC, ETH, XAUT** only.
**There are no SOL options** on Delta Exchange India.

| Underlying | Perp | Spot index | contract_value | tick (prod / testnet) | Settlement time | Expiries listed |
|---|---|---|---|---|---|---|
| BTC | BTCUSD | .DEXBTUSD | 0.001 BTC | 0.1 / **0.5** | 12:00 UTC = 17:30 IST | daily ×3, weekly, monthly, quarterly |
| ETH | ETHUSD | .DEETHUSD | **0.01 ETH** | 0.01 / 0.01 | 12:00 UTC = 17:30 IST | daily ×3, weekly, monthly, quarterly |
| XAUT | XAUTUSD | .DEXAUTUSD | 0.001 XAUT | 0.01 / 0.01 | **16:00 UTC = 21:30 IST** | daily only (2 listed) |

- Premium is quoted in **USD per 1 unit of the underlying**; positions are cash-settled in USD.
- The symbol date (DDMMYY) does not encode the time. The settlement hour differs by underlying (XAUT 16:00 UTC), so
  take `settlement_time` from /v2/products and use the per-underlying hour only as a fallback.
- /v2/products returns at most 500 rows per page; option listings exceed that, so **always paginate**.
- Some older XAUT symbols have a different format (e.g. `C-XAUT-W-220726`); parsers must skip unknown formats.


VERIFIED (live), 2026-10-02.

**Products**
- Underlyings: **BTC, ETH and XAUT**.
- Live product count: 485 calls and 465 puts.
- Expiries: **daily** (the next 3 days), **weekly** (Fridays), **monthly** (last Friday) and quarterly. For example, BTC
  had expiries on 02, 03, 04, 09, 16 and 30 Oct, 27 Nov and 25 Dec.
- Settlement: `settlement_time` is **12:00 UTC (17:30 IST)**.
- Symbol format: `C|P-<ASSET>-<STRIKE>-<DDMMYY>`, e.g. `C-BTC-87000-021026`.
- BTC options: `contract_value` 0.001 BTC, `tick_size` 0.1 (USD premium per 1 BTC).

**Fees**
- `maker/taker_commission_rate` = 0.0001 (0.01%, applied to notional, which is UNVERIFIED).
- `product_specs.premium_commission_rate` = 0.035, i.e. the fee is capped at 3.5% of premium. This confirms the press
  article on the cap; the press figure of 0.03% does not match the product field.

**Tickers** (`/v2/tickers?contract_types=call_options&underlying_asset_symbols=BTC`)
- Prices and size: `mark_price`, `oi`, `turnover_usd`.
- `quotes`: `best_bid`, `best_ask`, sizes, `bid_iv`, `ask_iv`, `mark_iv`.
- `greeks`: `delta`, `gamma`, `theta`, `vega`, `rho`, `spot`.
- Liquidity is concentrated in near-dated, near-ATM strikes. On 2026-10-02 the top call by turnover was a 1-day
  expiry, with a bid/ask of 10.6/12.

**Candles for live options:** `/v2/history/candles` works for option symbols, and so does `MARK:<option symbol>`.

**Expired options history**
- Expired products are listed by `/v2/products?states=expired`, back to at least Dec 2025. The listing has more than
  20,000 expired calls, all paginated.
- `/v2/history/candles` returns candles for expired options. Spot-checks found BTC monthly calls from Jan, Mar, May,
  Jul and Aug 2026.
- **The history is SPARSE.** Bars exist only when there was a trade. In the 7 days before expiry, sampled contracts
  had 102–283 five-minute bars out of 2016.
- `MARK:` series for expired options are equally sparse (145–284 of 2016 bars).
- **Candles continue after settlement:** flat bars repeating the last price with volume 0 (seen for 4 days after a
  26-Sep expiry). Any backtest must cut a contract's bars at its `settlement_time`.

**Pricing convention** (VERIFIED live, 2026-10-02)
- Black-Scholes with **r = 0, q = 0**, S = the spot index (`greeks.spot`), T in **365-day years** to the 12:00 UTC
  expiry, and σ = `quotes.mark_iv` reproduces `mark_price`.
- Checked on 546 live BTC options: median |BS − mark| / mark = 0.014%, 90th percentile 0.09%; |delta error| median 6e-5.
- Premiums are **USD per 1 unit of the underlying** (per 1 BTC); a contract is `contract_value` (0.001 BTC) of that.
- Option tickers have no `settlement_time` field: derive expiry from the symbol (`DDMMYY`, at 12:00 UTC).
- `/v2/products` and `/v2/tickers` accept `underlying_asset_symbols=BTC,ETH`, and `/v2/products` accepts
  comma-separated `contract_types`.

**Settlement** (VERIFIED from 5 expiries, 2026-09-29 to 2026-10-01)
- Expired products carry `settlement_price` = cash payoff per 1 unit; it is 0 when the option expires OTM.
- The settlement index implied by ITM contracts (K ± settlement_price) is identical for every strike of an expiry.
- It matches the **30-minute TWAP of the spot index ending at 12:00 UTC** within ~0.05%. Examples: ETH 2703.01 implied
  vs 2703.07 TWAP; BTC 83941.47 vs 83940.20. The last 1-minute close and a 15-minute TWAP match worse.
- The remaining difference is probably sampling (1-minute candles vs Delta's finer sampling). Treat it as an
  approximation of the exact method.

**UNVERIFIED**
- Option margin for sellers.
- Whether options can carry exchange-side bracket orders.
- Whether historical IV is available anywhere other than being inferred from traded prices.

## 13. Implementation consequences

1. Always re-sign retries, and track the clock offset from the server.
2. Rate limits are a weight budget per fixed 5-minute window: stay about 30% under 20,000.
3. Never auto-retry an order. Use `client_order_id` to find out whether an order whose request timed out was accepted.
4. Drop the forming candle. Treat candle times as UTC bar-open times. Page candles in windows of at most 2000 bars.
5. Resolve product ids from symbols at runtime, for each environment.
6. Use `ticker.timestamp`, not `ticker.time`.
7. Funding interval 8 h, at 00:00, 08:00 and 16:00 UTC. Prefer `nfr` from the WebSocket feed.
8. Live trading needs a static, whitelisted IP. Streamlit Community Cloud is paper-only.
