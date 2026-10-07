"""Shared page furniture built on ui.theme: page header + live status bar, KPI tiles, chips, entity cards, range bars,
quotes, meters, key-value grids, empty states, notices, tables, and the password gate.

Pages call only these helpers (and ui.charts / ui.format): no raw CSS or colours in pages. Colour is never the only
signal: every tone carries an icon (good ●, warning ▲, serious ▲, critical ✖, info ●, neutral ○) and every signed number
carries ▲/▼ and +/-."""
from __future__ import annotations

import hmac
import html as _html
from collections.abc import Callable, Iterable, Sequence

import pandas as pd
import streamlit as st
from pandas.io.formats.style import Styler

from delta_intelligence.config.settings import get_settings
from delta_intelligence.ui import state
from delta_intelligence.ui.format import MISSING, arrow, duration, pct, tone, usd
from delta_intelligence.ui.theme import BLUE, apply_theme, rgba, tone_color, tone_icon
from delta_intelligence.utils.timeutil import fmt_ist, liquidity_sessions_at, next_funding_time, now_utc, parse_sessions


def _esc(x) -> str:
    return _html.escape(MISSING if x is None else str(x))


class Html(str):
    """Trusted markup produced by these helpers; inserted as-is. Plain strings are escaped."""


def _v(x) -> str:
    return x if isinstance(x, Html) else _esc(x)


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


# ---- atoms ---------------------------------------------------------------------------------------------------------
def chip(text: str, tone_: str = "neutral", icon: str | None = None) -> str:
    """Pill with a tinted background; the tone's icon prefixes the text (colour never alone)."""
    ic = tone_icon(tone_) if icon is None else icon
    return f"<span class='app-chip t-{tone_}'>{_esc(ic)} {_esc(text)}</span>" if ic else \
        f"<span class='app-chip t-{tone_}'>{_esc(text)}</span>"


def chips(items: Iterable[tuple[str, str]]) -> str:
    return " ".join(chip(t, tn) for t, tn in items)


def value_html(v: float | None, pct_: float | None = None, money: bool = True, dp: int = 2) -> Html:
    """Signed, coloured value with ▲/▼ and an optional small %, e.g. '▲ +$12.30 (+4.1%)'."""
    if v is None:
        return Html(f"<span class='app-val'>{MISSING}</span>")
    body = usd(v, dp, signed=True) if money else f"{v:+,.{dp}f}"
    small = f"<small>({pct(pct_, 1, signed=True)})</small>" if pct_ is not None else ""
    return Html(f"<span class='app-val app-t-{tone(v)}'>{arrow(v)} {body}{small}</span>")


def meter_html(fraction: float | None, tone_: str = "info") -> str:
    f = 0.0 if fraction is None else max(0.0, min(1.0, float(fraction)))
    return f"<div class='app-meter'><div class='t-{tone_}' style='width:{f * 100:.1f}%'></div></div>"


def meter(fraction: float | None, tone_: str = "info", label: str | None = None) -> None:
    """6px rounded progress bar with an optional caption (the caption carries the meaning, not the colour)."""
    html(meter_html(fraction, tone_) + (f"<div class='app-sub'>{tone_icon(tone_)} {_esc(label)}</div>" if label else ""))


def kv_html(pairs: Sequence[tuple[str, str]]) -> str:
    """Label-left / bold value-right grid. Values are escaped unless they are Html from these helpers."""
    cells = "".join(f"<div class='k'>{_esc(k)}</div><div class='v'>{_v(v)}</div>" for k, v in pairs)
    return f"<div class='app-kv'>{cells}</div>"


def kv_grid(pairs: Sequence[tuple[str, str]]) -> None:
    html(kv_html(pairs))


def empty_state(title: str, hint: str = "") -> None:
    html(f"<div class='app-empty'><b>{_esc(title)}</b>{_esc(hint)}</div>")


def notice(tone_: str, text: str) -> None:
    """An actionable message: say what is wrong and what to do. Material icons give a non-colour signal."""
    fn, icon = {"good": (st.success, ":material/check_circle:"), "warning": (st.warning, ":material/warning:"),
                "serious": (st.warning, ":material/report:"), "critical": (st.error, ":material/error:"),
                }.get(tone_, (st.info, ":material/info:"))
    fn(text, icon=icon)


def check_results(results) -> None:
    """Render connectivity-check results (objects with name / ok / warning / detail) as notices."""
    for r in results:
        notice("warning" if r.warning else "good" if r.ok else "critical", f"{r.name}: {r.detail}")


# ---- composites -----------------------------------------------------------------------------------------------------
def range_bar_html(low: float, mid: float, high: float, current: float | None, favourable: str = "up",
                   labels: tuple[str, str, str] | None = None) -> str:
    """4px track, tick at `mid`, fill mid→current (good on the favourable side, critical otherwise), 12px dot at
    current. Labels underneath: '✖ low · mid · high ◎'."""
    span = (high - low) or 1.0
    pos = lambda x: max(0.0, min(100.0, (x - low) / span * 100))  # noqa: E731
    lo_l, mid_l, hi_l = labels or (f"{low:,.2f}", f"{mid:,.2f}", f"{high:,.2f}")
    parts = ["<div class='app-range'><div class='track'></div>",
             f"<div class='tick' style='left:{pos(mid):.1f}%'></div>"]
    if current is not None:
        good = current >= mid if favourable == "up" else current <= mid
        c = tone_color("good" if good else "critical")
        a, b = sorted((pos(mid), pos(current)))
        parts.append(f"<div class='fill' style='left:{a:.1f}%;width:{b - a:.1f}%;background:{c}'></div>"
                     f"<div class='dot' style='left:{pos(current):.1f}%;background:{c}'></div>")
    parts.append("</div>")
    parts.append(f"<div class='app-range-l'><span>✖ {_esc(lo_l)}</span><span>{_esc(mid_l)}</span>"
                 f"<span>{_esc(hi_l)} ◎</span></div>")
    return "".join(parts)


def range_bar(low: float, mid: float, high: float, current: float | None, favourable: str = "up",
              labels: tuple[str, str, str] | None = None) -> None:
    html(range_bar_html(low, mid, high, current, favourable, labels))


def header_quote(name: str, price: float | None, change_pct: float | None, stats: Sequence[str] = (),
                 low: float | None = None, high: float | None = None, tags: Iterable[tuple[str, str]] = (),
                 dp: int = 2) -> None:
    """Big primary number, a coloured ▲/▼ change, a sub-line of stats and a day-range dot track."""
    px = MISSING if price is None else f"{price:,.{dp}f}"
    ch = "" if change_pct is None else \
        f"<span class='app-quote-ch app-t-{tone(change_pct)}'>{arrow(change_pct)} {pct(change_pct, 2, True)}</span>"
    out = [f"<div class='app-quote-name'>{_esc(name)} {chips(tags)}</div>",
           f"<div><span class='app-quote-px'>{px}</span>{ch}</div>"]
    if stats:
        out.append(f"<div class='app-sub'>{' · '.join(_esc(s) for s in stats)}</div>")
    if low is not None and high is not None and price is not None and high > low:
        p = max(0.0, min(100.0, (price - low) / (high - low) * 100))
        out.append(f"<div class='app-range' style='max-width:420px'><div class='track'></div>"
                   f"<div class='dot' style='left:{p:.1f}%;background:{BLUE}'></div></div>"
                   f"<div class='app-range-l' style='max-width:420px'><span>24h low {low:,.{dp}f}</span>"
                   f"<span>24h high {high:,.{dp}f}</span></div>")
    with st.container(border=True):
        html("".join(out))


def kpi_row(items: Sequence[dict]) -> None:
    """Bordered metric tiles. Item = {label, value, delta?, tone?, help?, spark?}. tone picks the delta colour:
    good=normal, critical=inverse, anything else=off. Deltas carry their own ▲/▼ and sign, so Streamlit's arrow is off
    and delta strings must not start with '-' (Streamlit would flip the colour)."""
    if not items:
        return
    # Streamlit truncates a metric's label and value with "..." when its column is narrow, so a long row is split into rows of
    # at most KPI_MAX_PER_ROW tiles of equal width (7 tiles -> 4 + 3, 5 -> 3 + 2). Rows of 4 or fewer are unchanged.
    for rows in kpi_layout(len(items)):
        cols = st.columns(rows[1])
        for col, it in zip(cols, items[rows[0]:rows[0] + rows[1]]):
            t = it.get("tone", "neutral")
            delta = it.get("delta")
            col.metric(it["label"], it.get("value", MISSING), delta=delta,
                       delta_color={"good": "normal", "critical": "inverse"}.get(t, "off"), delta_arrow="off",
                       help=it.get("help"), border=True, chart_data=it.get("spark"), chart_type="area")


KPI_MAX_PER_ROW = 4


def kpi_layout(n: int) -> list[tuple[int, int]]:
    """[(first_item_index, columns_in_this_row), ...]: `n` tiles in the fewest rows of at most KPI_MAX_PER_ROW, evenly sized."""
    if n <= 0:
        return []
    n_rows = -(-n // KPI_MAX_PER_ROW)
    per_row = -(-n // n_rows)
    return [(start, per_row) for start in range(0, n, per_row)]


def entity_card(title: str, tags: Iterable[tuple[str, str]] = (), key_value: str | None = None,
                cells: Sequence[tuple[str, str]] = (), sub: str | None = None,
                action: dict | None = None, body: Callable[[], None] | None = None) -> bool:
    """A bordered card for one entity: bold name + chips + key value right-aligned, then an auto-fit grid of
    label/value cells. `action` = {label, key, disabled?, type?} renders a button in a [6,1] right column; returns True
    when it was clicked. `body` renders extra content (e.g. a range bar) under the grid. Values are escaped unless
    they are Html (e.g. from value_html)."""
    clicked = False
    with st.container(border=True):
        main = st
        if action:
            main, side = st.columns([6, 1], vertical_alignment="center")
            clicked = side.button(action["label"], key=action["key"], disabled=action.get("disabled", False),
                                  type=action.get("type", "secondary"), width="stretch")
        with main.container():
            kv = f"<span class='app-card-key'>{_v(key_value)}</span>" if key_value is not None else ""
            grid = "".join(f"<div><div class='app-cell-l'>{_esc(k)}</div><div class='app-cell-v'>{_v(v)}</div></div>"
                           for k, v in cells)
            html(f"<div class='app-card-head'><span class='app-card-title'>{_esc(title)} {chips(tags)}</span>{kv}</div>"
                 + (f"<div class='app-sub'>{_esc(sub)}</div>" if sub else "")
                 + (f"<div class='app-grid'>{grid}</div>" if cells else ""))
            if body:
                body()
    return clicked


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Text columns: None/NaN -> '–' (numeric columns stay numeric so column_config formats apply)."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].map(lambda v: MISSING if v is None or (isinstance(v, float) and v != v) else v)
    return out


def table(df: pd.DataFrame | Styler | None, column_config: dict | None = None, height: int | str = "auto",
          empty: tuple[str, str] = ("Nothing yet", ""), **kw) -> None:
    """st.dataframe with hidden index and full width; an empty_state instead of a blank table."""
    if df is None or not len(df.data if isinstance(df, Styler) else df):
        empty_state(*empty)
        return
    data = df if isinstance(df, Styler) else _clean(df)
    st.dataframe(data, hide_index=True, width="stretch", column_config=column_config, height=height, **kw)


def highlight_rows(df: pd.DataFrame, mask: Sequence[bool]):
    """Styler that tints the rows where `mask` is True (e.g. the ATM strike in the chain ladder)."""
    bg = f"background-color: {rgba(BLUE, .16)}; font-weight: 600"
    flags = list(mask)
    return _clean(df).style.apply(lambda r: [bg if flags[r.name] else "" for _ in r], axis=1)


# ---- domain → tone mappings (display only) -------------------------------------------------------------------------
DECISION_TONE = {"TRIGGERED": "info", "WAITING_FOR_SETUP": "neutral", "NO_TRADE": "neutral", "ERROR": "critical"}
LEVEL_TONE = {"ERROR": "critical", "WARNING": "warning", "INFO": "info"}


def status_label(tone_: str, text_: str) -> str:
    """Plain-text status for tables (icon + word), e.g. '✖ AT LIMIT'."""
    return f"{tone_icon(tone_)} {text_}"


# ---- page header + status bar --------------------------------------------------------------------------------------
def _next_expiry(asset: str):
    from delta_intelligence.config.watchlist import SETTLE_HOUR_BY_ASSET

    now = pd.Timestamp(now_utc())
    hour = SETTLE_HOUR_BY_ASSET.get(asset, 12)
    exp = now.normalize() + pd.Timedelta(hours=hour)
    return exp if exp > now else exp + pd.Timedelta(days=1)


def _status_items() -> list[list[str]]:
    from delta_intelligence.brokers.rate_limit import LIMITER
    from delta_intelligence.execution.engine import kill_switch_on

    s = get_settings()
    now = now_utc()
    groups: list[list[str]] = []
    sessions = ", ".join(liquidity_sessions_at(now, parse_sessions(s.clocks.liquidity_sessions))) or "off-session"
    groups.append([f"<span class='app-clock'>{fmt_ist(now, '%d %b %H:%M:%S')} IST</span>",
                   chip(sessions, "neutral", "◷")])

    # engine (any process, via the DB heartbeat) + this app's engine details
    _, age = state.engine_heartbeat()
    eng = None if state.offline() else state.get_engine()
    ours = eng is not None and eng.is_running()
    eng_chips = []
    if age is not None and age < 120:
        detail = f" · {eng.cycles} cycles" if ours else " · other process"
        eng_chips.append(chip(f"engine running{detail} · {age:.0f}s", "good"))
    else:
        eng_chips.append(chip("engine stopped" + (f" · last {duration(age)} ago" if age is not None else ""),
                              "warning"))
    if ours:
        ws = getattr(getattr(eng, "feed", None), "status", "off")
        eng_chips.append(chip(f"ws {ws}", {"connected": "good", "stopped": "neutral", "off": "neutral"}.get(ws, "warning")))
    lim = LIMITER.snapshot(60)
    if lim["auth_block_remaining"] > 0:
        eng_chips.append(chip(f"API auth blocked · {lim['auth_block_remaining']:.0f}s", "critical"))
    elif lim["cooldown_remaining"] > 0:
        eng_chips.append(chip(f"API rate-limited · {lim['cooldown_remaining']:.0f}s", "warning"))
    elif lim["last_ok_age_sec"] is not None:
        eng_chips.append(chip(f"API ok · {duration(lim['last_ok_age_sec'])} ago", "good"))
    else:
        eng_chips.append(chip("API offline" if state.offline() else "API idle", "neutral"))
    groups.append(eng_chips)

    if s.is_live_mode:
        mode = chip(f"LIVE · {s.delta_env}", "critical" if s.delta_env == "PRODUCTION" else "warning")
    else:
        mode = chip("PAPER", "good")
    groups.append([mode, chip(f"data {s.data_env}", "neutral")])

    btc, xau = _next_expiry("BTC"), _next_expiry("XAUT")
    groups.append([chip(f"BTC/ETH expiry {fmt_ist(btc, '%H:%M')} · {duration(btc - pd.Timestamp(now))}", "neutral", "⏱"),
                   chip(f"XAUT expiry {fmt_ist(xau, '%H:%M')} · {duration(xau - pd.Timestamp(now))}", "neutral", "⏱"),
                   chip(f"funding {fmt_ist(next_funding_time(now), '%H:%M')}", "neutral", "⏱")])
    if kill_switch_on():
        groups.append([chip("KILL SWITCH ON", "critical")])
    return groups


@st.fragment(run_every="10s")
def status_bar() -> None:
    div = "<span class='app-div'></span>"
    html(f"<div class='app-bar'>{div.join(' '.join(g) for g in _status_items())}</div>")


def page_header(title: str, subtitle: str | None = None) -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    status_bar()


def page_setup(title: str, subtitle: str | None = None) -> None:
    """Call first on every page: theme, database, title, subtitle and the live status bar."""
    apply_theme(title)
    state.ensure_db()
    page_header(title, subtitle)


# ---- access control ----------------------------------------------------------------------------------------------
def password_configured() -> bool:
    return bool(get_settings().app_password)


def authenticated() -> bool:
    return bool(st.session_state.get("_authed"))


def open_access() -> bool:
    """No password configured AND not in LIVE mode: the dashboard runs with full controls and no login. This is for
    PAPER trading only. LIVE mode never runs open: it needs APP_PASSWORD."""
    return not password_configured() and not get_settings().is_live_mode


def can_control() -> bool:
    """Controls (kill switch, engine start/stop, orders, exits). With a password: a successful login. Without one:
    allowed in PAPER mode only (`open_access`), never in LIVE mode."""
    if password_configured():
        return authenticated()
    return open_access()


def gate() -> bool:
    """Optional password gate. With APP_PASSWORD set, every page needs a login. Without it, PAPER mode is open (full
    controls); LIVE mode without a password renders READ-ONLY."""
    if not password_configured():
        if get_settings().is_live_mode:
            st.sidebar.warning("LIVE mode needs APP_PASSWORD: with none set the dashboard is READ-ONLY (no engine "
                               "control, kill switch or orders).", icon=":material/lock:")
        return True
    if authenticated():
        if st.sidebar.button("Log out"):
            st.session_state["_authed"] = False
            st.rerun()
        return True
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.title("delta-intelligence")
        st.caption("Buy-only options intelligence for Delta Exchange India")
        with st.container(border=True):
            pw = st.text_input("Password", type="password")
            if st.button("Log in", type="primary", width="stretch"):
                if hmac.compare_digest(pw.encode(), get_settings().app_password.encode()):
                    st.session_state["_authed"] = True
                    st.rerun()
                else:
                    notice("critical", "Wrong password. Check APP_PASSWORD in .env / Streamlit secrets.")
    return False


def control_note() -> None:
    if not can_control():
        notice("info", "Controls are disabled: " + ("log in to use them." if password_configured()
                                                    else "LIVE mode needs APP_PASSWORD (PAPER mode does not)."))
