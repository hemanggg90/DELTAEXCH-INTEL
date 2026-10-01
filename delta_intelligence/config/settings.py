"""
Central configuration.

Everything comes from environment variables, from a local `.env` (python-dotenv) or from Streamlit secrets. Defaults
are safe, so the app runs with no configuration at all: PAPER mode, TESTNET, local SQLite.

Settings are built by `Settings.from_env(env)`, so tests can pass a plain dict instead of touching `os.environ`.
`get_settings()` returns the process-wide instance.

Secrets (`delta_api_key`, `delta_api_secret`, `app_password`) are excluded from `repr()` so they cannot leak into
logs or tracebacks.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

LIVE_CONFIRM_PHRASE = "YES_I_UNDERSTAND_THE_RISK"

# Verified 2026-10-01, see docs/DELTA_API_NOTES.md section 1.
DELTA_ENVIRONMENTS: dict[str, dict[str, str]] = {
    "PRODUCTION": {
        "rest": "https://api.india.delta.exchange",
        "ws_private": "wss://socket.india.delta.exchange",
        "ws_public": "wss://public-socket.india.delta.exchange",
    },
    "TESTNET": {
        "rest": "https://cdn-ind.testnet.deltaex.org",
        "ws_private": "wss://socket-ind.testnet.deltaex.org",
        "ws_public": "wss://socket-ind-pub.testnet.deltaex.org",
    },
}

SECRET_KEYS = ("DELTA_API_KEY", "DELTA_API_SECRET", "APP_PASSWORD")


def _streamlit_secrets() -> Mapping[str, object]:
    """Top-level Streamlit secrets, or {} outside Streamlit or when no secrets file exists."""
    try:
        import streamlit as st

        return {k: v for k, v in st.secrets.items() if not isinstance(v, Mapping)}
    except Exception:
        return {}


def _merged_env() -> dict[str, str]:
    """Environment variables override Streamlit secrets (both are accepted)."""
    merged = {k: str(v) for k, v in _streamlit_secrets().items()}
    merged.update(os.environ)
    return merged


class _Env:
    """Typed accessor over a string mapping. Bad values fall back to the default rather than crashing start-up."""

    def __init__(self, env: Mapping[str, str]):
        self.env = env

    def str(self, name: str, default: str = "") -> str:
        val = self.env.get(name)
        return default if val is None else str(val).strip()

    def bool(self, name: str, default: bool = False) -> bool:
        val = self.str(name).lower()
        if not val:
            return default
        return val in ("1", "true", "yes", "y", "on")

    def float(self, name: str, default: float) -> float:
        try:
            return float(self.str(name) or default)
        except ValueError:
            return default

    def opt_float(self, name: str) -> float | None:
        try:
            val = self.str(name)
            return float(val) if val else None
        except ValueError:
            return None

    def int(self, name: str, default: int) -> int:
        try:
            return int(self.str(name) or default)
        except ValueError:
            return default


@dataclass(frozen=True)
class RiskLimits:
    """Options-only risk limits.

    Approved by the user on 2026-10-01 (account limits) and 2026-10-02 (option limits). Change only with the user's
    agreement. Every structure is defined-risk, so "risk per trade" is the structure's max loss plus fees.
    """

    max_risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 8.0
    max_trades_per_day: int = 6
    max_strategy_exposure_pct: float = 10.0
    max_portfolio_exposure_pct: float = 20.0  # sum of open structures' max losses
    max_concurrent_positions: int = 3
    min_relative_volume: float = 0.3  # of the underlying
    # Options (2026-10-02)
    min_hours_to_expiry: float = 6.0  # otherwise roll to the next daily expiry
    close_before_settlement_min: int = 30  # exit by 17:00 IST for the 17:30 IST settlement
    max_leg_spread_pct: float = 10.0  # bid/ask spread as % of mid, per leg
    max_iv_to_rv_for_debit: float = 1.5  # veto debit structures when implied vol > 1.5x realised vol
    allow_naked_short: bool = False  # never configurable via env: defined-risk only

    @classmethod
    def from_env(cls, e: _Env) -> "RiskLimits":
        d = cls()
        return cls(
            max_risk_per_trade_pct=e.float("MAX_RISK_PER_TRADE_PCT", d.max_risk_per_trade_pct),
            max_daily_loss_pct=e.float("MAX_DAILY_LOSS_PCT", d.max_daily_loss_pct),
            max_drawdown_pct=e.float("MAX_DRAWDOWN_PCT", d.max_drawdown_pct),
            max_trades_per_day=e.int("MAX_TRADES_PER_DAY", d.max_trades_per_day),
            max_strategy_exposure_pct=e.float("MAX_STRATEGY_EXPOSURE_PCT", d.max_strategy_exposure_pct),
            max_portfolio_exposure_pct=e.float("MAX_PORTFOLIO_EXPOSURE_PCT", d.max_portfolio_exposure_pct),
            max_concurrent_positions=e.int("MAX_CONCURRENT_POSITIONS", d.max_concurrent_positions),
            min_relative_volume=e.float("MIN_RELATIVE_VOLUME", d.min_relative_volume),
            min_hours_to_expiry=e.float("MIN_HOURS_TO_EXPIRY", d.min_hours_to_expiry),
            close_before_settlement_min=e.int("CLOSE_BEFORE_SETTLEMENT_MIN", d.close_before_settlement_min),
            max_leg_spread_pct=e.float("MAX_LEG_SPREAD_PCT", d.max_leg_spread_pct),
            max_iv_to_rv_for_debit=e.float("MAX_IV_TO_RV_FOR_DEBIT", d.max_iv_to_rv_for_debit),
        )


@dataclass(frozen=True)
class CostModel:
    """Trading costs. Commission rates and the premium cap are read per product from /v2/products. These are only
    fallbacks (option values verified 2026-10-02: 0.01% commission, capped at 3.5% of premium)."""

    fallback_maker_rate: float = 0.0001
    fallback_taker_rate: float = 0.0001
    fallback_premium_cap_rate: float = 0.035
    # UNVERIFIED: 18% GST on fees is stated only by press articles. See docs/DELTA_API_NOTES.md section 11.
    gst_rate: float = 0.18
    slippage_ticks: int = 1

    @classmethod
    def from_env(cls, e: _Env) -> "CostModel":
        d = cls()
        return cls(
            fallback_maker_rate=e.float("FALLBACK_MAKER_RATE", d.fallback_maker_rate),
            fallback_taker_rate=e.float("FALLBACK_TAKER_RATE", d.fallback_taker_rate),
            fallback_premium_cap_rate=e.float("FALLBACK_PREMIUM_CAP_RATE", d.fallback_premium_cap_rate),
            gst_rate=e.float("GST_RATE", d.gst_rate),
            slippage_ticks=e.int("SLIPPAGE_TICKS", d.slippage_ticks),
        )


@dataclass(frozen=True)
class DeltaLimits:
    """Client-side budget for Delta's REST quota: 20,000 weight per fixed 5-minute window (docs section 3).

    Reads may use `read_budget_fraction` of the quota; orders may go up to `order_budget_fraction`, so exits still
    have headroom when reads are busy.
    """

    quota_per_window: int = 20_000
    window_sec: float = 300.0
    read_budget_fraction: float = 0.7
    order_budget_fraction: float = 0.95
    # Minimum spacing between orders (the matching engine allows 500 ops/s per product; we stay far below).
    min_order_interval_sec: float = 0.2
    # Reads that would have to wait longer than this for budget raise RateLimitError instead of blocking.
    max_read_wait_sec: float = 30.0

    max_attempts: int = 3
    backoff_sec: float = 2.0
    breaker_threshold: int = 3
    breaker_cooldown_sec: float = 30.0
    breaker_max_cooldown_sec: float = 120.0
    auth_failure_cooldown_sec: float = 300.0

    ticker_cache_ttl_sec: float = 2.0
    products_cache_ttl_sec: float = 3600.0
    connect_timeout_sec: float = 3.0
    read_timeout_sec: float = 27.0

    @classmethod
    def from_env(cls, e: _Env) -> "DeltaLimits":
        d = cls()
        return cls(
            read_budget_fraction=e.float("DELTA_READ_BUDGET_FRACTION", d.read_budget_fraction),
            order_budget_fraction=e.float("DELTA_ORDER_BUDGET_FRACTION", d.order_budget_fraction),
            min_order_interval_sec=e.float("DELTA_MIN_ORDER_INTERVAL_SEC", d.min_order_interval_sec),
            max_read_wait_sec=e.float("DELTA_MAX_READ_WAIT_SEC", d.max_read_wait_sec),
            max_attempts=e.int("DELTA_MAX_ATTEMPTS", d.max_attempts),
            backoff_sec=e.float("DELTA_BACKOFF_SEC", d.backoff_sec),
            breaker_threshold=e.int("DELTA_BREAKER_THRESHOLD", d.breaker_threshold),
            breaker_cooldown_sec=e.float("DELTA_BREAKER_COOLDOWN_SEC", d.breaker_cooldown_sec),
            breaker_max_cooldown_sec=e.float("DELTA_BREAKER_MAX_COOLDOWN_SEC", d.breaker_max_cooldown_sec),
            auth_failure_cooldown_sec=e.float("DELTA_AUTH_FAILURE_COOLDOWN_SEC", d.auth_failure_cooldown_sec),
            ticker_cache_ttl_sec=e.float("DELTA_TICKER_CACHE_TTL_SEC", d.ticker_cache_ttl_sec),
            products_cache_ttl_sec=e.float("DELTA_PRODUCTS_CACHE_TTL_SEC", d.products_cache_ttl_sec),
            connect_timeout_sec=e.float("DELTA_CONNECT_TIMEOUT_SEC", d.connect_timeout_sec),
            read_timeout_sec=e.float("DELTA_READ_TIMEOUT_SEC", d.read_timeout_sec),
        )


@dataclass(frozen=True)
class ClockSettings:
    """The three clocks (see utils/timeutil.py)."""

    # (1) Exchange day: minutes after 00:00 UTC at which the exchange day starts. Delta's 1d candles open at 00:00 UTC.
    exchange_day_offset_min: int = 0
    # (2) Risk day: starts at this local time in `risk_day_tz` (daily loss limit, trade counter, reports).
    risk_day_tz: str = "Asia/Kolkata"
    risk_day_start: str = "00:00"
    # (3) Liquidity sessions in IST, "NAME=HH:MM-HH:MM" comma-separated. A window may cross midnight.
    liquidity_sessions: str = "ASIA=05:30-13:30,EUROPE=13:30-21:30,US=19:00-02:00"

    @classmethod
    def from_env(cls, e: _Env) -> "ClockSettings":
        d = cls()
        return cls(
            exchange_day_offset_min=e.int("EXCHANGE_DAY_OFFSET_MIN", d.exchange_day_offset_min),
            risk_day_tz=e.str("RISK_DAY_TZ", d.risk_day_tz),
            risk_day_start=e.str("RISK_DAY_START", d.risk_day_start),
            liquidity_sessions=e.str("LIQUIDITY_SESSIONS", d.liquidity_sessions),
        )


@dataclass(frozen=True)
class Settings:
    delta_env: str = "TESTNET"
    delta_api_key: str = field(default="", repr=False)
    delta_api_secret: str = field(default="", repr=False)
    delta_base_url_override: str = ""
    # Where market data (public candles/tickers) comes from. PAPER research uses the real PRODUCTION market by default;
    # LIVE forces the data venue to match the trading venue (see `data_env`).
    delta_data_env: str = "PRODUCTION"

    trading_mode: str = "PAPER"
    trading_live_confirm: str = field(default="", repr=False)

    app_password: str = field(default="", repr=False)
    app_private: bool = False

    database_url: str = ""
    data_cache_dir: Path = PROJECT_ROOT / "data_cache"
    logs_dir: Path = PROJECT_ROOT / "logs"

    usdinr_rate: float | None = None
    paper_starting_capital_usd: float = 10_000.0
    default_timeframe: str = "5m"
    lookback_days: int = 60

    risk: RiskLimits = field(default_factory=RiskLimits)
    costs: CostModel = field(default_factory=CostModel)
    delta: DeltaLimits = field(default_factory=DeltaLimits)
    clocks: ClockSettings = field(default_factory=ClockSettings)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = _Env(_merged_env() if env is None else env)
        delta_env = e.str("DELTA_ENV", "TESTNET").upper()
        if delta_env not in DELTA_ENVIRONMENTS:
            delta_env = "TESTNET"  # unknown value: fall back to the safe environment
        data_env = e.str("DELTA_DATA_ENV", "PRODUCTION").upper()
        if data_env not in DELTA_ENVIRONMENTS:
            data_env = "PRODUCTION"
        mode = e.str("TRADING_MODE", "PAPER").upper()
        if mode not in ("PAPER", "LIVE"):
            mode = "PAPER"
        cache_dir = Path(e.str("DATA_CACHE_DIR") or PROJECT_ROOT / "data_cache")
        return cls(
            delta_env=delta_env,
            delta_api_key=e.str("DELTA_API_KEY"),
            delta_api_secret=e.str("DELTA_API_SECRET"),
            delta_base_url_override=e.str("DELTA_BASE_URL").rstrip("/"),
            delta_data_env=data_env,
            trading_mode=mode,
            trading_live_confirm=e.str("TRADING_LIVE_CONFIRM"),
            app_password=e.str("APP_PASSWORD"),
            app_private=e.bool("APP_PRIVATE", False),
            database_url=e.str("DATABASE_URL") or f"sqlite:///{(cache_dir / 'delta_intelligence.db').as_posix()}",
            data_cache_dir=cache_dir,
            logs_dir=Path(e.str("LOGS_DIR") or PROJECT_ROOT / "logs"),
            usdinr_rate=e.opt_float("USDINR_RATE"),
            paper_starting_capital_usd=e.float("PAPER_STARTING_CAPITAL_USD", 10_000.0),
            default_timeframe=e.str("DEFAULT_TIMEFRAME", "5m"),
            lookback_days=max(10, min(180, e.int("LOOKBACK_DAYS", 60))),
            risk=RiskLimits.from_env(e),
            costs=CostModel.from_env(e),
            delta=DeltaLimits.from_env(e),
            clocks=ClockSettings.from_env(e),
        )

    # ---- environment -----------------------------------------------------------------------------------------
    @property
    def rest_base_url(self) -> str:
        """REST base for the TRADING environment (authenticated calls)."""
        return self.delta_base_url_override or DELTA_ENVIRONMENTS[self.delta_env]["rest"]

    @property
    def data_env(self) -> str:
        """Environment that market data comes from. In LIVE mode it must match the trading venue."""
        return self.delta_env if self.is_live_mode else self.delta_data_env

    @property
    def data_rest_base_url(self) -> str:
        if self.data_env == self.delta_env and self.delta_base_url_override:
            return self.delta_base_url_override
        return DELTA_ENVIRONMENTS[self.data_env]["rest"]

    def ws_url(self, private: bool) -> str:
        env = self.delta_env if private else self.data_env
        return DELTA_ENVIRONMENTS[env]["ws_private" if private else "ws_public"]

    @property
    def has_credentials(self) -> bool:
        return bool(self.delta_api_key and self.delta_api_secret)

    # ---- trading mode ----------------------------------------------------------------------------------------
    @property
    def is_live_mode(self) -> bool:
        return self.trading_mode == "LIVE"

    @property
    def live_mode_fully_authorized(self) -> bool:
        """LIVE needs BOTH the mode flag AND the confirmation phrase. One flipped variable is never enough."""
        return self.is_live_mode and self.trading_live_confirm == LIVE_CONFIRM_PHRASE

    @property
    def parquet_cache_dir(self) -> Path:
        return self.data_cache_dir / "parquet_cache" / self.data_env.lower()

    @property
    def csv_dir(self) -> Path:
        return self.data_cache_dir / "csv"


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings.from_env()
    return _SETTINGS


def set_settings(settings: Settings | None) -> None:
    """Replace the process-wide settings (tests, or the private-deployment credentials panel)."""
    global _SETTINGS
    _SETTINGS = settings
