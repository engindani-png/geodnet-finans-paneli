# app.py
# MonsPro | Operasyonel Portal (GEODNET)
# - Excel auto map: SN / Partner / İl / Konum
# - Background refresh + progress (thread-safe JobStore)
# - Partner PDF report includes İl + Konum columns (must)
#
# Run:
#   pip install streamlit pandas openpyxl requests fpdf streamlit-autorefresh
#   streamlit run app.py

import os
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple, List

import pandas as pd
import requests
import streamlit as st
from fpdf import FPDF

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOR = True
except Exception:
    HAS_AUTOR = False

# -----------------------------
# CONFIG
# -----------------------------
APP_TITLE = "MonsPro | Operasyonel Portal"
REFRESH_INTERVAL_SEC = 15 * 60
UI_TICK_SEC = 2
HTTP_TIMEOUT = 25
LOW_PROD_THRESHOLD_DEFAULT = 180
TZ = timezone(timedelta(hours=3))  # TR (+03)


# -----------------------------
# UTILS
# -----------------------------
def now_tr() -> datetime:
    return datetime.now(TZ)

def fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def normalize_col(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip().lower()
    repl = {"ı": "i", "İ": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"}
    for a, b in repl.items():
        s = s.replace(a, b)
    s = " ".join(s.split())
    return s

def pick_first_existing(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols_norm = {normalize_col(c): c for c in df.columns}
    for cand in candidates:
        key = normalize_col(cand)
        if key in cols_norm:
            return cols_norm[key]
    return None


# -----------------------------
# EXCEL LOADING (AUTO MAP)
# -----------------------------
def load_partner_excel(uploaded_file) -> Tuple[pd.DataFrame, Dict[str, Optional[str]]]:
    if uploaded_file is None:
        return pd.DataFrame(), {}

    try:
        df = pd.read_excel(
            uploaded_file,
            engine="openpyxl",
            dtype=str,
            keep_default_na=False,
            na_filter=False,
        )
    except Exception:
        # header kayık ise
        df = pd.read_excel(
            uploaded_file,
            engine="openpyxl",
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            header=1,
        )

    df = df.applymap(lambda x: str(x).strip() if x is not None else "")

    colmap = {
        "sn": pick_first_existing(df, ["sn", "serial", "serial number", "seri no", "seri numarasi", "miner", "miner no", "miner sn", "device"]),
        "partner": pick_first_existing(df, ["is ortagi", "is ortağı", "partner", "bayi", "musteri", "müşteri", "firma", "company"]),
        "il": pick_first_existing(df, ["il", "şehir", "sehir", "province", "city"]),
        "konum": pick_first_existing(df, ["konum", "lokasyon", "location", "adres", "address", "site"]),
    }
    return df, colmap

def standardize_partner_df(df: pd.DataFrame, colmap: Dict[str, Optional[str]]) -> pd.DataFrame:
    def series_for(key: str) -> pd.Series:
        c = colmap.get(key)
        if c and c in df.columns:
            return df[c].astype(str).fillna("").map(lambda x: str(x).strip())
        return pd.Series([""] * len(df))

    out = pd.DataFrame()
    out["SN"] = series_for("sn")
    out["Partner"] = series_for("partner")
    out["İl"] = series_for("il")
    out["Konum"] = series_for("konum")

    out["SN_norm"] = out["SN"].astype(str).str.strip()
    out = out[out["SN_norm"] != ""].copy()
    out.drop(columns=["SN_norm"], inplace=True)

    out = out.drop_duplicates(subset=["SN"], keep="first").reset_index(drop=True)
    return out


# -----------------------------
# PRICE FETCH (CACHE)
# -----------------------------
@st.cache_data(ttl=120)
def fetch_geod_usd() -> Optional[float]:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "geodnet", "vs_currencies": "usd"}
    r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    v = data.get("geodnet", {}).get("usd")
    return float(v) if v else None

@st.cache_data(ttl=120)
def fetch_usd_try() -> Optional[float]:
    url = "https://open.er-api.com/v6/latest/USD"
    r = requests.get(url, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    v = (data.get("rates") or {}).get("TRY")
    return float(v) if v else None


# -----------------------------
# GEODNET API PLACEHOLDER
# -----------------------------
def get_all_rewards(sn: str, start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
    """
    >>> BURAYA SENİN ÇALIŞAN API/AES KODUNU KOY <<<
    Bu fonksiyon, SN için (start_dt, end_dt) aralığında reward payload döndürmeli.

    return örneği sende neyse o.
    """
    # PLACEHOLDER:
    return {"ok": True, "sn": sn, "rewards": []}

def parse_reward_fields(payload: Dict[str, Any]) -> Dict[str, float]:
    """
    >>> BURAYA SENİN PAYLOAD PARSE KODUNU KOY <<<
    Dönüş: tablo kolonları için sayılar
      - kazanc   (örn: points / score)
      - hakediş  (GEOD hakediş)
      - eklenen  (GEOD eklenen)
      - top_geod (hakediş+eklenen gibi)
    """
    # PLACEHOLDER:
    return {"kazanc": 0.0, "hakedis": 0.0, "eklenen": 0.0, "top_geod": 0.0}


# -----------------------------
# THREAD-SAFE JOB STORE
# -----------------------------
@dataclass
class JobProgress:
    running: bool = False
    total: int = 0
    done: int = 0
    success: int = 0
    failed: int = 0
    current_sn: str = ""
    last_heartbeat: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    last_error: str = ""

class JobStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.progress = JobProgress()
        self.log: List[str] = []
        self.last_refresh_at: Optional[datetime] = None
        self.next_refresh_at: datetime = now_tr() + timedelta(seconds=REFRESH_INTERVAL_SEC)
        self.thread: Optional[threading.Thread] = None

    def add_log(self, msg: str):
        with self.lock:
            self.log.append(f"{fmt_dt(now_tr())} - {msg}")
            self.log = self.log[-400:]

    def snapshot(self) -> Tuple[JobProgress, List[str], Optional[datetime], datetime]:
        with self.lock:
            p = JobProgress(**self.progress.__dict__)
            logs = list(self.log)
            return p, logs, self.last_refresh_at, self.next_refresh_at

    def start_refresh(self, base_rows: pd.DataFrame, start_dt: datetime, end_dt: datetime, update_callback):
        with self.lock:
            if self.progress.running:
                return False

            self.progress = JobProgress(
                running=True,
                total=len(base_rows),
                done=0,
                success=0,
                failed=0,
                current_sn="",
                last_heartbeat=now_tr(),
                started_at=now_tr(),
                finished_at=None,
                last_error=""
            )
        self.add_log(f"Refresh başladı. cihaz={len(base_rows)} aralık={start_dt.date()}..{end_dt.date()}")

        def worker(df_rows: pd.DataFrame):
            updated = []
            for _, row in df_rows.iterrows():
                sn = str(row.get("SN", "")).strip()
                if not sn:
                    continue

                with self.lock:
                    self.progress.current_sn = sn
                    self.progress.last_heartbeat = now_tr()

                try:
                    payload = get_all_rewards(sn, start_dt, end_dt)
                    fields = parse_reward_fields(payload)

                    kazanc = safe_float(fields.get("kazanc", 0.0))
                    hakedis = safe_float(fields.get("hakedis", 0.0))
                    eklenen = safe_float(fields.get("eklenen", 0.0))
                    top_geod = safe_float(fields.get("top_geod", hakedis + eklenen))

                    new_row = row.to_dict()
                    new_row["Kazanç"] = kazanc
                    new_row["Hakediş"] = hakedis
                    new_row["Eklenen"] = eklenen
                    new_row["Top.GEOD"] = top_geod
                    new_row["Son_Guncelleme"] = fmt_dt(now_tr())

                    updated.append(new_row)

                    with self.lock:
                        self.progress.success += 1

                except Exception as e:
                    with self.lock:
                        self.progress.failed += 1
                        self.progress.last_error = str(e)
                    self.add_log(f"HATA sn={sn}: {e}")

                finally:
                    with self.lock:
                        self.progress.done += 1
                        self.progress.last_heartbeat = now_tr()

            try:
                upd_df = pd.DataFrame(updated)
                update_callback(upd_df)
            except Exception as e:
                self.add_log(f"DF update hatası: {e}")

            with self.lock:
                self.progress.running = False
                self.progress.finished_at = now_tr()
                self.progress.current_sn = ""
                self.progress.last_heartbeat = now_tr()
                self.last_refresh_at = now_tr()
                self.next_refresh_at = now_tr() + timedelta(seconds=REFRESH_INTERVAL_SEC)

            self.add_log("Refresh bitti.")

        t = threading.Thread(target=worker, args=(base_rows.copy(),), daemon=True)
        with self.lock:
            self.thread = t
        t.start()
        return True

@st.cache_resource
def get_jobstore() -> JobStore:
    return JobStore()


# -----------------------------
# PDF: Partner Report (TABLE includes İl + Konum MUST)
# -----------------------------
def pdf_kv(pdf: FPDF, k: str, v: str):
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, f"{k}: {v}", ln=True)

def pdf_table_header(pdf: FPDF, headers: List[str], widths: List[int]):
    pdf.set_font("Helvetica", "B", 10)
    for i, h in enumerate(headers):
        pdf.cell(widths[i], 9, h, border=1, align="C")
    pdf.ln(9)

def pdf_table_row(pdf: FPDF, values: List[Any], widths: List[int]):
    pdf.set_font("Helvetica", "", 10)
    for i, v in enumerate(values):
        text = "" if v is None else str(v)
        pdf.cell(widths[i], 9, text, border=1)
    pdf.ln(9)

def generate_partner_pdf(
    partner_name: str,
    start_dt: datetime,
    end_dt: datetime,
    geod_usd: Optional[float],
    usd_try: Optional[float],
    df_partner_rows: pd.DataFrame,
    out_path: str
) -> str:
    """
    df_partner_rows must include:
      SN, Partner, İl, Konum, Kazanç, Durum, Hakediş, Eklenen, Top.GEOD, Tutar(TL)
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "MonsPro GEODNET Odul Raporu", ln=True)

    pdf_kv(pdf, "Is Ortagi", partner_name)
    pdf_kv(pdf, "Rapor Tarihi", end_dt.strftime("%d.%m.%Y"))
    pdf_kv(pdf, "Donem", f"{start_dt.strftime('%d.%m.%Y')} - {end_dt.strftime('%d.%m.%Y')}")
    price_str = f"${geod_usd:.4f}" if geod_usd else "—"
    kur_str = f"{usd_try:.2f} TL" if usd_try else "—"
    pdf_kv(pdf, "GEOD Fiyat | Kur", f"{price_str} | {kur_str}")

    pdf.ln(4)

    # TABLE: include İl + Konum columns
    headers = ["Miner No", "Il", "Konum", "Kazanc", "Durum", "Hakedis", "Eklenen", "Top.GEOD", "Tutar(TL)"]
    widths  = [26,        14,   30,      16,       20,      16,        16,       18,        24]  # toplam ~180

    pdf_table_header(pdf, headers, widths)

    total_tl = 0.0
    for _, r in df_partner_rows.iterrows():
        total_tl += safe_float(r.get("Tutar(TL)", 0.0))

        values = [
            r.get("SN", ""),
            r.get("İl", ""),
            r.get("Konum", ""),
            f"{safe_float(r.get('Kazanç', 0.0)):.2f}",
            r.get("Durum", ""),
            f"{safe_float(r.get('Hakediş', 0.0)):.2f}",
            f"{safe_float(r.get('Eklenen', 0.0)):.2f}",
            f"{safe_float(r.get('Top.GEOD', 0.0)):.2f}",
            f"{safe_float(r.get('Tutar(TL)', 0.0)):.2f} TL",
        ]

        # sayfa taşarsa otomatik geçecek, ama header tekrar basmak istersen:
        if pdf.get_y() > 260:
            pdf.add_page()
            pdf_table_header(pdf, headers, widths)

        pdf_table_row(pdf, values, widths)

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"Genel Toplam: {total_tl:.2f} TL", ln=True, align="R")

    pdf.output(out_path)
    return out_path


# -----------------------------
# STREAMLIT APP
# -----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
job = get_jobstore()

if HAS_AUTOR:
    st_autorefresh(interval=UI_TICK_SEC * 1000, key="ui_tick")

if "miners_df" not in st.session_state:
    st.session_state.miners_df = pd.DataFrame()
if "report_df" not in st.session_state:
    st.session_state.report_df = pd.DataFrame()

st.title(APP_TITLE)

# Sidebar
with st.sidebar:
    prog, logs, last_ref, next_ref = job.snapshot()

    st.subheader("Durum")
    st.write(f"**Son yenileme:** {fmt_dt(last_ref)}")
    st.write(f"**Sıradaki yenileme:** {fmt_dt(next_ref)}")
    rem = (next_ref - now_tr()).total_seconds()
    st.write(f"**Kalan:** {int(max(rem, 0))} sn")

    manual_refresh = st.button("🔄 Manuel Yenile", use_container_width=True)
    offline_only = st.toggle("Sadece düşük üretim (AZ URETIM)", value=False)

    st.divider()
    st.caption("Token Kontrol")
    st.write("GEODNET_CLIENT_ID:", "✅" if os.getenv("GEODNET_CLIENT_ID") else "❌")
    st.write("GEODNET_TOKEN:", "✅" if os.getenv("GEODNET_TOKEN") else "❌")

# Prices
c1, c2, c3 = st.columns(3)
with c1:
    try:
        geod_usd = fetch_geod_usd()
        st.metric("GEOD / USD", f"{geod_usd:.6f}" if geod_usd else "—")
    except Exception:
        geod_usd = None
        st.metric("GEOD / USD", "—")
with c2:
    try:
        usd_try = fetch_usd_try()
        st.metric("USD / TRY", f"{usd_try:.2f}" if usd_try else "—")
    except Exception:
        usd_try = None
        st.metric("USD / TRY", "—")
with c3:
    low_thr = st.number_input("Düşük Üretim Eşiği (GEOD)", min_value=0, value=LOW_PROD_THRESHOLD_DEFAULT, step=10)

st.divider()

# Excel upload
st.subheader("Excel Yükle (otomatik eşleme)")
uploaded = st.file_uploader("Excel dosyası (.xlsx)", type=["xlsx", "xls"], key="excel_upload")

if uploaded is not None:
    raw_df, colmap = load_partner_excel(uploaded)
    if raw_df.empty or not colmap.get("sn"):
        st.error("Excel okundu ama SN kolonu bulunamadı. Başlıkları kontrol et (SN/Serial/Seri No vb.).")
    else:
        std = standardize_partner_df(raw_df, colmap)
        st.session_state.miners_df = std
        st.success(f"Yüklendi: {len(std)} cihaz. (SN={colmap['sn']}, Partner={colmap.get('partner')}, İl={colmap.get('il')}, Konum={colmap.get('konum')})")

st.divider()

# Refresh controls + progress
st.subheader("Yenileme Durumu")
prog, logs, last_ref, next_ref = job.snapshot()

p1, p2, p3, p4 = st.columns([1.2, 1, 1, 2.2])
with p1:
    st.metric("Durum", "ÇALIŞIYOR" if prog.running else "BEKLEMEDE")
with p2:
    st.metric("İlerleme", f"{prog.done}/{prog.total}" if prog.total else "0/0")
with p3:
    st.metric("Başarılı/Hatalı", f"{prog.success}/{prog.failed}")
with p4:
    st.write(f"**Şu an:** {prog.current_sn or '—'}")
    st.write(f"**Heartbeat:** {fmt_dt(prog.last_heartbeat)}")
    if prog.last_error:
        st.error(f"Son hata: {prog.last_error}")

ratio = 0.0 if prog.total == 0 else min(1.0, prog.done / max(1, prog.total))
st.progress(ratio)
st.caption(f"Yenilenen cihaz: {prog.done} / {prog.total}")

# Report period selection
st.subheader("Rapor Dönemi")
d1, d2 = st.columns(2)
with d1:
    start_date = st.date_input("Başlangıç", value=(now_tr() - timedelta(days=3)).date())
with d2:
    end_date = st.date_input("Bitiş", value=now_tr().date())

start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=TZ)
end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=TZ)

def update_report_df(upd_df: pd.DataFrame):
    """
    upd_df: refreshed rows (contains SN, Partner, İl, Konum, Kazanç, Hakediş, Eklenen, Top.GEOD, Son_Guncelleme)
    Build session_state.report_df with required columns + Tutar(TL) + Durum labels.
    """
    if upd_df is None or upd_df.empty:
        return

    df = upd_df.copy()

    # Durum: düşük üretim vs
    # (Senin eski PDF'inde "AZ URETIM" görünüyor; onu koruyalım)
    df["Durum"] = df["Top.GEOD"].apply(lambda x: "AZ URETIM" if safe_float(x, 0.0) < float(low_thr) else "NORMAL")

    # Tutar(TL)
    # senin PDF'te muhtemelen hakediş->top_geod üzerinden TL çeviriyorsun.
    # burada Top.GEOD baz alıyoruz:
    geod_price = geod_usd or 0.0
    kur = usd_try or 0.0
    df["Tutar(TL)"] = df["Top.GEOD"].apply(lambda g: safe_float(g, 0.0) * geod_price * kur)

    # Kolonları rapor düzenine getir
    keep = ["SN", "Partner", "İl", "Konum", "Kazanç", "Durum", "Hakediş", "Eklenen", "Top.GEOD", "Tutar(TL)", "Son_Guncelleme"]
    for k in keep:
        if k not in df.columns:
            df[k] = ""

    st.session_state.report_df = df[keep].copy()

# Trigger refresh
should_auto_refresh = (now_tr() >= next_ref) and (not prog.running) and (not st.session_state.miners_df.empty)

if manual_refresh or should_auto_refresh:
    if st.session_state.miners_df.empty:
        st.warning("Önce Excel yükle (SN listesi).")
    else:
        started = job.start_refresh(
            base_rows=st.session_state.miners_df[["SN", "Partner", "İl", "Konum"]],
            start_dt=start_dt,
            end_dt=end_dt,
            update_callback=update_report_df
        )
        if not started:
            st.info("Zaten bir yenileme çalışıyor.")

st.divider()

# Device table
st.subheader("Cihaz Listesi (Rapor)")
if st.session_state.report_df.empty:
    st.info("Henüz rapor datası yok. Manuel yenile ile çekebilirsin.")
else:
    df_show = st.session_state.report_df.copy()
    if offline_only:
        df_show = df_show[df_show["Durum"] == "AZ URETIM"].copy()

    st.dataframe(df_show, use_container_width=True, height=520)

st.divider()

# Partner PDF
st.subheader("PDF Rapor (İş Ortağı)")

if st.session_state.report_df.empty:
    st.warning("Önce rapor datasını oluştur (yenile).")
else:
    partners = sorted([p for p in st.session_state.report_df["Partner"].dropna().astype(str).unique().tolist() if p.strip() != ""])
    if not partners:
        st.warning("Partner bilgisi yok. Excel'de Partner sütununu kontrol et.")
    else:
        sel_partner = st.selectbox("İş Ortağı Seç", partners)

        make_pdf = st.button("📄 Seçili İş Ortağı için PDF üret", use_container_width=True)
        if make_pdf:
            df_partner = st.session_state.report_df[st.session_state.report_df["Partner"].astype(str) == str(sel_partner)].copy()
            if df_partner.empty:
                st.error("Bu iş ortağı için satır yok.")
            else:
                safe_name = "".join([c if c.isalnum() else "_" for c in sel_partner])[:60]
                out_path = f"/mnt/data/GEODNET_Rapor_{safe_name}_{end_dt.strftime('%Y%m%d')}.pdf"

                # PDF MUST include İl + Konum columns (already in df_partner)
                generate_partner_pdf(
                    partner_name=sel_partner,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    geod_usd=geod_usd,
                    usd_try=usd_try,
                    df_partner_rows=df_partner,
                    out_path=out_path
                )

                st.success("PDF oluşturuldu (tabloda İl + Konum var).")
                with open(out_path, "rb") as f:
                    st.download_button(
                        "⬇️ PDF İndir",
                        data=f.read(),
                        file_name=os.path.basename(out_path),
                        mime="application/pdf",
                        use_container_width=True
                    )

with st.expander("İşlem Logları"):
    prog, logs, _, _ = job.snapshot()
    if logs:
        st.code("\n".join(logs[-250:]))
    else:
        st.write("Log yok.")
