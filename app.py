import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

# INSIGHTRA — Streamlit prototype. Prices from Yahoo Finance may be delayed/incomplete.
st.set_page_config(page_title="INSIGHTRA | Indonesia Equity Terminal", page_icon="📈", layout="wide", initial_sidebar_state="expanded")
WIB = ZoneInfo("Asia/Jakarta")
REFRESH_SECONDS = 60
CACHE_NEWS_SECONDS = 300
CACHE_INFO_SECONDS = 1800
PERIODS = {
    "1m": ("5d", "1"), "5m": ("5d", "5"), "15m": ("5d", "15"),
    "30m": ("1mo", "30"), "1h": ("3mo", "60"),
    "1D": ("1y", "D"), "1W": ("5y", "W"), "1M": ("10y", "M"),
}
INDICES = {"IHSG": "^JKSE", "LQ45": "^JKLQ45", "USD/IDR": "IDR=X"}
WATCHLIST_DEFAULT = ["BBCA", "BBRI", "BMRI", "TLKM", "ASII", "BRPT", "ANTM", "ADRO"]

# Official/product entry points. Some features require the user's own account/subscription.
TOOL_LINKS = {
    "TradingView": "https://www.tradingview.com/",
    "RTI Business": "https://www.rti.co.id/",
    "RTI Investor / Analytics": "https://investor4.rti.co.id/new_rti2_pay/?m_id=1&sub_m=s19",
    "Namora Hub": "https://namorahub.com/",
}

st.markdown("""
<style>
:root { color-scheme: dark; }
.stApp { background: #07111d; color: #e6edf7; }
[data-testid="stHeader"] { background: rgba(7,17,29,.92); }
section[data-testid="stSidebar"] { background: #0a1726; border-right: 1px solid #1e3348; }
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1800px; }
.ins-brand {font-size: 1.65rem; font-weight: 800; letter-spacing: .06em; color: #eef5ff;}
.ins-brand span {color:#2e91ff;}
.ins-sub {font-size:.68rem; letter-spacing:.16em; color:#8ba2bc; margin-top:-.2rem;}
[data-testid="stMetric"] { background: linear-gradient(145deg,#102236,#0b1928); border:1px solid #20384f; padding:12px 14px; border-radius:10px; }
[data-testid="stMetricLabel"] { color:#9db1c8; }
[data-testid="stMetricValue"] { color:#f2f7ff; }
[data-testid="stMetricDelta"] svg { display:none; }
.stTabs [data-baseweb="tab-list"] { gap: 5px; }
.stTabs [data-baseweb="tab"] { background:#0e2032; border-radius:7px 7px 0 0; padding:8px 14px; }
.stTabs [aria-selected="true"] { background:#123b70 !important; }
div[data-testid="stDataFrame"] { border:1px solid #20384f; border-radius:8px; }
.ins-note {background:#102337;border:1px solid #24445f;padding:9px 12px;border-radius:8px;color:#a9bfd7;font-size:.82rem;}
.ins-positive {color:#00d7a0;font-weight:700;} .ins-negative {color:#ff647c;font-weight:700;}
a { color:#64adff !important; }
</style>
""", unsafe_allow_html=True)


def fmt_num(value, decimals=2):
    try:
        v = float(value)
        if pd.isna(v):
            return "—"
        return f"{v:,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def fmt_big(value):
    try:
        v = float(value)
        if pd.isna(v): return "—"
        if abs(v) >= 1e12: return f"Rp {v/1e12:,.2f} T"
        if abs(v) >= 1e9: return f"Rp {v/1e9:,.2f} M"
        return f"Rp {v:,.0f}"
    except (TypeError, ValueError):
        return "—"


def normalize_symbol(raw):
    code = re.sub(r"\s+", "", (raw or "").upper())
    if not code:
        return "BBCA.JK"
    if code.startswith("IDX:"):
        code = code.split(":", 1)[1]
    if "." not in code and code not in {"^JKSE", "IDR=X", "GC=F", "CL=F"}:
        code += ".JK"
    return code


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def download_prices(symbol, period, interval):
    df = yf.download(symbol, period=period, interval=interval, auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return pd.DataFrame()