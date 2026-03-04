# app.py
# MonsPro | Operasyonel Portal (GEODNET)
# - Arka planda yenileme (thread) + İlerleme barı (X/Y, heartbeat, current SN)
# - Excel sütunlarını otomatik eşler (SN / Partner / İl / Konum)
# - PDF raporda mutlaka İl + Konum yer alır
#
# NOT: GEODNET API + AES encrypt kısmını senin çalışan fonksiyonlarınla değiştirmen gerekiyor.
#      Aşağıdaki 3 fonksiyon "PLACEHOLDER":
#        1) encrypt_params_aes_cbc(params)
#        2) get_rewards_timeline(sn, start_ms, end_ms)
#        3) parse_total_geod(payload)

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

# Optional UI rerun without full page reload
try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOR = True
except Exception:
    HAS_AUTOR = False


# -----------------------------
# Config
# -----------------------------
APP_TITLE = "MonsPro | Operasyonel Portal"
REFRESH_INTERVAL_SEC = 15 * 60         # 15 dk
UI_TICK_SEC = 2                        # progress UI güncelleme aralığı
HTTP_TIMEOUT = 25
PAYOUT_CUTOFF_TR = "08:30"             # sende vardı; gerekirse kullanırsın
LOW_PROD_THRESHOLD_DEFAULT = 180
TZ = timezone(timedelta(hours=3))      # TR


# -----------------------------
# Utils
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
# Excel Loader (Auto map)
# -----------------------------
def load_partner_excel(uploaded_file) -> Tuple[pd.DataFrame, Dict[str, Optional[str]]]:
    if uploaded_file is None:
        return pd.DataFrame(), {}

    # dtype=str + keep_default_na=False + na_filter=False -> boşlar "NaN" olmaz
    try:
        df = pd.read_excel(
            uploaded_file,
            engine="openpyxl",
            dtype=str,
            keep_default_na=False,
            na_filter=False
        )
    except Exception:
        # bazı dosyalarda header kaymış olabiliyor
        df = pd.read_excel(
            uploaded_file,
            engine="openpyxl",
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            header=1
        )

    df = df.applymap(lambda x: str(x).strip() if x is not None else "")

    colmap = {
        "sn": pick_first_existing(df, ["sn", "serial", "serial number", "seri no", "seri numarasi", "miner", "miner sn", "device"]),
        "partner": pick_first_existing(df, ["is ortagi", "is ortağı", "partner", "bayi", "musteri", "müşteri", "firma", "company"]),
        "il": pick_first_existing(df, ["il", "şehir", "sehir", "province", "city"]),
        "konum": pick_first_existing(df, ["konum", "lokasyon", "location", "adres", "address", "site"]),
    }
    return df, colmap

def standardize_partner_df(df: pd.DataFrame, colmap: Dict[str, Optional[str]]) -> pd.DataFrame:
    def col_series(key: str) -> pd.Series:
        c = colmap.get(key)
        if c and c in df.columns:
            return df[c].astype(str).fillna("").map(lambda x: str(x).strip())
        return pd.Series([""] * len(df))

    out = pd.DataFrame()
    out["SN"] = col_series("sn")
    out["Partner"] = col_series("partner")
    out["İl"] = col_series("il")
    out["Konum"] = col_series("konum")

    out["SN_norm"] = out["SN"].astype(str).str.strip()
    out = out[out["SN_norm"] != ""].copy()
    out.drop(columns=["SN_norm"], inplace=True)

    # Unique SN
    out = out.drop_duplicates(subset=["SN"], keep="first").reset_index(drop=True)
    return out


# -----------------------------
# Price fetch (cache)
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
# GEODNET API placeholders (SENİN ÇALIŞAN KODUNLA DEĞİŞTİR)
# -----------------------------
def geodnet_api_headers() -> Dict[str, str]:
    token = os.getenv("GEODNET_TOKEN", "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}

def encrypt_params_aes_cbc(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    >>> BURAYA SENİN ÇALIŞAN AES-CBC PARAM ENCRYPT KODUNU KOY <<<
    """
    return params

def get_rewards_timeline(sn: str, start_ms: int, end_ms: int) -> Dict[str, Any]:
    """
    >>> BURAYA SENİN ÇALIŞAN getRewardsTimeLine API ÇAĞRINI KOY <<<
    """
    # url = "https://....../getRewardsTimeLine"
    # payload = encrypt_params_aes_cbc({"sn": sn, "startTime": start_ms, "endTime": end_ms})
    # r = requests.post(url, json=payload, headers=geodnet_api_headers(), timeout=HTTP_TIMEOUT)
    # r.raise_for_status()
    # return r.json()
    return {"ok": True, "rewards": []}

def get_all_rewards(sn: str, days: int = 30) -> Dict[str, Any]:
    # 30 günlük (istersen chunk'la genişlet)
    end = now_tr()
    start = end - timedelta(days=days)
    end_ms = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)
    return get_rewards_timeline(sn, start_ms, end_ms)

def parse_total_geod(payload: Dict[str, Any]) -> float:
    """
    >>> PAYLOAD FORMATINA GÖRE DÜZENLE <<<
    """
    rewards = payload.get("rewards") or []
    total = 0.0
    for it in rewards:
        try:
            total += float(it.get("amount", 0))
        except Exception:
            pass
    return total


# -----------------------------
# Thread-safe Job Store (progress kesin aksın)
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

    def get_snapshot(self) -> Tuple[JobProgress, List[str], Optional[datetime], datetime]:
        with self.lock:
            # shallow copy
            p = JobProgress(**self.progress.__dict__)
            logs = list(self.log)
            return p, logs, self.last_refresh_at, self.next_refresh_at

    def start(self, rows: pd.DataFrame, update_callback):
        """
        rows: standardized df (SN, Partner, İl, Konum)
        update_callback: function(updated_rows_df) -> None  (UI state update)
        """
        with self.lock:
            if self.progress.running:
                return False

            self.progress = JobProgress(
                running=True,
                total=len(rows),
                done=0,
                success=0,
                failed=0,
                current_sn="",
                last_heartbeat=now_tr(),
                started_at=now_tr(),
                finished_at=None,
                last_error=""
            )
            self.add_log(f"Refresh başladı. cihaz={len(rows)}")

        def worker(df_rows: pd.DataFrame):
            updated_rows = []

            for _, row in df_rows.iterrows():
                sn = str(row.get("SN", "")).strip()
                if not sn:
                    continue

                # progress: current sn
                with self.lock:
                    self.progress.current_sn = sn
                    self.progress.last_heartbeat = now_tr()

                try:
                    payload = get_all_rewards(sn, days=30)
                    total_geod = parse_total_geod(payload)

                    new_row = row.to_dict()
                    new_row["Toplam_GEOD_30G"] = float(total_geod)
                    new_row["Son_Guncelleme"] = fmt_dt(now_tr())

                    # Offline/Online (senin minerstatus kriterinle değiştirilebilir)
                    new_row["Durum"] = "OFFLINE" if float(total_geod) <= 0 else "ONLINE"

                    updated_rows.append(new_row)

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

            # UI dataframe merge/update
            try:
                upd_df = pd.DataFrame(updated_rows)
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

        t = threading.Thread(target=worker, args=(rows.copy(),), daemon=True)
        with self.lock:
            self.thread = t
        t.start()
        return True

@st.cache_resource
def get_jobstore() -> JobStore:
    return JobStore()


# -----------------------------
# PDF Reporting (İl + Konum MUTLAKA)
# -----------------------------
def _pdf_add_kv(pdf: FPDF, k: str, v: str):
    pdf.set_font("Helvetica", size=11)
    pdf.cell(45, 7, f"{k}:", 0, 0)
    pdf.multi_cell(0, 7, str(v) if v is not None else "")

def generate_station_pdf(
    sn: str,
    row: Dict[str, Any],
    geod_usd: Optional[float],
    usd_try: Optional[float],
    out_path: str
) -> str:
    """
    row: must include SN, Partner, İl, Konum, Toplam_GEOD_30G, Durum, Son_Guncelleme
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "MonsPro GEODNET Istasyon Performans Raporu", ln=True)

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Rapor Tarihi: {fmt_dt(now_tr())}", ln=True)
    pdf.ln(2)

    # MUST: İl + Konum
    partner = row.get("Partner", "")
    il = row.get("İl", "")
    konum = row.get("Konum", "")

    total_geod_30g = safe_float(row.get("Toplam_GEOD_30G", 0.0), 0.0)
    durum = row.get("Durum", "")
    son = row.get("Son_Guncelleme", "")

    usd_val = total_geod_30g * (geod_usd or 0.0) if geod_usd else None
    tl_val = usd_val * (usd_try or 0.0) if (usd_val is not None and usd_try) else None

    _pdf_add_kv(pdf, "Istasyon SN", sn)
    _pdf_add_kv(pdf, "Is Ortagi", partner)

    # *** User request: MUST appear ***
    _pdf_add_kv(pdf, "Il", il)
    _pdf_add_kv(pdf, "Konum", konum)

    pdf.ln(2)
    _pdf_add_kv(pdf, "Durum", durum)
    _pdf_add_kv(pdf, "Toplam GEOD (30 Gun)", f"{total_geod_30g:.4f}")
    _pdf_add_kv(pdf, "GEOD/USD", f"{geod_usd:.6f}" if geod_usd else "—")
    _pdf_add_kv(pdf, "USD Degeri", f"${usd_val:.2f}" if usd_val is not None else "—")
    _pdf_add_kv(pdf, "USD/TRY", f"{usd_try:.2f}" if usd_try else "—")
    _pdf_add_kv(pdf, "TL Degeri", f"{tl_val:,.0f} TL" if tl_val is not None else "—")
    _pdf_add_kv(pdf, "Son Guncelleme", son)

    pdf.output(out_path)
    return out_path


# -----------------------------
# Streamlit App
# -----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
job = get_jobstore()

# UI tick (no full page reload)
if HAS_AUTOR:
    st_autorefresh(interval=UI_TICK_SEC * 1000, key="ui_tick")

# Session DF
if "miners_df" not in st.session_state:
    st.session_state.miners_df = pd.DataFrame()

st.title(APP_TITLE)

# Sidebar controls (sade)
with st.sidebar:
    st.subheader("Kontrol")

    prog, logs, last_refresh_at, next_refresh_at = job.get_snapshot()

    st.write(f"**Son yenileme:** {fmt_dt(last_refresh_at)}")
    st.write(f"**Sıradaki yenileme:** {fmt_dt(next_refresh_at)}")

    rem = (next_refresh_at - now_tr()).total_seconds()
    if rem < 0:
        rem = 0
    st.write(f"**Kalan:** {int(rem)} sn")

    manual_refresh = st.button("🔄 Manuel Yenile", use_container_width=True)

    st.session_state.offline_only = st.toggle(
        "Sadece OFFLINE listele",
        value=st.session_state.get("offline_only", False)
    )

    st.divider()
    st.caption("Token Kontrol")
    st.write("GEODNET_CLIENT_ID:", "✅" if os.getenv("GEODNET_CLIENT_ID") else "❌")
    st.write("GEODNET_TOKEN:", "✅" if os.getenv("GEODNET_TOKEN") else "❌")

# Top metrics
col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    try:
        geod_usd = fetch_geod_usd()
        st.metric("GEOD / USD", f"{geod_usd:.6f}" if geod_usd else "—")
    except Exception:
        geod_usd = None
        st.metric("GEOD / USD", "—")
with col2:
    try:
        usd_try = fetch_usd_try()
        st.metric("USD / TRY", f"{usd_try:.2f}" if usd_try else "—")
    except Exception:
        usd_try = None
        st.metric("USD / TRY", "—")
with col3:
    low_thr = st.number_input(
        "Düşük Üretim Eşiği (30G GEOD)",
        min_value=0,
        value=LOW_PROD_THRESHOLD_DEFAULT,
        step=10
    )

st.divider()

# Excel upload (auto-map, sade)
st.subheader("Excel Yükle (otomatik sütun eşleme)")
uploaded = st.file_uploader("Excel dosyası (.xlsx)", type=["xlsx", "xls"], key="excel_upload")

if uploaded is not None:
    raw_df, colmap = load_partner_excel(uploaded)

    if raw_df.empty or not colmap.get("sn"):
        st.error("Excel okundu ama SN kolonu otomatik bulunamadı. Başlıkları kontrol et (SN/Serial/Seri No vb.).")
    else:
        std_df = standardize_partner_df(raw_df, colmap)
        st.session_state.miners_df = std_df.copy()
        st.success(f"Yüklendi: {len(std_df)} cihaz (SN). Otomatik eşleme: SN={colmap.get('sn')}, Partner={colmap.get('partner')}, İl={colmap.get('il')}, Konum={colmap.get('konum')}")

# Progress area (sade)
st.subheader("Yenileme Durumu")

prog, logs, last_refresh_at, next_refresh_at = job.get_snapshot()
cA, cB, cC, cD = st.columns([1.2, 1, 1, 2.2])
with cA:
    st.metric("Durum", "ÇALIŞIYOR" if prog.running else "BEKLEMEDE")
with cB:
    st.metric("İlerleme", f"{prog.done}/{prog.total}" if prog.total else "0/0")
with cC:
    st.metric("Başarılı/Hatalı", f"{prog.success}/{prog.failed}")
with cD:
    st.write(f"**Şu an:** {prog.current_sn or '—'}")
    st.write(f"**Heartbeat:** {fmt_dt(prog.last_heartbeat)}")
    if prog.last_error:
        st.error(f"Son hata: {prog.last_error}")

ratio = 0.0 if prog.total == 0 else min(1.0, prog.done / max(1, prog.total))
st.progress(ratio)
st.caption(f"Yenilenen cihaz: {prog.done} / {prog.total}")

# Auto refresh trigger (infinite loop yok)
should_auto_refresh = (now_tr() >= next_refresh_at) and (not prog.running) and (not st.session_state.miners_df.empty)

# Data update callback for jobstore
def merge_updates(upd_df: pd.DataFrame):
    if upd_df is None or upd_df.empty:
        return
    base = st.session_state.miners_df.copy()
    # eski kolonları drop edip yeniden merge
    for col in ["Toplam_GEOD_30G", "Son_Guncelleme", "Durum"]:
        if col in base.columns:
            base = base.drop(columns=[col], errors="ignore")

    st.session_state.miners_df = base.merge(
        upd_df[["SN", "Toplam_GEOD_30G", "Son_Guncelleme", "Durum"]],
        on="SN",
        how="left"
    )

if manual_refresh or should_auto_refresh:
    if st.session_state.miners_df.empty:
        st.warning("Önce Excel yükleyip cihaz listesini oluştur.")
    else:
        started = job.start(
            st.session_state.miners_df[["SN", "Partner", "İl", "Konum"]],
            update_callback=merge_updates
        )
        if not started:
            st.info("Zaten bir yenileme işlemi çalışıyor.")

st.divider()

# Device list + PDF
st.subheader("Cihaz Listesi")

df_show = st.session_state.miners_df.copy()

if df_show.empty:
    st.info("Liste yok. Excel yükleyince burada görünecek.")
else:
    # Ensure columns exist
    for col in ["Durum", "Toplam_GEOD_30G", "Son_Guncelleme"]:
        if col not in df_show.columns:
            df_show[col] = "" if col != "Toplam_GEOD_30G" else None

    # Offline filter without re-scan
    if st.session_state.get("offline_only", False):
        df_show = df_show[df_show["Durum"].astype(str).str.upper() == "OFFLINE"].copy()

    # Low production flag
    df_show["Düşük_Üretim"] = df_show["Toplam_GEOD_30G"].apply(lambda v: safe_float(v, 0.0) < float(low_thr))

    st.dataframe(df_show, use_container_width=True, height=520)

    st.divider()
    st.subheader("PDF Rapor")

    # Station selection
    sns = df_show["SN"].astype(str).tolist()
    selected_sn = st.selectbox("PDF oluşturulacak istasyon (SN)", sns, index=0)

    colp1, colp2 = st.columns([1, 2])
    with colp1:
        make_pdf = st.button("📄 Seçili SN için PDF üret", use_container_width=True)
    with colp2:
        st.caption("PDF raporda **İl** ve **Konum** mutlaka yer alır (Excel'den alınır).")

    if make_pdf and selected_sn:
        # Find row
        match = df_show[df_show["SN"].astype(str) == str(selected_sn)]
        if match.empty:
            st.error("Seçili SN tabloda bulunamadı.")
        else:
            row = match.iloc[0].to_dict()

            # Output path
            safe_sn = str(selected_sn).replace("/", "_").replace("\\", "_").replace(" ", "_")
            out_path = f"/mnt/data/MonsPro_GEODNET_Rapor_{safe_sn}.pdf"

            try:
                generate_station_pdf(
                    sn=str(selected_sn),
                    row=row,
                    geod_usd=geod_usd,
                    usd_try=usd_try,
                    out_path=out_path
                )
                st.success("PDF oluşturuldu.")
                st.download_button(
                    label="⬇️ PDF İndir",
                    data=open(out_path, "rb").read(),
                    file_name=f"MonsPro_GEODNET_Rapor_{safe_sn}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"PDF üretim hatası: {e}")

# Logs
with st.expander("İşlem Logları"):
    prog, logs, _, _ = job.get_snapshot()
    if logs:
        st.code("\n".join(logs[-250:]))
    else:
        st.write("Log yok.")
