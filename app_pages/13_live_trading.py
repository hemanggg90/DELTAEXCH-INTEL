"""Live Trading: disabled until P7 and the double gate. Shows exactly what is required."""
from __future__ import annotations

import streamlit as st

from delta_intelligence.config.settings import LIVE_CONFIRM_PHRASE, get_settings
from delta_intelligence.ui.components import page_setup

page_setup("Live Trading")
s = get_settings()
st.error("Live trading is NOT available yet (phase P7: testnet first). Nothing on this page can place a real order.")
st.markdown(f"""
**Live trading will require ALL of:**
1. `TRADING_MODE=LIVE` (now: `{s.trading_mode}`)
2. `TRADING_LIVE_CONFIRM={LIVE_CONFIRM_PHRASE}` (now: {'set' if s.trading_live_confirm == LIVE_CONFIRM_PHRASE else 'not set'})
3. `DELTA_ENV` set deliberately (now: `{s.delta_env}`; live code defaults to TESTNET)
4. API key + secret for that environment (now: {'set' if s.has_credentials else 'not set'})
5. A passing startup check (time sync, auth, **IP whitelist**), as in `scripts/check_delta.py`
6. A **static public IP** host: Delta only accepts Trading keys from whitelisted IPs. Streamlit Community Cloud cannot
   provide one, so live needs a VPS running `streamlit run` under systemd or supervisor.

**Safety, regardless of mode:** buying options only (sell-to-open is rejected by the structures, the broker order guard
and the risk engine); sells are reduce-only closes; orders are never auto-retried; there is no withdrawal function.
""")
