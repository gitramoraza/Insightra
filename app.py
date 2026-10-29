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
import requests
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

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
WIB = ZoneInfo("Asia/Jakarta")

# Yahoo menolak period terlalu pendek untuk interval kecil; 5d aman untuk semua.
# Catatan: interval 1m dibatasi Yahoo maksimal 7 hari.
PERIODE = {"1m": "5d", "5m": "5d", "15m": "5d"}

SIMBOL_IHSG = "^JKSE"
SIMBOL_USDIDR = "IDR=X"

# RSS Kontan. Pola alamatnya: https://www.kontan.co.id/feed/news/<kanal>
# Kanal yang tidak bisa dibuka akan dilewati otomatis.
FEED_KONTAN = [
    "https://www.kontan.co.id/feed/news/investasi",
    "https://www.kontan.co.id/feed/news/keuangan",
]
BATAS_BERITA = 30

# Tools eksternal di tab "Tools lain". Isi dengan alamat lengkap; tulisan {kode}
# akan diganti kode saham tanpa ".JK" (contoh: "https://contoh.com/saham/{kode}").
# Biarkan "" bila belum diisi.
TOOLS_EKSTERNAL = {
    "RTI Business": "",
    "Namora Hub": "",
}

FOLDER = os.path.dirname(os.path.abspath(__file__))


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
# Format angka
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


def fmt_rupiah_besar(v) -> str:
    f = _angka(v)
    if f is None:
        return "-"
    if f >= 1e12:
        return f"Rp {f / 1e12:,.1f} T"
    if f >= 1e9:
        return f"Rp {f / 1e9:,.1f} M"
    return f"Rp {f:,.0f}"


# ---------------------------------------------------------------------------
# Data harga
# ---------------------------------------------------------------------------
@st.cache_data(ttl=REFRESH_DETIK, show_spinner=False)
def ambil_data(kode: str, interval: str) -> pd.DataFrame:
    """Ambil data dari Yahoo Finance (di-cache agar tidak terlalu sering memanggil)."""
    df = yf.download(
        kode,
        period=PERIODE[interval],
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index.name = "Waktu"
    return df


def hitung_indikator(df: pd.DataFrame) -> pd.DataFrame:
    """Tambahkan MA20 dan RSI(14)."""
    df = df.copy()
    df["MA20"] = df["Close"].rolling(20).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["RSI"] = 100 - (100 / (1 + gain / loss))
    return df


@st.cache_data(ttl=REFRESH_DETIK, show_spinner=False)
def ambil_ringkasan(simbol: str):
    """Harga terakhir + perubahan dari penutupan hari sebelumnya.

    Mengembalikan (harga, selisih, persen, waktu_WIB) atau None bila data kosong.
    """
    df = yf.download(
        simbol, period="5d", interval="5m", auto_adjust=True, progress=False
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
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
# Fundamental
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
# Chart
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
# Komponen halaman
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


def tampilkan_tradingview(kode: str, interval: str):
    simbol = kode_tv(kode)
    st.caption(
        f"Simbol TradingView: {simbol}. Data di widget gratis bisa tertunda dan "
        "mungkin tidak sama persis dengan Yahoo Finance."
    )
    # Widget ini punya pembaruan sendiri, jadi sengaja berada di luar auto-refresh
    # supaya zoom dan gambar di chart tidak ter-reset tiap menit.
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
def tampilkan_tools(kode: str):
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
@st.fragment(run_every=REFRESH_DETIK)
def tampil(kode: str, interval: str, hanya_terkait: bool, tampilan: dict):
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


def main():
    tampilkan_logo()
    st.title("Analisa Saham (Auto-refresh)")

    kode = st.sidebar.text_input("Kode saham", "BBCA.JK").strip().upper()
    if kode and "." not in kode:
        kode += ".JK"  # otomatis tambahkan akhiran bursa Indonesia
    interval = st.sidebar.selectbox("Interval", list(PERIODE), index=1)

    # Pilihan bagian yang ditampilkan / disembunyikan
    st.sidebar.markdown("**Tampilkan di Dashboard**")
    tampilan = {
        "fundamental": st.sidebar.toggle("Fundamental", value=True),
        "chart": st.sidebar.toggle("Chart", value=True),
        "rumor": st.sidebar.toggle("Rumor", value=True),
    }
    hanya_terkait = st.sidebar.checkbox("Berita khusus emiten ini", value=False)

    if not kode:
        st.info("Masukkan kode saham di panel kiri.")
        return

    tab_dash, tab_tv, tab_tools = st.tabs(["Dashboard", "TradingView", "Tools lain"])
    with tab_dash:
        tampil(kode, interval, hanya_terkait, tampilan)
    with tab_tv:
        tampilkan_tradingview(kode, interval)
    with tab_tools:
        tampilkan_tools(kode)


main()