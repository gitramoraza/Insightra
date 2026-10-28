import base64
import os
import warnings

import altair as alt
import pandas as pd
import streamlit as st
import yfinance as yf

warnings.filterwarnings("ignore")

st.set_page_config(page_title="Analisa Saham", layout="wide")

# Logo di pojok kiri atas (dicari di folder yang sama dengan app.py)
LEBAR_LOGO = 300  # ubah angka ini untuk memperbesar/memperkecil logo (piksel)
LEBAR_PANEL = LEBAR_LOGO + 50  # panel kiri dilebarkan agar logo muat
st.markdown(
    f"""
    <style>
    section[data-testid="stSidebar"][aria-expanded="true"] {{
        width: {LEBAR_PANEL}px !important;
        min-width: {LEBAR_PANEL}px !important;
        max-width: {LEBAR_PANEL}px !important;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)
FOLDER = os.path.dirname(os.path.abspath(__file__))
LOGO_SVG = os.path.join(FOLDER, "logo.svg")  # vektor: paling tajam di ukuran apa pun
LOGO_PNG = os.path.join(FOLDER, "logo.png")  # cadangan kalau logo.svg tidak ada
if os.path.exists(LOGO_SVG):
    with open(LOGO_SVG, "rb") as f:
        svg64 = base64.b64encode(f.read()).decode()
    st.sidebar.markdown(
        f'<img src="data:image/svg+xml;base64,{svg64}" width="{LEBAR_LOGO}">',
        unsafe_allow_html=True,
    )
elif os.path.exists(LOGO_PNG):
    st.sidebar.image(LOGO_PNG, width=LEBAR_LOGO)
else:
    st.sidebar.warning("logo.svg / logo.png tidak ditemukan di folder app-saham.")

st.title("Analisa Saham (Auto-refresh)")

kode = st.sidebar.text_input("Kode saham", "BBCA.JK")
interval = st.sidebar.selectbox("Interval", ["1m", "5m", "15m"], index=1)


@st.fragment(run_every=60)  # perbarui tiap 60 detik
def tampil():
    df = yf.download(kode, period="1d", interval=interval, auto_adjust=True)
    if df.empty:
        st.warning("Data kosong. Pasar mungkin tutup atau kode salah.")
        return
    df.columns = df.columns.get_level_values(0)
    df.index.name = "Waktu"

    # Indikator
    df["MA20"] = df["Close"].rolling(20).mean()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["RSI"] = 100 - (100 / (1 + gain / loss))

    # Ringkasan harga
    terakhir = df["Close"].iloc[-1]
    awal = df["Close"].iloc[0]
    st.metric("Harga terakhir", f"{terakhir:,.0f}", f"{terakhir - awal:,.0f}")
    st.caption(f"Data terakhir: {df.index[-1]}")

    # Grafik harga + MA20
    data = (
        df[["Close", "MA20"]]
        .reset_index()
        .melt("Waktu", var_name="Seri", value_name="Harga")
        .dropna()
    )
    grafik = alt.Chart(data).mark_line().encode(
        x="Waktu:T",
        y=alt.Y("Harga:Q", scale=alt.Scale(zero=False)),
        color="Seri:N",
    )
    st.altair_chart(grafik)

    # Grafik RSI (sumbu 0-100 + garis batas 30 dan 70)
    rsi = df[["RSI"]].reset_index().dropna()
    garis_rsi = alt.Chart(rsi).mark_line(color="#a78bfa").encode(
        x="Waktu:T",
        y=alt.Y("RSI:Q", scale=alt.Scale(domain=[0, 100])),
    )
    batas = alt.Chart(pd.DataFrame({"y": [30, 70]})).mark_rule(
        strokeDash=[4, 4], color="orange"
    ).encode(y="y:Q")
    st.altair_chart(garis_rsi + batas)

    # Tabel data terbaru
    st.dataframe(df.tail(10).round(2))


tampil()
