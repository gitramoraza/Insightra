import base64
import os
import re
import warnings
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import requests
import streamlit as st
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

    Mengembalikan (harga, selisih, persen, waktu) atau None bila data kosong.
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
    return harga, selisih, persen, df.index[-1]


@st.cache_data(ttl=86400, show_spinner=False)
def ambil_nama_perusahaan(kode: str) -> str:
    """Nama perusahaan dari Yahoo Finance; bila gagal, pakai kode saham."""
    try:
        info = yf.Ticker(kode).info
        return info.get("longName") or info.get("shortName") or kode
    except Exception:
        return kode


# ---------------------------------------------------------------------------
# Berita Kontan (RSS)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_BERITA, show_spinner=False)
def ambil_berita() -> list[dict]:
    """Ambil berita terbaru dari RSS Kontan, gabungkan, urutkan dari yang terbaru."""
    berita, terlihat = [], set()
    for url in FEED_KONTAN:
        try:
            resp = requests.get(
                url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (aplikasi analisa saham)"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception:
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
    return berita[:BATAS_BERITA]


def kata_kunci(kode: str, nama: str) -> list[str]:
    """Kata kunci untuk menyaring berita: kode saham dan nama perusahaan."""
    kunci = [kode.split(".")[0].upper()]
    bersih = re.sub(r"\b(PT|Tbk\.?|Persero)\b", "", nama, flags=re.I).strip(" ,.")
    if bersih and bersih.upper() != kunci[0]:
        kunci.append(bersih.upper())
    return kunci


def tampilkan_berita(kode: str, nama: str, hanya_terkait: bool):
    st.subheader("Berita Kontan")
    berita = ambil_berita()

    if not berita:
        st.warning(
            "Berita Kontan belum bisa dimuat. Buka langsung di "
            "[kontan.co.id](https://www.kontan.co.id)."
        )
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
# Grafik
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
        st.caption(f"Per {waktu:%d %b %Y %H:%M}")


def tampilkan_saham(kode: str, nama: str, interval: str):
    st.subheader(f"{nama} ({kode})")

    try:
        df = ambil_data(kode, interval)
    except Exception as e:
        st.error(f"Gagal mengambil data dari Yahoo Finance: {e}")
        return

    if df.empty:
        st.warning(
            "Data kosong. Periksa kode saham, atau Yahoo Finance sedang membatasi "
            "permintaan. Coba lagi beberapa menit lagi."
        )
        return

    # Indikator dihitung dari seluruh data agar MA20/RSI sudah terisi,
    # lalu grafik hanya menampilkan hari bursa terakhir.
    df = hitung_indikator(df)
    hari = df[df.index.date == df.index[-1].date()]

    terakhir = hari["Close"].iloc[-1]
    pembukaan = hari["Open"].iloc[0]
    st.metric(
        "Harga terakhir",
        f"{terakhir:,.0f}",
        f"{terakhir - pembukaan:,.0f} dari pembukaan",
    )
    st.caption(f"Data terakhir: {df.index[-1]:%d %b %Y %H:%M}")

    grafik_harga(hari)
    grafik_rsi(hari)

    st.markdown("**Data terbaru**")
    st.dataframe(df.tail(10).round(2))


# ---------------------------------------------------------------------------
# Halaman utama
# ---------------------------------------------------------------------------
@st.fragment(run_every=REFRESH_DETIK)
def tampil(kode: str, interval: str, hanya_terkait: bool):
    # Baris atas: IHSG dan kurs
    k1, k2, _ = st.columns([1, 1, 2])
    kartu_pasar(k1, "IHSG", SIMBOL_IHSG)
    kartu_pasar(k2, "USD/IDR", SIMBOL_USDIDR, awalan="Rp ")
    st.divider()

    nama = ambil_nama_perusahaan(kode)
    kiri, kanan = st.columns([3, 1.4], gap="large")
    with kiri:
        tampilkan_saham(kode, nama, interval)
    with kanan:
        tampilkan_berita(kode, nama, hanya_terkait)


def main():
    tampilkan_logo()
    st.title("Analisa Saham (Auto-refresh)")

    kode = st.sidebar.text_input("Kode saham", "BBCA.JK").strip().upper()
    if kode and "." not in kode:
        kode += ".JK"  # otomatis tambahkan akhiran bursa Indonesia
    interval = st.sidebar.selectbox("Interval", list(PERIODE), index=1)
    hanya_terkait = st.sidebar.checkbox("Berita khusus emiten ini", value=False)

    if not kode:
        st.info("Masukkan kode saham di panel kiri.")
        return
    tampil(kode, interval, hanya_terkait)


main()