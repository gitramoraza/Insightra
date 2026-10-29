import base64
import json
import os
import re
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
from plotly.subplots import make_subplots

warnings.filterwarnings("ignore")

st.set_page_config(page_title="Analisa Saham", layout="wide")

# ---------------------------------------------------------------------------
# Pengaturan
# ---------------------------------------------------------------------------
LEBAR_LOGO = 300  # ubah untuk memperbesar/memperkecil logo (piksel)
LEBAR_PANEL = LEBAR_LOGO + 50  # panel kiri dilebarkan agar logo muat
REFRESH_DETIK = 60  # jeda auto-refresh sekaligus lama cache data harga
CACHE_BERITA = 300  # berita di-cache 5 menit
CACHE_INFO = 1800  # data fundamental di-cache 30 menit
CACHE_LAPORAN = 21600  # laporan keuangan, dividen: 6 jam
WIB = ZoneInfo("Asia/Jakarta")

# Interval untuk halaman Dashboard. Yahoo menolak period terlalu pendek untuk
# interval kecil; 5d aman. Interval 1m dibatasi Yahoo maksimal 7 hari.
PERIODE = {"1m": "5d", "5m": "5d", "15m": "5d"}

# Timeframe untuk halaman Teknikal: nama -> (periode, interval Yahoo)
TIMEFRAME = {
    "1m": ("5d", "1m"),
    "5m": ("5d", "5m"),
    "15m": ("5d", "15m"),
    "30m": ("1mo", "30m"),
    "1h": ("3mo", "1h"),
    "1D": ("1y", "1d"),
    "1W": ("5y", "1wk"),
    "1M": ("10y", "1mo"),
}
INTRADAY = {"1m", "5m", "15m", "30m", "1h"}

SIMBOL_IHSG = "^JKSE"
SIMBOL_USDIDR = "IDR=X"

# Daftar awal (bisa diubah di aplikasi). Bukan rekomendasi dan bukan daftar resmi
# indeks apa pun; kode yang tidak valid akan dilewati.
DAFTAR_WATCHLIST = "BBCA, BBRI, BMRI, TLKM, ASII, ANTM, ADRO, BRPT"
DAFTAR_SCREENER = (
    "BBCA, BBRI, BMRI, BBNI, TLKM, ASII, UNVR, ICBP, INDF, KLBF, ADRO, PTBA, "
    "ANTM, MDKA, GOTO, BRPT, AMRT, CPIN, SMGR, UNTR, ITMG, PGAS, EXCL"
)
MAKS_DAFTAR = 40

# RSS Kontan. Pola alamatnya: https://www.kontan.co.id/feed/news/<kanal>
# Kanal yang tidak bisa dibuka akan dilewati otomatis.
FEED_KONTAN = [
    "https://www.kontan.co.id/feed/news/investasi",
    "https://www.kontan.co.id/feed/news/keuangan",
]
BATAS_BERITA = 30

# Tools eksternal di halaman "Tools lain". {kode} diganti kode saham tanpa ".JK".
# Biarkan "" bila belum diisi.
TOOLS_EKSTERNAL = {
    "RTI Business": "https://www.rti.co.id/",
    "RTI Investor / Analytics": "https://investor4.rti.co.id/new_rti2_pay/?m_id=1&sub_m=s19",
    "Namora Hub": "https://namorahub.com/",
}

HALAMAN = [
    "Dashboard",
    "Teknikal",
    "Fundamental",
    "Watchlist",
    "Screener",
    "Portofolio",
    "Kalkulator",
    "TradingView",
    "Tools lain",
]

FOLDER = os.path.dirname(os.path.abspath(__file__))
NAN = float("nan")


# ---------------------------------------------------------------------------
# Tampilan: panel kiri + logo
# ---------------------------------------------------------------------------
def tampilkan_logo():
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
    logo_svg = os.path.join(FOLDER, "logo.svg")  # vektor: paling tajam
    logo_png = os.path.join(FOLDER, "logo.png")  # cadangan

    if os.path.exists(logo_svg):
        with open(logo_svg, "rb") as f:
            svg64 = base64.b64encode(f.read()).decode()
        st.sidebar.markdown(
            f'<img src="data:image/svg+xml;base64,{svg64}" width="{LEBAR_LOGO}">',
            unsafe_allow_html=True,
        )
    elif os.path.exists(logo_png):
        st.sidebar.image(logo_png, width=LEBAR_LOGO)
    else:
        st.sidebar.warning("logo.svg / logo.png tidak ditemukan di folder aplikasi.")


# ---------------------------------------------------------------------------
# Utilitas umum
# ---------------------------------------------------------------------------
def _angka(v):
    """Kembalikan float bila v angka valid, selain itu None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # f != f berarti NaN


def fmt_angka(v, desimal: int = 2, akhiran: str = "") -> str:
    f = _angka(v)
    return "-" if f is None else f"{f:,.{desimal}f}{akhiran}"


def fmt_persen(v) -> str:
    f = _angka(v)
    return "-" if f is None else f"{f * 100:,.1f}%"


def fmt_rupiah(v) -> str:
    f = _angka(v)
    return "-" if f is None else f"Rp {f:,.0f}"


def fmt_rupiah_besar(v) -> str:
    f = _angka(v)
    if f is None:
        return "-"
    if f >= 1e12:
        return f"Rp {f / 1e12:,.1f} T"
    if f >= 1e9:
        return f"Rp {f / 1e9:,.1f} M"
    return f"Rp {f:,.0f}"


def normalisasi_kode(teks: str) -> str:
    """'bbca' -> 'BBCA.JK'; kode yang sudah berakhiran (.JK) dibiarkan."""
    kode = re.sub(r"\s+", "", str(teks or "")).upper()
    if kode.startswith("IDX:"):
        kode = kode.split(":", 1)[1]
    if kode and "." not in kode and not kode.startswith("^") and "=" not in kode:
        kode += ".JK"
    return kode


def parse_daftar(teks: str, maks: int = MAKS_DAFTAR) -> list[str]:
    hasil = []
    for bagian in re.split(r"[,\s;]+", teks or ""):
        kode = normalisasi_kode(bagian)
        if kode and kode not in hasil:
            hasil.append(kode)
    return hasil[:maks]


def tanpa_zona(indeks):
    """Ubah indeks waktu ke WIB lalu buang zona, supaya Plotly memakai jam WIB apa adanya."""
    if getattr(indeks, "tz", None) is not None:
        return indeks.tz_convert(WIB).tz_localize(None)
    return indeks


# ---------------------------------------------------------------------------
# Data harga dan indikator
# ---------------------------------------------------------------------------
def _rapikan(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if "Close" in df:
        df = df.dropna(subset=["Close"])
    df.index.name = "Waktu"
    return df


@st.cache_data(ttl=REFRESH_DETIK, show_spinner=False)
def ambil_ohlc(kode: str, periode: str, interval: str) -> pd.DataFrame:
    """Data OHLCV dari Yahoo Finance (di-cache agar tidak terlalu sering memanggil)."""
    df = yf.download(
        kode, period=periode, interval=interval, auto_adjust=True, progress=False
    )
    return _rapikan(df)


def ambil_data(kode: str, interval: str) -> pd.DataFrame:
    return ambil_ohlc(kode, PERIODE[interval], interval)


def hitung_indikator(df: pd.DataFrame) -> pd.DataFrame:
    """Tambahkan MA, EMA, Bollinger, RSI(14), MACD(12,26,9), Stochastic(14,3), rata-rata volume."""
    d = df.copy()
    c = d["Close"]

    d["MA20"] = c.rolling(20).mean()
    d["MA50"] = c.rolling(50).mean()
    d["EMA20"] = c.ewm(span=20, adjust=False).mean()

    sd = c.rolling(20).std()
    d["BB_ATAS"] = d["MA20"] + 2 * sd
    d["BB_BAWAH"] = d["MA20"] - 2 * sd

    delta = c.diff()
    rata_naik = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    rata_turun = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["RSI"] = 100 - (100 / (1 + rata_naik / rata_turun))

    d["MACD"] = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    d["MACD_SINYAL"] = d["MACD"].ewm(span=9, adjust=False).mean()
    d["MACD_HIST"] = d["MACD"] - d["MACD_SINYAL"]

    rendah = d["Low"].rolling(14).min()
    tinggi = d["High"].rolling(14).max()
    d["STOCH_K"] = 100 * (c - rendah) / (tinggi - rendah)
    d["STOCH_D"] = d["STOCH_K"].rolling(3).mean()

    if "Volume" in d:
        d["VOL20"] = d["Volume"].rolling(20).mean()
    else:
        d["VOL20"] = NAN
    return d


@st.cache_data(ttl=REFRESH_DETIK, show_spinner=False)
def ambil_ringkasan(simbol: str):
    """Harga terakhir + perubahan dari penutupan hari sebelumnya.

    Mengembalikan (harga, selisih, persen, waktu_WIB) atau None bila data kosong.
    """
    df = yf.download(
        simbol, period="5d", interval="5m", auto_adjust=True, progress=False
    )
    df = _rapikan(df)
    if df.empty:
        return None

    tanggal = df.index.date
    hari_ini = df[tanggal == tanggal[-1]]
    sebelumnya = df[tanggal != tanggal[-1]]

    harga = float(hari_ini["Close"].iloc[-1])
    if not sebelumnya.empty:
        acuan = float(sebelumnya["Close"].iloc[-1])  # penutupan hari sebelumnya
    else:
        acuan = float(hari_ini["Open"].iloc[0])
    selisih = harga - acuan
    persen = selisih / acuan * 100 if acuan else 0.0

    # Semua jam ditampilkan dalam WIB (kurs dari Yahoo biasanya berjam UTC)
    waktu = df.index[-1]
    if waktu.tzinfo is None:
        waktu = waktu.tz_localize("UTC")
    return harga, selisih, persen, waktu.tz_convert(WIB)


@st.cache_data(ttl=CACHE_INFO, show_spinner=False)
def ambil_info(kode: str) -> dict:
    """Data profil + fundamental dari Yahoo Finance."""
    info = yf.Ticker(kode).info
    if not info:
        raise ValueError("info kosong")  # error tidak di-cache, jadi dicoba lagi
    return info


def muat_info(kode: str) -> dict:
    try:
        return ambil_info(kode)
    except Exception:
        return {}


def nama_perusahaan(kode: str, info: dict) -> str:
    return info.get("longName") or info.get("shortName") or kode


def ringkas_saham(kode: str):
    """Satu baris ringkasan teknikal harian untuk Watchlist dan Screener."""
    try:
        df = ambil_ohlc(kode, "6mo", "1d")
    except Exception:
        return None
    if df is None or len(df) < 2:
        return None

    d = hitung_indikator(df)
    r, p = d.iloc[-1], d.iloc[-2]
    ma20 = _angka(r["MA20"])
    vol20 = _angka(r["VOL20"])
    macd, sinyal = _angka(r["MACD"]), _angka(r["MACD_SINYAL"])
    return {
        "Kode": kode.replace(".JK", ""),
        "Harga": float(r["Close"]),
        "Ubah %": (float(r["Close"]) / float(p["Close"]) - 1) * 100,
        "Volume": _angka(r.get("Volume")),
        "Vol/rata2": (float(r["Volume"]) / vol20) if vol20 and _angka(r.get("Volume")) is not None else NAN,
        "RSI": _angka(r["RSI"]) if _angka(r["RSI"]) is not None else NAN,
        "vs MA20 %": (float(r["Close"]) / ma20 - 1) * 100 if ma20 else NAN,
        "MACD": ("Naik" if macd > sinyal else "Turun") if macd is not None and sinyal is not None else "-",
    }


# ---------------------------------------------------------------------------
# Berita / rumor Kontan (RSS)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_BERITA, show_spinner=False)
def ambil_berita():
    """Ambil berita terbaru dari RSS Kontan.

    Mengembalikan (daftar_berita, daftar_galat). Galat dicatat per alamat feed
    supaya mudah dilacak bila berita tidak muncul.
    """
    berita, terlihat, galat = [], set(), []
    for url in FEED_KONTAN:
        try:
            resp = requests.get(
                url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (aplikasi analisa saham)"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            galat.append(f"{url} -> {type(e).__name__}: {e}")
            continue  # kanal ini gagal: lanjut ke kanal berikutnya

        for item in root.iter("item"):
            judul = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not judul or not link or link in terlihat:
                continue
            terlihat.add(link)
            try:
                waktu = parsedate_to_datetime(item.findtext("pubDate")).astimezone(WIB)
            except Exception:
                waktu = None
            berita.append({"judul": judul, "link": link, "waktu": waktu})

    paling_lama = pd.Timestamp("1970-01-01", tz=WIB).to_pydatetime()
    berita.sort(key=lambda b: b["waktu"] or paling_lama, reverse=True)
    return berita[:BATAS_BERITA], galat


def kata_kunci(kode: str, nama: str) -> list[str]:
    """Kata kunci untuk menyaring berita: kode saham dan nama perusahaan."""
    kunci = [kode.split(".")[0].upper()]
    bersih = re.sub(r"\b(PT|Tbk\.?|Persero)\b", "", nama, flags=re.I).strip(" ,.")
    if bersih and bersih.upper() != kunci[0]:
        kunci.append(bersih.upper())
    return kunci


def tampilkan_rumor(kode: str, nama: str, hanya_terkait: bool):
    st.subheader("Rumor & Berita")
    st.caption("Sumber: Kontan")
    berita, galat = ambil_berita()

    if not berita:
        st.warning(
            "Berita Kontan belum bisa dimuat. Buka langsung di "
            "[kontan.co.id](https://www.kontan.co.id)."
        )
        if galat:
            with st.expander("Detail galat"):
                for g in galat:
                    st.code(g, language=None)
        return

    if hanya_terkait:
        kunci = kata_kunci(kode, nama)
        terkait = [b for b in berita if any(k in b["judul"].upper() for k in kunci)]
        if terkait:
            berita = terkait
        else:
            st.caption("Tidak ada berita terkait emiten ini, menampilkan berita terbaru.")

    with st.container(height=620):
        for b in berita:
            judul = b["judul"].replace("[", "(").replace("]", ")")
            st.markdown(f"**[{judul}]({b['link']})**")
            if b["waktu"]:
                st.caption(f"{b['waktu']:%d %b %Y %H:%M} WIB")


# ---------------------------------------------------------------------------
# Fundamental ringkas (Dashboard)
# ---------------------------------------------------------------------------
def tampilkan_fundamental(info: dict):
    st.markdown("#### Fundamental")
    if not info:
        st.caption("Data fundamental belum tersedia dari Yahoo Finance.")
        return

    harga = _angka(info.get("currentPrice") or info.get("regularMarketPrice"))
    dps = _angka(info.get("dividendRate"))
    yield_div = f"yield {dps / harga * 100:,.2f}%" if dps and harga else None

    # (label, nilai, keterangan kecil)
    baris1 = [
        ("Kapitalisasi pasar", fmt_rupiah_besar(info.get("marketCap")), None),
        ("PER", fmt_angka(info.get("trailingPE"), 1, "x"), None),
        ("PBV", fmt_angka(info.get("priceToBook"), 2, "x"), None),
        ("EPS (Rp)", fmt_angka(info.get("trailingEps"), 0), None),
    ]
    baris2 = [
        ("ROE", fmt_persen(info.get("returnOnEquity")), None),
        ("Margin laba", fmt_persen(info.get("profitMargins")), None),
        ("Dividen/saham (Rp)", fmt_angka(dps, 0), yield_div),
    ]
    for baris in (baris1, baris2):
        for kolom, (label, nilai, ket) in zip(st.columns(4), baris):
            kolom.metric(label, nilai, ket, delta_color="off")

    rendah, tinggi = info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
    bagian = []
    if info.get("sector"):
        bagian.append(f"Sektor: {info['sector']}")
    if info.get("industry"):
        bagian.append(f"Industri: {info['industry']}")
    if _angka(rendah) and _angka(tinggi):
        bagian.append(f"Rentang 52 minggu: {fmt_angka(rendah, 0)} - {fmt_angka(tinggi, 0)}")
    if bagian:
        st.caption(" | ".join(bagian))
    st.caption("Sumber: Yahoo Finance. Data bisa kosong atau terlambat untuk emiten Indonesia.")


# ---------------------------------------------------------------------------
# Chart sederhana (Dashboard, Altair)
# ---------------------------------------------------------------------------
def grafik_harga(hari: pd.DataFrame):
    data = (
        hari[["Close", "MA20"]]
        .reset_index()
        .melt("Waktu", var_name="Seri", value_name="Harga")
        .dropna()
    )
    grafik = alt.Chart(data).mark_line().encode(
        x="Waktu:T",
        y=alt.Y("Harga:Q", scale=alt.Scale(zero=False)),
        color="Seri:N",
    )
    st.altair_chart(grafik, use_container_width=True)


def grafik_rsi(hari: pd.DataFrame):
    rsi = hari[["RSI"]].reset_index().dropna()
    garis = alt.Chart(rsi).mark_line(color="#a78bfa").encode(
        x="Waktu:T",
        y=alt.Y("RSI:Q", scale=alt.Scale(domain=[0, 100])),
    )
    batas = (
        alt.Chart(pd.DataFrame({"y": [30, 70]}))
        .mark_rule(strokeDash=[4, 4], color="orange")
        .encode(y="y:Q")
    )
    st.altair_chart(garis + batas, use_container_width=True)


def tampilkan_chart(df: pd.DataFrame, hari: pd.DataFrame):
    st.markdown("#### Chart")
    grafik_harga(hari)
    grafik_rsi(hari)

    st.markdown("**Data terbaru**")
    st.dataframe(df.tail(10).round(2))


# ---------------------------------------------------------------------------
# Komponen Dashboard
# ---------------------------------------------------------------------------
def kartu_pasar(kolom, judul: str, simbol: str, awalan: str = ""):
    """Satu kartu ringkasan (IHSG atau kurs) di baris atas."""
    with kolom:
        try:
            hasil = ambil_ringkasan(simbol)
        except Exception:
            hasil = None
        if hasil is None:
            st.metric(judul, "-", "data tidak tersedia")
            return
        harga, selisih, persen, waktu = hasil
        st.metric(
            judul,
            f"{awalan}{harga:,.2f}",
            f"{selisih:+,.2f} ({persen:+.2f}%)",
        )
        st.caption(f"Per {waktu:%d %b %Y %H:%M} WIB")


def tampilkan_saham(kode: str, nama: str, info: dict, interval: str, tampilan: dict):
    st.subheader(f"{nama} ({kode})")

    # Harga terakhir selalu tampil sebagai kepala bagian saham
    df, hari = None, None
    try:
        mentah = ambil_data(kode, interval)
    except Exception as e:
        st.error(f"Gagal mengambil data dari Yahoo Finance: {e}")
        mentah = None

    if mentah is not None and mentah.empty:
        st.warning(
            "Data harga kosong. Periksa kode saham, atau Yahoo Finance sedang "
            "membatasi permintaan. Coba lagi beberapa menit lagi."
        )
    elif mentah is not None:
        # Indikator dihitung dari seluruh data agar MA20/RSI sudah terisi,
        # lalu grafik hanya menampilkan hari bursa terakhir.
        df = hitung_indikator(mentah)
        hari = df[df.index.date == df.index[-1].date()]

        terakhir = hari["Close"].iloc[-1]
        pembukaan = hari["Open"].iloc[0]
        st.metric(
            "Harga terakhir",
            f"{terakhir:,.0f}",
            f"{terakhir - pembukaan:,.0f} dari pembukaan",
        )
        st.caption(f"Data terakhir: {df.index[-1]:%d %b %Y %H:%M} WIB")

    if tampilan["fundamental"]:
        tampilkan_fundamental(info)
    if tampilan["chart"] and df is not None:
        tampilkan_chart(df, hari)


@st.fragment(run_every=REFRESH_DETIK)
def halaman_dashboard(kode: str, interval: str, hanya_terkait: bool, tampilan: dict):
    # Baris atas: IHSG dan kurs
    k1, k2, _ = st.columns([1, 1, 2])
    kartu_pasar(k1, "IHSG", SIMBOL_IHSG)
    kartu_pasar(k2, "USD/IDR", SIMBOL_USDIDR, awalan="Rp ")
    st.caption(f"Diperbarui: {datetime.now(WIB):%H:%M:%S} WIB")
    st.divider()

    info = muat_info(kode)
    nama = nama_perusahaan(kode, info)

    # Bagian Rumor di kolom kanan; bila disembunyikan, saham memakai lebar penuh
    if tampilan["rumor"]:
        kiri, kanan = st.columns([3, 1.4], gap="large")
        with kanan:
            tampilkan_rumor(kode, nama, hanya_terkait)
    else:
        kiri = st.container()

    with kiri:
        tampilkan_saham(kode, nama, info, interval, tampilan)


# ---------------------------------------------------------------------------
# Halaman Teknikal: candlestick, indikator, sinyal, support/resistance
# ---------------------------------------------------------------------------
WARNA_NAIK = "#1D9E75"
WARNA_TURUN = "#E24B4A"


def buat_figur(d: pd.DataFrame, aktif: list[str], interval: str) -> go.Figure:
    d = d.copy()
    d.index = tanpa_zona(d.index)

    panel = ["harga"]
    if "Volume" in aktif and "Volume" in d:
        panel.append("Volume")
    for nama in ("RSI", "MACD", "Stochastic"):
        if nama in aktif:
            panel.append(nama)

    lain = len(panel) - 1
    tinggi_baris = [1.0] if lain == 0 else [0.5] + [0.5 / lain] * lain
    fig = make_subplots(
        rows=len(panel), cols=1, shared_xaxes=True,
        vertical_spacing=0.03, row_heights=tinggi_baris,
    )

    fig.add_trace(
        go.Candlestick(
            x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
            name="Harga", increasing_line_color=WARNA_NAIK, decreasing_line_color=WARNA_TURUN,
        ),
        row=1, col=1,
    )
    for nama, warna in (("MA20", "#EF9F27"), ("MA50", "#378ADD"), ("EMA20", "#D4537E")):
        if nama in aktif:
            fig.add_trace(
                go.Scatter(x=d.index, y=d[nama], name=nama, mode="lines", line=dict(width=1.4, color=warna)),
                row=1, col=1,
            )
    if "Bollinger" in aktif:
        for nama in ("BB_ATAS", "BB_BAWAH"):
            fig.add_trace(
                go.Scatter(
                    x=d.index, y=d[nama], name=nama.replace("_", " ").title(),
                    mode="lines", line=dict(width=1, color="#888780", dash="dot"),
                ),
                row=1, col=1,
            )

    for baris, nama in enumerate(panel[1:], start=2):
        if nama == "Volume":
            warna = [WARNA_NAIK if c >= o else WARNA_TURUN for o, c in zip(d["Open"], d["Close"])]
            fig.add_trace(
                go.Bar(x=d.index, y=d["Volume"], name="Volume", marker_color=warna, opacity=0.6),
                row=baris, col=1,
            )
        elif nama == "RSI":
            fig.add_trace(
                go.Scatter(x=d.index, y=d["RSI"], name="RSI", mode="lines", line=dict(width=1.4, color="#a78bfa")),
                row=baris, col=1,
            )
            for y in (30, 70):
                fig.add_hline(y=y, line_dash="dot", line_color="#EF9F27", row=baris, col=1)
            fig.update_yaxes(range=[0, 100], row=baris, col=1)
        elif nama == "MACD":
            warna = [WARNA_NAIK if (_angka(h) or 0) >= 0 else WARNA_TURUN for h in d["MACD_HIST"]]
            fig.add_trace(
                go.Bar(x=d.index, y=d["MACD_HIST"], name="Histogram", marker_color=warna, opacity=0.6),
                row=baris, col=1,
            )
            fig.add_trace(
                go.Scatter(x=d.index, y=d["MACD"], name="MACD", mode="lines", line=dict(width=1.3, color="#378ADD")),
                row=baris, col=1,
            )
            fig.add_trace(
                go.Scatter(x=d.index, y=d["MACD_SINYAL"], name="Sinyal", mode="lines", line=dict(width=1.3, color="#EF9F27")),
                row=baris, col=1,
            )
        elif nama == "Stochastic":
            fig.add_trace(
                go.Scatter(x=d.index, y=d["STOCH_K"], name="%K", mode="lines", line=dict(width=1.3, color="#378ADD")),
                row=baris, col=1,
            )
            fig.add_trace(
                go.Scatter(x=d.index, y=d["STOCH_D"], name="%D", mode="lines", line=dict(width=1.3, color="#EF9F27")),
                row=baris, col=1,
            )
            for y in (20, 80):
                fig.add_hline(y=y, line_dash="dot", line_color="#888780", row=baris, col=1)
            fig.update_yaxes(range=[0, 100], row=baris, col=1)

    # Sembunyikan akhir pekan (dan jam di luar bursa untuk data intraday)
    istirahat = []
    if interval in ("1m", "5m", "15m", "30m", "1h", "1d"):
        istirahat.append(dict(bounds=["sat", "mon"]))
    if interval in ("1m", "5m", "15m", "30m", "1h"):
        istirahat.append(dict(bounds=[16, 9], pattern="hour"))
    if istirahat:
        fig.update_xaxes(rangebreaks=istirahat)

    fig.update_layout(
        template="plotly_dark",
        height=420 + 140 * lain,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        dragmode="pan",
    )
    return fig


def hitung_sinyal(d: pd.DataFrame) -> list[tuple[str, int]]:
    """Daftar (kalimat, nada): nada +1 positif, -1 negatif, 0 netral. Bukan rekomendasi."""
    hasil = []
    if len(d) < 2:
        return hasil
    r, p = d.iloc[-1], d.iloc[-2]
    c = _angka(r["Close"])

    ma20, ma50 = _angka(r["MA20"]), _angka(r["MA50"])
    if c is not None and ma20 is not None:
        hasil.append(
            ("Harga di atas MA20", 1) if c > ma20 else ("Harga di bawah MA20", -1)
        )
    if ma20 is not None and ma50 is not None:
        hasil.append(
            ("MA20 di atas MA50 (tren naik)", 1) if ma20 > ma50 else ("MA20 di bawah MA50 (tren turun)", -1)
        )

    rsi = _angka(r["RSI"])
    if rsi is not None:
        if rsi < 30:
            hasil.append((f"RSI {rsi:.0f}: jenuh jual", 1))
        elif rsi > 70:
            hasil.append((f"RSI {rsi:.0f}: jenuh beli", -1))
        else:
            hasil.append((f"RSI {rsi:.0f}: netral", 0))

    m, s = _angka(r["MACD"]), _angka(r["MACD_SINYAL"])
    mp, sp = _angka(p["MACD"]), _angka(p["MACD_SINYAL"])
    if None not in (m, s, mp, sp):
        if mp <= sp and m > s:
            hasil.append(("MACD baru memotong sinyal ke atas", 1))
        elif mp >= sp and m < s:
            hasil.append(("MACD baru memotong sinyal ke bawah", -1))
        elif m > s:
            hasil.append(("MACD di atas garis sinyal", 1))
        else:
            hasil.append(("MACD di bawah garis sinyal", -1))

    bu, bb = _angka(r["BB_ATAS"]), _angka(r["BB_BAWAH"])
    if c is not None and bu is not None and bb is not None:
        if c > bu:
            hasil.append(("Harga di atas pita atas Bollinger", -1))
        elif c < bb:
            hasil.append(("Harga di bawah pita bawah Bollinger", 1))
        else:
            hasil.append(("Harga di dalam pita Bollinger", 0))

    k = _angka(r["STOCH_K"])
    if k is not None:
        if k < 20:
            hasil.append((f"Stochastic {k:.0f}: jenuh jual", 1))
        elif k > 80:
            hasil.append((f"Stochastic {k:.0f}: jenuh beli", -1))

    vol, v20 = _angka(r.get("Volume")), _angka(r["VOL20"])
    if vol is not None and v20:
        if vol > 1.5 * v20:
            hasil.append((f"Volume {vol / v20:.1f}x rata-rata 20 bar", 0))
    return hasil


def tampilkan_sinyal(d: pd.DataFrame):
    st.markdown("#### Sinyal teknikal")
    sinyal = hitung_sinyal(d)
    if not sinyal:
        st.caption("Data belum cukup untuk menghitung sinyal.")
        return
    for teks, nada in sinyal:
        tanda = ":green[+]" if nada > 0 else (":red[-]" if nada < 0 else ":gray[=]")
        st.markdown(f"{tanda} {teks}")
    pos = sum(1 for _, n in sinyal if n > 0)
    neg = sum(1 for _, n in sinyal if n < 0)
    st.caption(
        f"{pos} sinyal positif, {neg} negatif. Ini hitungan indikator, bukan rekomendasi "
        "beli atau jual."
    )


def hitung_pivot(harian: pd.DataFrame):
    """Pivot klasik dari hari bursa terakhir yang sudah selesai."""
    if len(harian) < 2:
        return None
    hari_ini = datetime.now(WIB).date()
    terakhir = harian.index[-1]
    terakhir = terakhir.tz_convert(WIB) if getattr(terakhir, "tz", None) else terakhir
    k = harian.iloc[-1] if terakhir.date() < hari_ini else harian.iloc[-2]
    h, l, c = float(k["High"]), float(k["Low"]), float(k["Close"])
    p = (h + l + c) / 3
    return {
        "R2": p + (h - l),
        "R1": 2 * p - l,
        "Pivot": p,
        "S1": 2 * p - h,
        "S2": p - (h - l),
    }


def tampilkan_level(harian: pd.DataFrame):
    st.markdown("#### Support dan resistance")
    if harian is None or harian.empty:
        st.caption("Data harian belum tersedia.")
        return

    baris = []
    pivot = hitung_pivot(harian)
    if pivot:
        baris += [("Pivot" if nama == "Pivot" else f"{nama} (pivot)", harga) for nama, harga in pivot.items()]
    for hari in (20, 60):
        potong = harian.tail(hari)
        baris.append((f"Tertinggi {hari} hari", float(potong["High"].max())))
        baris.append((f"Terendah {hari} hari", float(potong["Low"].min())))
    tabel = pd.DataFrame(baris, columns=["Level", "Harga"]).sort_values("Harga", ascending=False)
    harga_kini = float(harian["Close"].iloc[-1])
    tabel["Jarak dari harga (%)"] = (tabel["Harga"] / harga_kini - 1) * 100
    st.dataframe(
        tabel,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Harga": st.column_config.NumberColumn(format="localized"),
            "Jarak dari harga (%)": st.column_config.NumberColumn(format="%+.1f"),
        },
    )
    st.caption("Pivot dihitung dari hari bursa terakhir yang sudah selesai.")


def halaman_teknikal(kode: str):
    k1, k2 = st.columns([1, 3])
    tf = k1.selectbox("Timeframe", list(TIMEFRAME), index=list(TIMEFRAME).index("1D"))
    aktif = k2.multiselect(
        "Indikator",
        ["Volume", "MA20", "MA50", "EMA20", "Bollinger", "RSI", "MACD", "Stochastic"],
        default=["Volume", "MA20", "MA50", "RSI"],
    )
    periode, interval = TIMEFRAME[tf]

    try:
        df = ambil_ohlc(kode, periode, interval)
    except Exception as e:
        st.error(f"Gagal mengambil data dari Yahoo Finance: {e}")
        return
    if df.empty:
        st.warning("Data kosong. Periksa kode saham atau coba timeframe lain.")
        return

    d = hitung_indikator(df)
    st.plotly_chart(buat_figur(d, aktif, interval))
    st.caption(
        f"{kode} | {tf} | data terakhir {tanpa_zona(d.index)[-1]:%d %b %Y %H:%M} WIB. "
        "Sumber: Yahoo Finance, bisa tertunda."
    )

    kiri, kanan = st.columns(2)
    with kiri:
        tampilkan_sinyal(d)
    with kanan:
        try:
            harian = ambil_ohlc(kode, "1y", "1d")
        except Exception:
            harian = None
        tampilkan_level(harian)


# ---------------------------------------------------------------------------
# Halaman Fundamental: dividen, laporan keuangan, pembanding
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_LAPORAN, show_spinner=False)
def ambil_aksi(kode: str):
    t = yf.Ticker(kode)
    return t.dividends, t.splits


@st.cache_data(ttl=CACHE_LAPORAN, show_spinner=False)
def ambil_laporan(kode: str) -> pd.DataFrame:
    laporan = yf.Ticker(kode).income_stmt
    if laporan is None or laporan.empty:
        raise ValueError("laporan kosong")
    return laporan


def tampilkan_dividen(kode: str):
    st.markdown("#### Dividen dan stock split")
    try:
        dividen, split = ambil_aksi(kode)
    except Exception:
        st.caption("Riwayat dividen belum bisa diambil dari Yahoo Finance.")
        return

    if dividen is None or len(dividen) == 0:
        st.caption("Tidak ada riwayat dividen di Yahoo Finance untuk emiten ini.")
    else:
        d = dividen.copy()
        d.index = tanpa_zona(d.index)
        per_tahun = d.groupby(d.index.year).sum().rename("Dividen per saham (Rp)")
        k1, k2 = st.columns([1, 1])
        with k1:
            st.markdown("**Total per tahun**")
            st.bar_chart(per_tahun)
        with k2:
            st.markdown("**Pembayaran terakhir**")
            terbaru = d.sort_index(ascending=False).head(8).to_frame("Dividen per saham (Rp)")
            terbaru.index = terbaru.index.strftime("%d %b %Y")
            terbaru.index.name = "Tanggal (ex-date menurut Yahoo)"
            st.dataframe(terbaru, use_container_width=True)

    if split is not None and len(split) > 0:
        s = split.copy()
        s.index = tanpa_zona(s.index).strftime("%d %b %Y")
        st.markdown("**Stock split**")
        st.dataframe(s.rename("Rasio"), use_container_width=True)


def tampilkan_laporan(kode: str):
    st.markdown("#### Pendapatan dan laba bersih")
    try:
        lap = ambil_laporan(kode)
    except Exception:
        st.caption("Laporan keuangan belum tersedia dari Yahoo Finance untuk emiten ini.")
        return

    baris = {}
    for nama, label in (("Total Revenue", "Pendapatan"), ("Net Income", "Laba bersih")):
        if nama in lap.index:
            baris[label] = lap.loc[nama].astype(float) / 1e12
    if not baris:
        st.caption("Baris pendapatan dan laba bersih tidak ditemukan.")
        return

    tabel = pd.DataFrame(baris).dropna(how="all").sort_index()
    tabel.index = [pd.Timestamp(i).year for i in tabel.index]
    st.bar_chart(tabel)
    st.caption("Dalam triliun rupiah, per tahun buku menurut Yahoo Finance. Cocokkan dengan laporan resmi emiten.")


def tampilkan_pembanding(kode: str):
    st.markdown("#### Pembanding emiten")
    teks = st.text_input(
        "Kode pembanding (pisahkan dengan koma)",
        "BBRI, BMRI, BBNI",
        help="Ganti dengan emiten sejenis di sektor yang sama.",
    )
    daftar = [kode] + [k for k in parse_daftar(teks, 8) if k != kode]

    baris = []
    for k in daftar:
        info = muat_info(k)
        if not info:
            continue
        harga = _angka(info.get("currentPrice") or info.get("regularMarketPrice"))
        dps = _angka(info.get("dividendRate"))
        roe = _angka(info.get("returnOnEquity"))
        margin = _angka(info.get("profitMargins"))
        mcap = _angka(info.get("marketCap"))
        baris.append({
            "Kode": k.replace(".JK", ""),
            "Nama": nama_perusahaan(k, info),
            "Kap. pasar (T)": mcap / 1e12 if mcap else NAN,
            "PER": _angka(info.get("trailingPE")) or NAN,
            "PBV": _angka(info.get("priceToBook")) or NAN,
            "ROE %": roe * 100 if roe is not None else NAN,
            "Margin %": margin * 100 if margin is not None else NAN,
            "Yield %": dps / harga * 100 if dps and harga else NAN,
        })
    if not baris:
        st.caption("Data pembanding belum tersedia dari Yahoo Finance.")
        return
    st.dataframe(
        pd.DataFrame(baris),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Kap. pasar (T)": st.column_config.NumberColumn(format="%.1f"),
            "PER": st.column_config.NumberColumn(format="%.1f"),
            "PBV": st.column_config.NumberColumn(format="%.2f"),
            "ROE %": st.column_config.NumberColumn(format="%.1f"),
            "Margin %": st.column_config.NumberColumn(format="%.1f"),
            "Yield %": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption("Sumber: Yahoo Finance. Angka kosong (-) berarti Yahoo tidak menyediakannya.")


def halaman_fundamental(kode: str):
    info = muat_info(kode)
    st.subheader(f"{nama_perusahaan(kode, info)} ({kode})")
    tampilkan_fundamental(info)
    st.divider()
    tampilkan_dividen(kode)
    st.divider()
    tampilkan_laporan(kode)
    st.divider()
    tampilkan_pembanding(kode)


# ---------------------------------------------------------------------------
# Halaman Watchlist
# ---------------------------------------------------------------------------
def warna_ubah(v):
    f = _angka(v)
    if f is None:
        return ""
    return f"color: {WARNA_NAIK}" if f > 0 else (f"color: {WARNA_TURUN}" if f < 0 else "")


def halaman_watchlist():
    teks = st.text_input("Daftar pantau (pisahkan dengan koma)", DAFTAR_WATCHLIST)
    daftar = parse_daftar(teks)
    st.caption(
        "Daftar ini hanya berlaku selama sesi dibuka. Salin teksnya untuk menyimpan. "
        "Harga adalah penutupan terakhir dari data harian Yahoo Finance."
    )
    if not daftar:
        st.info("Masukkan minimal satu kode saham.")
        return

    baris = []
    with st.spinner("Mengambil data..."):
        for kode in daftar:
            r = ringkas_saham(kode)
            if r:
                baris.append(r)
    if not baris:
        st.warning("Belum ada data yang bisa diambil. Periksa kode atau coba lagi nanti.")
        return

    df = pd.DataFrame(baris)
    tabel = df.style.map(warna_ubah, subset=["Ubah %", "vs MA20 %"]) if hasattr(df.style, "map") else df.style.applymap(warna_ubah, subset=["Ubah %", "vs MA20 %"])
    st.dataframe(
        tabel,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Harga": st.column_config.NumberColumn(format="localized"),
            "Ubah %": st.column_config.NumberColumn(format="%+.2f"),
            "Volume": st.column_config.NumberColumn(format="localized"),
            "Vol/rata2": st.column_config.NumberColumn(format="%.1fx"),
            "RSI": st.column_config.NumberColumn(format="%.0f"),
            "vs MA20 %": st.column_config.NumberColumn(format="%+.1f"),
        },
    )


# ---------------------------------------------------------------------------
# Halaman Screener
# ---------------------------------------------------------------------------
def kumpulkan_data_screener(daftar: list[str]) -> pd.DataFrame:
    baris = []
    bar = st.progress(0.0, text="Mengambil data...")
    for i, kode in enumerate(daftar):
        bar.progress((i + 1) / len(daftar), text=f"{kode} ({i + 1}/{len(daftar)})")
        r = ringkas_saham(kode)
        if not r:
            continue
        info = muat_info(kode)
        roe = _angka(info.get("returnOnEquity"))
        mcap = _angka(info.get("marketCap"))
        r.update({
            "Nama": nama_perusahaan(kode, info),
            "PER": _angka(info.get("trailingPE")) or NAN,
            "PBV": _angka(info.get("priceToBook")) or NAN,
            "ROE %": roe * 100 if roe is not None else NAN,
            "Kap. pasar (T)": mcap / 1e12 if mcap else NAN,
        })
        baris.append(r)
    bar.empty()
    return pd.DataFrame(baris)


def terapkan_filter(df, per_maks, pbv_maks, roe_min, rsi_min, rsi_maks, posisi_ma, vol_min):
    """Nilai 0 pada filter angka berarti filter itu tidak dipakai."""
    d = df.copy()
    if per_maks > 0:
        d = d[(d["PER"] > 0) & (d["PER"] <= per_maks)]
    if pbv_maks > 0:
        d = d[d["PBV"] <= pbv_maks]
    if roe_min > 0:
        d = d[d["ROE %"] >= roe_min]
    d = d[(d["RSI"] >= rsi_min) & (d["RSI"] <= rsi_maks)]
    if posisi_ma == "Di atas MA20":
        d = d[d["vs MA20 %"] > 0]
    elif posisi_ma == "Di bawah MA20":
        d = d[d["vs MA20 %"] < 0]
    if vol_min > 0:
        d = d[d["Vol/rata2"] >= vol_min]
    return d


def halaman_screener():
    teks = st.text_area("Daftar saham yang disaring", DAFTAR_SCREENER, height=90)
    daftar = parse_daftar(teks)
    st.caption(
        f"Maksimal {MAKS_DAFTAR} kode. Pengambilan pertama bisa memakan waktu karena "
        "tiap emiten diambil satu per satu dari Yahoo Finance; hasilnya disimpan sementara."
    )

    if st.button("Ambil data"):
        if not daftar:
            st.warning("Masukkan minimal satu kode saham.")
        else:
            st.session_state["scr_data"] = kumpulkan_data_screener(daftar)
            st.session_state["scr_waktu"] = datetime.now(WIB)

    data = st.session_state.get("scr_data")
    if data is None or data.empty:
        st.info("Tekan Ambil data, lalu atur filter di bawah.")
        return
    st.caption(f"Data diambil {st.session_state['scr_waktu']:%d %b %Y %H:%M:%S} WIB")

    st.markdown("#### Filter (isi 0 untuk tidak dipakai)")
    a, b, c, d_ = st.columns(4)
    per_maks = a.number_input("PER maksimal", min_value=0.0, value=0.0, step=1.0)
    pbv_maks = b.number_input("PBV maksimal", min_value=0.0, value=0.0, step=0.5)
    roe_min = c.number_input("ROE minimal (%)", min_value=0.0, value=0.0, step=1.0)
    vol_min = d_.number_input("Volume minimal (x rata-rata)", min_value=0.0, value=0.0, step=0.5)
    e, f = st.columns(2)
    rsi_min, rsi_maks = e.slider("Rentang RSI", 0, 100, (0, 100))
    posisi_ma = f.selectbox("Posisi harga terhadap MA20", ["Bebas", "Di atas MA20", "Di bawah MA20"])

    hasil = terapkan_filter(data, per_maks, pbv_maks, roe_min, rsi_min, rsi_maks, posisi_ma, vol_min)
    st.markdown(f"#### Hasil: {len(hasil)} dari {len(data)} saham")
    if hasil.empty:
        st.caption("Tidak ada saham yang memenuhi semua filter. Longgarkan salah satunya.")
        return

    kolom = ["Kode", "Nama", "Harga", "Ubah %", "PER", "PBV", "ROE %", "RSI", "vs MA20 %", "Vol/rata2", "MACD", "Kap. pasar (T)"]
    tampil = hasil[kolom].sort_values("Kap. pasar (T)", ascending=False)
    st.dataframe(
        tampil,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Harga": st.column_config.NumberColumn(format="localized"),
            "Ubah %": st.column_config.NumberColumn(format="%+.2f"),
            "PER": st.column_config.NumberColumn(format="%.1f"),
            "PBV": st.column_config.NumberColumn(format="%.2f"),
            "ROE %": st.column_config.NumberColumn(format="%.1f"),
            "RSI": st.column_config.NumberColumn(format="%.0f"),
            "vs MA20 %": st.column_config.NumberColumn(format="%+.1f"),
            "Vol/rata2": st.column_config.NumberColumn(format="%.1fx"),
            "Kap. pasar (T)": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    st.download_button(
        "Unduh hasil (CSV)",
        tampil.to_csv(index=False).encode("utf-8"),
        file_name="hasil_screener.csv",
        mime="text/csv",
    )
    st.caption("Hasil penyaringan angka, bukan rekomendasi. Periksa ulang datanya sebelum mengambil keputusan.")


# ---------------------------------------------------------------------------
# Halaman Portofolio
# ---------------------------------------------------------------------------
def hitung_portofolio(edit: pd.DataFrame) -> pd.DataFrame:
    baris = []
    for _, r in edit.iterrows():
        kode = normalisasi_kode(r.get("Kode"))
        lot, beli = _angka(r.get("Lot")), _angka(r.get("Harga beli"))
        if not kode or lot is None or beli is None or lot <= 0 or beli <= 0:
            continue
        try:
            df = ambil_ohlc(kode, "5d", "1d")
        except Exception:
            continue
        if df.empty:
            continue
        harga = float(df["Close"].iloc[-1])
        modal, nilai = lot * 100 * beli, lot * 100 * harga
        baris.append({
            "Kode": kode.replace(".JK", ""),
            "Lot": lot,
            "Harga beli": beli,
            "Harga": harga,
            "Modal": modal,
            "Nilai": nilai,
            "Untung/rugi": nilai - modal,
            "Untung %": (nilai / modal - 1) * 100,
            "Sektor": muat_info(kode).get("sector") or "-",
        })
    return pd.DataFrame(baris)


def halaman_portofolio():
    if "porto" not in st.session_state:
        st.session_state["porto"] = pd.DataFrame({"Kode": ["BBCA"], "Lot": [10], "Harga beli": [5000.0]})
        st.session_state["porto_versi"] = 0

    st.caption(
        "Isi posisi kamu di tabel (contoh baris bisa diganti atau dihapus). Data hanya hidup "
        "selama sesi dibuka dan tidak disimpan di server; gunakan Unduh dan Muat untuk menyimpannya "
        "di komputermu. 1 lot = 100 lembar."
    )

    berkas = st.file_uploader("Muat portofolio (CSV dengan kolom Kode, Lot, Harga beli)", type="csv")
    if berkas is not None and st.session_state.get("porto_berkas") != (berkas.name, berkas.size):
        try:
            muat = pd.read_csv(berkas)[["Kode", "Lot", "Harga beli"]]
            st.session_state["porto"] = muat
            st.session_state["porto_berkas"] = (berkas.name, berkas.size)
            st.session_state["porto_versi"] += 1
        except Exception:
            st.error("Berkas tidak sesuai. Pastikan ada kolom Kode, Lot, dan Harga beli.")

    edit = st.data_editor(
        st.session_state["porto"],
        num_rows="dynamic",
        key=f"porto_editor_{st.session_state['porto_versi']}",
        use_container_width=True,
        column_config={
            "Kode": st.column_config.TextColumn(help="Contoh: BBCA"),
            "Lot": st.column_config.NumberColumn(min_value=0, step=1),
            "Harga beli": st.column_config.NumberColumn(min_value=0.0, format="localized"),
        },
    )
    st.download_button(
        "Unduh portofolio (CSV)",
        edit.to_csv(index=False).encode("utf-8"),
        file_name="portofolio.csv",
        mime="text/csv",
    )

    with st.spinner("Menghitung..."):
        hasil = hitung_portofolio(edit)
    if hasil.empty:
        st.info("Isi minimal satu baris dengan kode, lot, dan harga beli yang valid.")
        return

    modal, nilai = hasil["Modal"].sum(), hasil["Nilai"].sum()
    untung = nilai - modal
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total modal", fmt_rupiah(modal))
    m2.metric("Nilai pasar", fmt_rupiah(nilai))
    m3.metric("Untung/rugi", fmt_rupiah(untung), f"{untung / modal * 100:+.2f}%")
    m4.metric("Jumlah emiten", f"{len(hasil)}")

    st.dataframe(
        hasil,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Lot": st.column_config.NumberColumn(format="localized"),
            "Harga beli": st.column_config.NumberColumn(format="localized"),
            "Harga": st.column_config.NumberColumn(format="localized"),
            "Modal": st.column_config.NumberColumn(format="localized"),
            "Nilai": st.column_config.NumberColumn(format="localized"),
            "Untung/rugi": st.column_config.NumberColumn(format="localized"),
            "Untung %": st.column_config.NumberColumn(format="%+.2f"),
        },
    )

    k1, k2 = st.columns(2)
    for kolom, judul, dasar in ((k1, "Alokasi per emiten", "Kode"), (k2, "Alokasi per sektor", "Sektor")):
        with kolom:
            st.markdown(f"**{judul}**")
            alokasi = hasil.groupby(dasar, as_index=False)["Nilai"].sum()
            grafik = alt.Chart(alokasi).mark_arc(innerRadius=50).encode(
                theta="Nilai:Q",
                color=alt.Color(f"{dasar}:N"),
                tooltip=[dasar, alt.Tooltip("Nilai:Q", format=",.0f")],
            )
            st.altair_chart(grafik, use_container_width=True)
    st.caption("Harga dari penutupan terakhir data harian Yahoo Finance, belum termasuk biaya transaksi.")


# ---------------------------------------------------------------------------
# Halaman Kalkulator
# ---------------------------------------------------------------------------
def kalkulator_posisi():
    st.markdown("#### Ukuran posisi berdasarkan risiko")
    a, b, c, d = st.columns(4)
    modal = a.number_input("Modal (Rp)", min_value=0.0, value=100_000_000.0, step=1_000_000.0, format="%.0f")
    risiko = b.number_input("Risiko per transaksi (%)", min_value=0.0, value=1.0, step=0.5)
    masuk = c.number_input("Harga masuk", min_value=0.0, value=5000.0, step=10.0, format="%.0f")
    stop = d.number_input("Stop loss", min_value=0.0, value=4850.0, step=10.0, format="%.0f")
    e, f, g = st.columns(3)
    target = e.number_input("Target harga (opsional)", min_value=0.0, value=5300.0, step=10.0, format="%.0f")
    fee_beli = f.number_input("Fee beli (%)", min_value=0.0, value=0.15, step=0.01, format="%.2f")
    fee_jual = g.number_input("Fee jual (%)", min_value=0.0, value=0.25, step=0.01, format="%.2f")
    st.caption("Fee bawaan hanya contoh. Isi sesuai tarif broker kamu, termasuk pajak penjualan.")

    selisih = masuk - stop
    if modal <= 0 or risiko <= 0 or masuk <= 0 or selisih <= 0:
        st.warning("Isi modal, risiko, dan harga masuk. Stop loss harus di bawah harga masuk.")
        return

    risiko_rp = modal * risiko / 100
    lot = int(risiko_rp // selisih) // 100
    lembar = lot * 100
    nilai = lembar * masuk
    risiko_nyata = lembar * selisih
    biaya_beli = nilai * fee_beli / 100

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Jumlah posisi", f"{lot:,} lot")
    m2.metric("Nilai posisi", fmt_rupiah(nilai), f"{nilai / modal * 100:.1f}% dari modal", delta_color="off")
    m3.metric("Risiko maksimal", fmt_rupiah(risiko_nyata), f"{risiko_nyata / modal * 100:.2f}% dari modal", delta_color="off")
    m4.metric("Biaya beli", fmt_rupiah(biaya_beli))

    if lot == 0:
        st.warning("Dengan risiko ini posisi kurang dari 1 lot. Naikkan risiko atau persempit stop loss.")
    if nilai > modal:
        st.warning("Nilai posisi melebihi modal. Kurangi ukuran atau persempit stop loss.")

    if target > masuk:
        untung_kotor = lembar * (target - masuk)
        biaya_jual = lembar * target * fee_jual / 100
        bersih = untung_kotor - biaya_beli - biaya_jual
        rr = (target - masuk) / selisih
        n1, n2, n3 = st.columns(3)
        n1.metric("Rasio untung : risiko", f"{rr:.2f} : 1")
        n2.metric("Untung kotor di target", fmt_rupiah(untung_kotor))
        n3.metric("Untung bersih setelah fee", fmt_rupiah(bersih))
    elif target > 0:
        st.caption("Target harus di atas harga masuk untuk menghitung rasio untung dan risiko.")


def kalkulator_rata_rata():
    st.markdown("#### Rata-rata harga beli")
    awal = pd.DataFrame({"Lot": [10, 5], "Harga": [5000.0, 4800.0]})
    edit = st.data_editor(
        awal,
        num_rows="dynamic",
        key="rata_rata_editor",
        use_container_width=True,
        column_config={
            "Lot": st.column_config.NumberColumn(min_value=0, step=1),
            "Harga": st.column_config.NumberColumn(min_value=0.0, format="localized"),
        },
    )
    bersih = edit.dropna()
    bersih = bersih[(bersih["Lot"] > 0) & (bersih["Harga"] > 0)]
    if bersih.empty:
        st.caption("Isi minimal satu pembelian.")
        return

    total_lot = bersih["Lot"].sum()
    total_modal = (bersih["Lot"] * 100 * bersih["Harga"]).sum()
    rata = total_modal / (total_lot * 100)

    a, b, c = st.columns(3)
    a.metric("Total lot", f"{total_lot:,.0f}")
    b.metric("Harga rata-rata", fmt_angka(rata, 1))
    c.metric("Total modal", fmt_rupiah(total_modal))

    st.markdown("**Skenario beli lagi**")
    x, y = st.columns(2)
    lot_baru = x.number_input("Tambah lot", min_value=0, value=5, step=1)
    harga_baru = y.number_input("Di harga", min_value=0.0, value=4700.0, step=10.0, format="%.0f")
    if lot_baru > 0 and harga_baru > 0:
        lot_gabung = total_lot + lot_baru
        rata_baru = (total_modal + lot_baru * 100 * harga_baru) / (lot_gabung * 100)
        st.metric("Rata-rata setelah beli lagi", fmt_angka(rata_baru, 1), f"{rata_baru - rata:+,.1f}", delta_color="off")


def kalkulator_lot_dan_impas():
    st.markdown("#### Konversi lot dan harga impas")
    a, b, c = st.columns(3)
    lot = a.number_input("Jumlah lot", min_value=0, value=10, step=1)
    harga = b.number_input("Harga per lembar", min_value=0.0, value=5000.0, step=10.0, format="%.0f")
    c.metric("Jumlah lembar", f"{lot * 100:,}")
    st.metric("Nilai transaksi", fmt_rupiah(lot * 100 * harga))

    d, e = st.columns(2)
    fee_b = d.number_input("Fee beli (%) ", min_value=0.0, value=0.15, step=0.01, format="%.2f")
    fee_j = e.number_input("Fee jual (%) ", min_value=0.0, value=0.25, step=0.01, format="%.2f")
    if harga > 0 and fee_j < 100:
        impas = harga * (1 + fee_b / 100) / (1 - fee_j / 100)
        st.metric("Harga impas (setelah fee beli dan jual)", fmt_angka(impas, 1), f"{(impas / harga - 1) * 100:+.2f}% dari harga beli", delta_color="off")


def kalkulator_dividen():
    st.markdown("#### Estimasi dividen")
    a, b, c = st.columns(3)
    lot = a.number_input("Jumlah lot ", min_value=0, value=10, step=1, key="dv_lot")
    dps = b.number_input("Dividen per lembar (Rp)", min_value=0.0, value=100.0, step=10.0, format="%.1f")
    pajak = c.number_input("Potongan pajak (%)", min_value=0.0, max_value=100.0, value=10.0, step=1.0)
    kotor = lot * 100 * dps
    st.metric("Dividen diterima (setelah pajak)", fmt_rupiah(kotor * (1 - pajak / 100)), f"kotor {fmt_rupiah(kotor)}", delta_color="off")
    st.caption("Tarif pajak bawaan hanya contoh; sesuaikan dengan ketentuan yang berlaku untukmu.")


def halaman_kalkulator():
    kalkulator_posisi()
    st.divider()
    kalkulator_rata_rata()
    st.divider()
    kalkulator_lot_dan_impas()
    st.divider()
    kalkulator_dividen()
    st.caption("Alat hitung bantu, bukan rekomendasi. Batas ARA dan ARB tidak disertakan karena aturannya bisa berubah; cek ketentuan BEI terbaru.")


# ---------------------------------------------------------------------------
# TradingView (widget embed)
# ---------------------------------------------------------------------------
INTERVAL_TV = {"1m": "1", "5m": "5", "15m": "15"}


def kode_tv(kode: str) -> str:
    """BBCA.JK -> IDX:BBCA (format simbol TradingView untuk bursa Indonesia)."""
    return "IDX:" + kode.split(".")[0]


def widget_tv(skrip: str, konfigurasi: dict, tinggi: int):
    """Tampilkan satu widget TradingView lewat skrip embed resminya."""
    html = f"""
    <div class="tradingview-widget-container" style="height:{tinggi}px;width:100%">
      <div class="tradingview-widget-container__widget" style="height:{tinggi}px;width:100%"></div>
      <script type="text/javascript"
              src="https://s3.tradingview.com/external-embedding/{skrip}" async>
      {json.dumps(konfigurasi)}
      </script>
    </div>
    """
    components.html(html, height=tinggi)


def halaman_tradingview(kode: str, interval: str):
    simbol = kode_tv(kode)
    st.caption(
        f"Simbol TradingView: {simbol}. Data di widget gratis bisa tertunda dan "
        "mungkin tidak sama persis dengan Yahoo Finance."
    )
    widget_tv(
        "embed-widget-advanced-chart.js",
        {
            "autosize": True,
            "symbol": simbol,
            "interval": INTERVAL_TV[interval],
            "timezone": "Asia/Jakarta",
            "theme": "dark",
            "style": "1",  # 1 = candlestick
            "locale": "id",
            "allow_symbol_change": True,
            "support_host": "https://www.tradingview.com",
        },
        650,
    )
    st.markdown("#### Ringkasan teknikal")
    widget_tv(
        "embed-widget-technical-analysis.js",
        {
            "interval": interval,
            "width": "100%",
            "height": 450,
            "isTransparent": True,
            "symbol": simbol,
            "showIntervalTabs": True,
            "displayMode": "single",
            "locale": "id",
            "colorTheme": "dark",
        },
        450,
    )


# ---------------------------------------------------------------------------
# Tools eksternal (RTI, Namora Hub, dst.)
# ---------------------------------------------------------------------------
def halaman_tools(kode: str):
    pendek = kode.split(".")[0]
    for nama, templat in TOOLS_EKSTERNAL.items():
        st.markdown(f"#### {nama}")
        if not templat.strip():
            st.info(f"Alamat {nama} belum diisi. Isi di TOOLS_EKSTERNAL pada app.py.")
            continue

        url = templat.replace("{kode}", pendek)
        st.link_button(f"Buka {nama}", url)
        if st.checkbox(f"Coba tampilkan {nama} di sini", key=f"embed_{nama}"):
            st.caption(
                "Banyak situs memblokir tampilan di dalam aplikasi lain. "
                "Bila kosong, pakai tombol Buka di atas."
            )
            components.iframe(url, height=700, scrolling=True)


# ---------------------------------------------------------------------------
# Halaman utama
# ---------------------------------------------------------------------------
def main():
    tampilkan_logo()
    st.title("Analisa Saham (Auto-refresh)")

    kode = normalisasi_kode(st.sidebar.text_input("Kode saham", "BBCA.JK"))
    interval = st.sidebar.selectbox("Interval", list(PERIODE), index=1)

    # Pilihan bagian yang ditampilkan / disembunyikan (halaman Dashboard)
    st.sidebar.markdown("**Tampilkan di Dashboard**")
    tampilan = {
        "fundamental": st.sidebar.toggle("Fundamental", value=True),
        "chart": st.sidebar.toggle("Chart", value=True),
        "rumor": st.sidebar.toggle("Rumor", value=True),
    }
    hanya_terkait = st.sidebar.checkbox("Berita khusus emiten ini", value=False)

    # Hanya halaman yang dipilih yang dijalankan, supaya Yahoo tidak dipanggil sia-sia
    halaman = st.radio("Halaman", HALAMAN, horizontal=True, label_visibility="collapsed")

    if not kode and halaman not in ("Watchlist", "Screener", "Portofolio", "Kalkulator"):
        st.info("Masukkan kode saham di panel kiri.")
        return

    if halaman == "Dashboard":
        halaman_dashboard(kode, interval, hanya_terkait, tampilan)
    elif halaman == "Teknikal":
        halaman_teknikal(kode)
    elif halaman == "Fundamental":
        halaman_fundamental(kode)
    elif halaman == "Watchlist":
        halaman_watchlist()
    elif halaman == "Screener":
        halaman_screener()
    elif halaman == "Portofolio":
        halaman_portofolio()
    elif halaman == "Kalkulator":
        halaman_kalkulator()
    elif halaman == "TradingView":
        halaman_tradingview(kode, interval)
    elif halaman == "Tools lain":
        halaman_tools(kode)

    st.caption("Data dari sumber pihak ketiga dan bisa tertunda atau tidak lengkap. Bukan rekomendasi investasi.")


main()