# app.py
# MonsPro - GEODNET Token Hesaplama & Offline Takip (Throttle + Retry + Lookback)
# 23.02.2026 patch: per-device delay, retry/backoff, wider time window for online/offline queries

import os
import time
import json
import math
import random
import datetime as dt
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

import requests
import pandas as pd
import streamlit as st

# =========================
# CONFIG (Edit to match your existing endpoints if different)
# =========================

# These are placeholders; replace with the exact endpoints you used yesterday if they differ.
GEODNET_BASE_URL = os.getenv("GEODNET_BASE_URL", "https://api.geodnet.com")  # keep same if already set
REWARDS_TIMELINE_ENDPOINT = os.getenv("REWARDS_TIMELINE_ENDPOINT", f"{GEODNET_BASE_URL}/api/v1/rewards/getRewardsTimeLine")
MINER_STATUS_ENDPOINT = os.getenv("MINER_STATUS_ENDPOINT", f"{GEODNET_BASE_URL}/api/v1/minerStatus")  # <-- adjust to your real one

# Auth
GEODNET_CLIENT_ID = os.getenv("GEODNET_CLIENT_ID", "").strip()
GEODNET_TOKEN = os.getenv("GEODNET_TOKEN", "").strip()

# =========================
# UI / APP SETTINGS
# =========================

st.set_page_config(page_title="MonsPro | GEODNET Token Hesaplama & Offline Takip", layout="wide")

st.title("MonsPro | GEODNET Token Hesaplama & Offline Takip")
st.caption("Patch: API throttle (2–3s), retry/backoff, lookback penceresi büyütme (offline kaçırmayı azaltır).")

# =========================
# Helpers
# =========================

@dataclass
class ThrottleConfig:
    per_device_delay_sec: float = 2.5     # 2-3 sec suggested
    max_retries: int = 3
    base_backoff_sec: float = 1.2         # exponential backoff base
    jitter_sec: float = 0.35              # random jitter
    timeout_sec: float = 20.0             # request timeout

def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def _to_iso_z(t: dt.datetime) -> str:
    # ISO8601 with Z
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def safe_float(x, default=0.0):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return default
        return float(x)
    except Exception:
        return default

def normalize_sn(sn: str) -> str:
    if sn is None:
        return ""
    return str(sn).strip()

def sleep_with_jitter(sec: float, jitter: float):
    if sec <= 0:
        return
    time.sleep(max(0.0, sec + random.uniform(-jitter, jitter)))

# =========================
# HTTP (Retry + Backoff)
# =========================

def geodnet_headers() -> Dict[str, str]:
    # Keep consistent with your existing working code.
    # If your yesterday code used different header keys, adjust here.
    headers = {
        "Content-Type": "application/json",
    }
    if GEODNET_TOKEN:
        headers["Authorization"] = f"Bearer {GEODNET_TOKEN}"
    if GEODNET_CLIENT_ID:
        headers["X-Client-Id"] = GEODNET_CLIENT_ID
    return headers

def post_with_retry(url: str, payload: Dict[str, Any], cfg: ThrottleConfig) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Returns: (ok, json, err_msg)
    Retries on network errors / 429 / 5xx.
    """
    headers = geodnet_headers()

    last_err = ""
    for attempt in range(1, cfg.max_retries + 1):
        try:
            r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=cfg.timeout_sec)

            # Retryable statuses
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                backoff = (cfg.base_backoff_sec ** attempt)
                sleep_with_jitter(backoff, cfg.jitter_sec)
                continue

            # Non-OK
            if not (200 <= r.status_code < 300):
                return False, None, f"HTTP {r.status_code}: {r.text[:400]}"

            # Parse JSON
            try:
                return True, r.json(), ""
            except Exception:
                return False, None, "JSON parse error"

        except requests.RequestException as e:
            last_err = str(e)
            backoff = (cfg.base_backoff_sec ** attempt)
            sleep_with_jitter(backoff, cfg.jitter_sec)

    return False, None, f"Retry failed: {last_err}"

# =========================
# Business Logic: Rewards + Online/Offline
# =========================

def fetch_rewards_timeline(sn: str, start_iso: str, end_iso: str, cfg: ThrottleConfig) -> Tuple[bool, float, str]:
    """
    Fetch total GEOD rewards for the SN in given window.
    Replace payload keys to match your working implementation if needed.
    """
    payload = {
        "sn": sn,
        "startTime": start_iso,
        "endTime": end_iso,
    }

    ok, js, err = post_with_retry(REWARDS_TIMELINE_ENDPOINT, payload, cfg)
    if not ok:
        return False, 0.0, err

    # Adjust parsing to match your API response shape from yesterday
    # Example patterns:
    #   js["data"] = [{"reward": 1.23}, ...]
    #   js["totalReward"] = 12.3
    total = 0.0

    try:
        if isinstance(js, dict) and "totalReward" in js:
            total = safe_float(js.get("totalReward", 0.0), 0.0)
        elif isinstance(js, dict) and "data" in js and isinstance(js["data"], list):
            for row in js["data"]:
                total += safe_float(row.get("reward", 0.0), 0.0)
        else:
            # fallback
            total = safe_float(js.get("reward", 0.0), 0.0)
    except Exception:
        total = 0.0

    return True, total, ""

def fetch_miner_status(sn: str, lookback_hours: int, cfg: ThrottleConfig) -> Tuple[bool, str, str, Optional[str]]:
    """
    Returns: (ok, status, err, last_seen_iso)
    status: "online" | "offline" | "unknown"
    - lookback_hours enlarged to reduce missed offline devices when one poll fails
    Replace payload/parse to match your existing minerstatus query.

    Many APIs expose either:
      - lastSeen timestamp
      - online boolean
      - status string
    We'll try to infer from common fields.
    """
    end_t = _now_utc()
    start_t = end_t - dt.timedelta(hours=int(lookback_hours))

    payload = {
        "sn": sn,
        "startTime": _to_iso_z(start_t),
        "endTime": _to_iso_z(end_t),
    }

    ok, js, err = post_with_retry(MINER_STATUS_ENDPOINT, payload, cfg)
    if not ok:
        return False, "unknown", err, None

    # ---- Parse flexibly (adjust to your exact schema if needed)
    status = "unknown"
    last_seen = None

    try:
        # Example 1: { data: { online: true, lastSeen: "..."} }
        data = js.get("data", js)

        if isinstance(data, dict):
            if "online" in data:
                status = "online" if bool(data.get("online")) else "offline"
            elif "status" in data:
                s = str(data.get("status")).lower()
                if "on" in s:
                    status = "online"
                elif "off" in s:
                    status = "offline"
            if "lastSeen" in data:
                last_seen = str(data.get("lastSeen"))
            elif "last_seen" in data:
                last_seen = str(data.get("last_seen"))

        # Example 2: { data: [ {ts, online}, ... ] } -> take latest
        if status == "unknown" and isinstance(js.get("data"), list) and js["data"]:
            last = js["data"][-1]
            if isinstance(last, dict):
                if "online" in last:
                    status = "online" if bool(last.get("online")) else "offline"
                if "ts" in last:
                    last_seen = str(last.get("ts"))
                if "time" in last and not last_seen:
                    last_seen = str(last.get("time"))

    except Exception:
        status = "unknown"

    return True, status, "", last_seen

# =========================
# Excel / Manual SN input
# =========================

def load_sn_table(uploaded_file) -> pd.DataFrame:
    df = pd.read_excel(uploaded_file)
    # Smart column match: try to find SN column automatically
    cols = {c.lower(): c for c in df.columns}
    sn_col = None
    for key in ["sn", "serial", "serialno", "serial_no", "seri", "seri no", "serial number", "device_sn"]:
        if key in cols:
            sn_col = cols[key]
            break
    if sn_col is None:
        # fallback: first column
        sn_col = df.columns[0]

    df = df.copy()
    df["SN"] = df[sn_col].astype(str).map(normalize_sn)

    # optional: partner / city / location mapping (keep your yesterday fields if you have them)
    def find_col(cands):
        for k in cands:
            if k in cols:
                return cols[k]
        return None

    partner_col = find_col(["iş ortağı", "is ortagi", "partner", "bayi", "reseller"])
    city_col = find_col(["il", "şehir", "sehir", "city"])
    loc_col = find_col(["konum", "lokasyon", "location", "adres", "address"])

    if partner_col:
        df["İş Ortağı"] = df[partner_col].astype(str)
    else:
        df["İş Ortağı"] = ""

    if city_col:
        df["İl"] = df[city_col].astype(str)
    else:
        df["İl"] = ""

    if loc_col:
        df["Konum"] = df[loc_col].astype(str)
    else:
        df["Konum"] = ""

    # de-dup and drop empty SN
    df = df[df["SN"].str.len() > 0].drop_duplicates(subset=["SN"]).reset_index(drop=True)
    return df[["SN", "İş Ortağı", "İl", "Konum"]]

# =========================
# Session State
# =========================

if "last_scan_at" not in st.session_state:
    st.session_state.last_scan_at = None
if "status_cache" not in st.session_state:
    st.session_state.status_cache = {}  # SN -> dict(status,last_seen,checked_at,err)
if "offline_list" not in st.session_state:
    st.session_state.offline_list = pd.DataFrame(columns=["SN", "İş Ortağı", "İl", "Konum", "Durum", "LastSeen", "Hata", "KontrolZamanı"])

# =========================
# Sidebar Controls
# =========================

st.sidebar.header("Ayarlar")

cfg = ThrottleConfig(
    per_device_delay_sec=float(st.sidebar.slider("Cihaz başı gecikme (sn)", 0.0, 10.0, 2.5, 0.5)),
    max_retries=int(st.sidebar.slider("Retry sayısı", 0, 6, 3, 1)),
    base_backoff_sec=float(st.sidebar.slider("Backoff katsayısı", 1.0, 2.5, 1.2, 0.1)),
    jitter_sec=float(st.sidebar.slider("Jitter (sn)", 0.0, 1.0, 0.35, 0.05)),
    timeout_sec=float(st.sidebar.slider("HTTP timeout (sn)", 5.0, 60.0, 20.0, 1.0)),
)

lookback_hours = int(st.sidebar.selectbox("Online/Offline lookback (saat)", [12, 24, 48, 72, 96], index=3))
st.sidebar.caption("Lookback büyüdükçe kısa süreli API hatalarında 'offline kaçırma' azalır.")

st.sidebar.divider()
st.sidebar.subheader("Kimlik Bilgileri")
st.sidebar.write(f"GEODNET_CLIENT_ID: {'✅' if GEODNET_CLIENT_ID else '❌ (env)'}")
st.sidebar.write(f"GEODNET_TOKEN: {'✅' if GEODNET_TOKEN else '❌ (env)'}")
if not GEODNET_CLIENT_ID or not GEODNET_TOKEN:
    st.sidebar.warning("Env değişkenleri eksik: GEODNET_CLIENT_ID ve GEODNET_TOKEN")

# =========================
# Input Area
# =========================

tab1, tab2 = st.tabs(["Offline Takip", "Token Hesaplama (opsiyonel)"])

with tab1:
    st.subheader("Online / Offline Cihaz Listesi (Sağlamlaştırılmış)")

    colA, colB = st.columns([1.2, 1])

    with colA:
        uploaded = st.file_uploader("Excel yükle (SN + opsiyonel İş Ortağı / İl / Konum)", type=["xlsx", "xls"])
        manual_sn_text = st.text_area("Veya manuel SN listesi (her satıra 1 SN)", height=120)

    with colB:
        st.markdown("**Kontrol**")
        scan_btn = st.button("🔄 Online/Offline Taraması Yap", type="primary", use_container_width=True)
        use_throttle = st.checkbox("Cihaz başı gecikme uygula", value=True)
        clear_cache = st.button("🧹 Cache temizle", use_container_width=True)

        if clear_cache:
            st.session_state.status_cache = {}
            st.session_state.offline_list = st.session_state.offline_list.iloc[0:0]
            st.session_state.last_scan_at = None
            st.success("Cache temizlendi.")

    # Build device table
    devices_df = pd.DataFrame(columns=["SN", "İş Ortağı", "İl", "Konum"])
    if uploaded is not None:
        try:
            devices_df = load_sn_table(uploaded)
        except Exception as e:
            st.error(f"Excel okunamadı: {e}")

    manual_sns = []
    if manual_sn_text.strip():
        manual_sns = [normalize_sn(x) for x in manual_sn_text.splitlines() if normalize_sn(x)]
        if manual_sns:
            md = pd.DataFrame({"SN": manual_sns, "İş Ortağı": "", "İl": "", "Konum": ""})
            devices_df = pd.concat([devices_df, md], ignore_index=True)

    devices_df = devices_df.drop_duplicates(subset=["SN"]).reset_index(drop=True)

    if len(devices_df) == 0:
        st.info("Tarama için Excel yükle veya manuel SN gir.")
    else:
        st.write(f"Toplam cihaz: **{len(devices_df)}**")

    # Scan
    if scan_btn and len(devices_df) > 0:
        progress = st.progress(0, text="Tarama başlıyor...")
        log_box = st.empty()

        offline_rows = []
        ok_count = 0
        fail_count = 0

        for i, row in devices_df.iterrows():
            sn = row["SN"]
            progress.progress((i) / max(1, len(devices_df)), text=f"Kontrol ediliyor: {sn} ({i+1}/{len(devices_df)})")

            # ---- call status with retry/backoff
            ok, status, err, last_seen = fetch_miner_status(sn, lookback_hours=lookback_hours, cfg=cfg)

            checked_at = _to_iso_z(_now_utc())
            st.session_state.status_cache[sn] = {
                "status": status if ok else "unknown",
                "last_seen": last_seen,
                "checked_at": checked_at,
                "err": err if not ok else "",
            }

            if ok:
                ok_count += 1
            else:
                fail_count += 1

            if status == "offline":
                offline_rows.append({
                    "SN": sn,
                    "İş Ortağı": row.get("İş Ortağı", ""),
                    "İl": row.get("İl", ""),
                    "Konum": row.get("Konum", ""),
                    "Durum": status,
                    "LastSeen": last_seen or "",
                    "Hata": "",
                    "KontrolZamanı": checked_at
                })
            elif not ok:
                # Keep unknowns as well, so you can see what was missed due to errors
                offline_rows.append({
                    "SN": sn,
                    "İş Ortağı": row.get("İş Ortağı", ""),
                    "İl": row.get("İl", ""),
                    "Konum": row.get("Konum", ""),
                    "Durum": "unknown",
                    "LastSeen": last_seen or "",
                    "Hata": err,
                    "KontrolZamanı": checked_at
                })

            log_box.caption(f"OK: {ok_count} | Fail: {fail_count} | Offline/Unknown listede: {len(offline_rows)}")

            # ---- throttle between devices
            if use_throttle and i < len(devices_df) - 1:
                sleep_with_jitter(cfg.per_device_delay_sec, cfg.jitter_sec)

        progress.progress(1.0, text="Tarama bitti ✅")
        st.session_state.last_scan_at = _to_iso_z(_now_utc())

        st.session_state.offline_list = pd.DataFrame(offline_rows)
        st.success(f"Tarama tamamlandı. OK={ok_count}, Fail={fail_count}, Offline/Unknown={len(offline_rows)}")

    # Display results (no rescan)
    st.divider()
    meta_cols = st.columns([1, 1, 2])
    meta_cols[0].metric("Son tarama", st.session_state.last_scan_at or "-")
    meta_cols[1].metric("Cache kayıt sayısı", len(st.session_state.status_cache))
    meta_cols[2].caption("‘Offline minerları listele’ görünümü tekrar tarama yapmaz; son tarama sonuçlarını gösterir.")

    st.subheader("Offline / Unknown Cihazlar")
    if st.session_state.offline_list is None or st.session_state.offline_list.empty:
        st.info("Henüz sonuç yok. Tarama başlat.")
    else:
        # show offline first, then unknown
        df_show = st.session_state.offline_list.copy()
        df_show["__rank"] = df_show["Durum"].map({"offline": 0, "unknown": 1}).fillna(9)
        df_show = df_show.sort_values(["__rank", "İş Ortağı", "İl", "SN"]).drop(columns=["__rank"])
        st.dataframe(df_show, use_container_width=True, height=420)

        # export
        out_xlsx = df_show.copy()
        st.download_button(
            "⬇️ Offline/Unknown Excel indir",
            data=out_xlsx.to_csv(index=False).encode("utf-8-sig"),
            file_name="geod_offline_unknown.csv",
            mime="text/csv",
            use_container_width=True
        )

with tab2:
    st.subheader("Token Hesaplama (opsiyonel)")
    st.caption("Bu bölüm dünkü uygulamada vardı; istersen aynı throttle/retry mantığıyla devam eder.")

    days = st.number_input("Hesaplama aralığı (gün)", min_value=1, max_value=365, value=30, step=1)
    calc_btn = st.button("🧮 Seçili cihazlar için rewards hesapla", use_container_width=True)

    if calc_btn:
        # Use the same devices_df from above (Excel/manual)
        if 'devices_df' not in locals() or devices_df is None or devices_df.empty:
            st.warning("Önce Offline Takip sekmesinde Excel yükle veya manuel SN gir.")
        else:
            end_t = _now_utc()
            start_t = end_t - dt.timedelta(days=int(days))
            start_iso = _to_iso_z(start_t)
            end_iso = _to_iso_z(end_t)

            progress2 = st.progress(0, text="Rewards hesaplanıyor...")
            rows = []
            for i, row in devices_df.iterrows():
                sn = row["SN"]
                progress2.progress((i) / max(1, len(devices_df)), text=f"Rewards: {sn} ({i+1}/{len(devices_df)})")
                ok, total, err = fetch_rewards_timeline(sn, start_iso, end_iso, cfg)
                rows.append({
                    "SN": sn,
                    "İş Ortağı": row.get("İş Ortağı", ""),
                    "İl": row.get("İl", ""),
                    "Konum": row.get("Konum", ""),
                    f"{days}g_GEOD": total if ok else 0.0,
                    "Hata": "" if ok else err,
                })

                if use_throttle and i < len(devices_df) - 1:
                    sleep_with_jitter(cfg.per_device_delay_sec, cfg.jitter_sec)

            progress2.progress(1.0, text="Bitti ✅")
            df_rewards = pd.DataFrame(rows)
            st.dataframe(df_rewards, use_container_width=True, height=420)
            st.download_button(
                "⬇️ Rewards CSV indir",
                data=df_rewards.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"geod_rewards_{days}d.csv",
                mime="text/csv",
                use_container_width=True
            )

st.divider()
st.caption("Not: Offline kaçırma genelde geçici HTTP hataları + rate limit + dar zaman penceresi kombinasyonundan olur. Bu patch üçüne birden çözüm getirir.")
