from __future__ import annotations

from delta_intelligence.config.settings import LIVE_CONFIRM_PHRASE, Settings
from delta_intelligence.config.watchlist import DEFAULT_WATCHLIST, get_watchlist


def test_defaults_are_safe() -> None:
    s = Settings.from_env({})
    assert s.trading_mode == "PAPER"
    assert s.delta_env == "TESTNET"
    assert not s.live_mode_fully_authorized
    assert s.rest_base_url == "https://cdn-ind.testnet.deltaex.org"
    assert s.database_url.startswith("sqlite:///")


def test_user_approved_risk_defaults() -> None:
    r = Settings.from_env({}).risk
    assert (r.max_premium_per_trade_pct, r.max_daily_loss_pct, r.max_drawdown_pct, r.max_concurrent_positions) == (
        0.5, 2.0, 8.0, 3)
    assert (r.expiry_guard_hours, r.max_leg_spread_pct, r.min_leg_open_interest, r.correlated_bucket_cap_pct) == (
        2.0, 10.0, 100.0, 1.5)


def test_sell_to_open_cannot_be_enabled_by_env() -> None:
    assert Settings.from_env({"ALLOW_SELL_TO_OPEN": "true"}).risk.allow_sell_to_open is False


def test_unknown_values_fall_back_to_safe() -> None:
    s = Settings.from_env({"DELTA_ENV": "mainnet", "TRADING_MODE": "yolo", "MAX_LEG_SPREAD_PCT": "abc"})
    assert s.delta_env == "TESTNET" and s.trading_mode == "PAPER" and s.risk.max_leg_spread_pct == 10.0


def test_live_needs_both_switches() -> None:
    assert not Settings.from_env({"TRADING_MODE": "LIVE"}).live_mode_fully_authorized
    assert not Settings.from_env({"TRADING_LIVE_CONFIRM": LIVE_CONFIRM_PHRASE}).live_mode_fully_authorized
    assert not Settings.from_env({"TRADING_MODE": "LIVE", "TRADING_LIVE_CONFIRM": "yes"}).live_mode_fully_authorized
    assert Settings.from_env({"TRADING_MODE": "LIVE",
                              "TRADING_LIVE_CONFIRM": LIVE_CONFIRM_PHRASE}).live_mode_fully_authorized


def test_repr_never_shows_secrets() -> None:
    s = Settings.from_env({"DELTA_API_KEY": "key-AAAA1111", "DELTA_API_SECRET": "sec-BBBB2222",
                           "APP_PASSWORD": "pw-CCCC3333"})
    text = repr(s)
    assert "key-AAAA1111" not in text and "sec-BBBB2222" not in text and "pw-CCCC3333" not in text
    assert s.has_credentials


def test_data_env_production_in_paper_matches_trading_env_in_live() -> None:
    paper = Settings.from_env({"DELTA_ENV": "TESTNET"})
    assert paper.data_env == "PRODUCTION"
    assert paper.data_rest_base_url == "https://api.india.delta.exchange"
    live = Settings.from_env({"DELTA_ENV": "TESTNET", "TRADING_MODE": "LIVE"})
    assert live.data_env == "TESTNET"
    assert live.ws_url(private=False) == "wss://socket-ind-pub.testnet.deltaex.org"


def test_base_url_override_and_lookback_clamp() -> None:
    s = Settings.from_env({"DELTA_BASE_URL": "https://example.test/", "LOOKBACK_DAYS": "999"})
    assert s.rest_base_url == "https://example.test"
    assert s.lookback_days == 180


def test_watchlist_default_and_override() -> None:
    assert get_watchlist("") == DEFAULT_WATCHLIST == ("BTCUSD", "ETHUSD", "XAUTUSD")
    assert get_watchlist(" btcusd, ETHUSD ,btcusd") == ("BTCUSD", "ETHUSD")


def test_cost_model_defaults_are_the_production_fee_schedule() -> None:
    """Verified 2026-10-02 from public /v2/products: PRODUCTION options 0.0001 rate, 3.5% cap; TESTNET charges more
    (0.0003, 10%). Back-tests must not be made stricter than production, so the defaults stay on production's."""
    from delta_intelligence.config.settings import CostModel

    c = CostModel()
    assert (c.fallback_taker_rate, c.fallback_maker_rate, c.fallback_premium_cap_rate) == (0.0001, 0.0001, 0.035)
