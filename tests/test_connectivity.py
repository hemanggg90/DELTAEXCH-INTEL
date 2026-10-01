from __future__ import annotations

from delta_intelligence.brokers.cache import TtlCache
from delta_intelligence.brokers.connectivity import all_critical_passed, run_connectivity_check
from delta_intelligence.brokers.delta_api_client import DeltaClient
from delta_intelligence.brokers.rate_limit import RateLimiter
from delta_intelligence.config.settings import DeltaLimits, Settings

BASE = "https://cdn-ind.testnet.deltaex.org"
PRODUCTS = [{"symbol": s, "id": i} for s, i in (("BTCUSD", 84), ("ETHUSD", 1699), ("XAUTUSD", 131253))]


def client(fake_clock, settings: Settings) -> DeltaClient:
    lim = RateLimiter(DeltaLimits(), clock=fake_clock, sleep=fake_clock.sleep, wall=fake_clock)
    return DeltaClient(BASE, settings.delta_api_key, settings.delta_api_secret, environment="TESTNET", limiter=lim,
                       clock=fake_clock, sleep=fake_clock.sleep, ticker_cache=TtlCache(fake_clock),
                       product_cache=TtlCache(fake_clock))


def public_mocks(mocked, fake_clock, products=PRODUCTS) -> None:
    hdr = {"Request-In-Time": str(int(fake_clock.t * 1_000_000))}
    mocked.get(f"{BASE}/v2/rate_limits/quota", json={"current_quota": 3, "remaining_time_in_milliseconds": 1},
               headers=hdr)
    mocked.get(f"{BASE}/v2/products", json={"success": True, "result": products, "meta": {}})
    mocked.get(f"{BASE}/v2/tickers", json={"success": True, "result": [
        {"symbol": "BTCUSD", "mark_price": "85000", "funding_rate": "0.01"}]})


def test_public_only_passes_without_keys(mocked, fake_clock) -> None:
    s = Settings.from_env({})
    public_mocks(mocked, fake_clock)
    results = run_connectivity_check(s, client(fake_clock, s), require_credentials=False)
    assert all_critical_passed(results)
    assert any(r.warning and "not set" in r.detail for r in results)


def test_keys_required_but_missing_fails(mocked, fake_clock) -> None:
    s = Settings.from_env({})
    public_mocks(mocked, fake_clock)
    assert not all_critical_passed(run_connectivity_check(s, client(fake_clock, s), require_credentials=True))


def test_full_pass_with_keys(mocked, fake_clock) -> None:
    s = Settings.from_env({"DELTA_API_KEY": "k" * 10, "DELTA_API_SECRET": "s" * 10})
    public_mocks(mocked, fake_clock)
    mocked.get(f"{BASE}/v2/wallet/balances", json={"success": True, "result": [
        {"asset_symbol": "USD", "available_balance": "100"}]})
    mocked.get(f"{BASE}/v2/positions/margined", json={"success": True, "result": []})
    mocked.get(f"{BASE}/v2/orders", json={"success": True, "result": []})
    results = run_connectivity_check(s, client(fake_clock, s))
    assert all_critical_passed(results), [r for r in results if not r.ok]


def test_ip_not_whitelisted_reports_ip(mocked, fake_clock) -> None:
    s = Settings.from_env({"DELTA_API_KEY": "k" * 10, "DELTA_API_SECRET": "s" * 10})
    public_mocks(mocked, fake_clock)
    mocked.get(f"{BASE}/v2/wallet/balances", status=401, json={"success": False, "error": {
        "code": "ip_not_whitelisted_for_api_key", "context": {"client_ip": "203.0.113.9"}}})
    results = run_connectivity_check(s, client(fake_clock, s))
    assert not all_critical_passed(results)
    assert "203.0.113.9" in results[-1].detail


def test_missing_watchlist_symbol_fails(mocked, fake_clock) -> None:
    s = Settings.from_env({})
    public_mocks(mocked, fake_clock, products=PRODUCTS[:1])
    results = run_connectivity_check(s, client(fake_clock, s), require_credentials=False)
    wl = next(r for r in results if r.name == "watchlist products")
    assert not wl.ok and "ETHUSD" in wl.detail
