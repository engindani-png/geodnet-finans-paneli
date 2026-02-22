# app.py
# MonsPro | GEOD Token Hesaplama Aracı (Streamlit)
# - GEODNET rewards timeline fetch (AES-CBC encrypted params)
# - 30-day chunking
# - Excel auto column mapping
# - TR 08:30 payout cut-off fix (performance-day aligned)
#
# Env vars required:
#   GEODNET_CLIENT_ID
#   GEODNET_TOKEN
#   GEODNET_AES_KEY   (hex OR base64 OR raw; see parse_key_iv)
#   GEODNET_AES_IV    (hex OR base64 OR raw; 16 bytes)
# Optional:
#   GEODNET_BASE_URL  (default: https://console-api.geodnet.com)  # change if yours differs
#   EXCHANGERATE_API_KEY (if you use exchangerate-api.com)
#
# pip install streamlit pandas numpy requests python-dotenv pycryptodome fpdf2 openpyxl

import os
import io
import re
import json
import time
import base64
import hashlib
import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import streamlit as st
from fpdf import FPDF

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


# ----------------------------
# Config
# ----------------------------
APP_TITLE = "MonsPro | GEOD Token Hesaplama Aracı"
TR_TZ = dt.timezone(dt.timedelta(hours=3))
PAYOUT_CUTOFF_TR = dt.time(hour=8, minute=30)  # 08:30 TR

DEFAULT_BASE_URL = os.getenv("GEODNET_BASE_URL", "https://console-api.geodnet.com")
GEODNET_CLIENT_ID = os.getenv("GEODNET_CLIENT_ID", "").strip()
GEODNET_TOKEN = os.getenv("GEODNET_TOKEN", "").strip()
EXCHANGERATE_API_KEY = os.getenv("EXCHANGERATE_API_KEY", "").strip()

st.set_page_config(page_title=APP_TITLE, layout="wide")


# ----------------------------
# Helpers: Key/IV parsing
# ----------------------------
def _try_b64(s: str) -> Optional[bytes]:
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        return None

def _try_hex(s: str) -> Optional[bytes]:
    try:
        s2 = s.strip().lower()
        if s2.startswith("0x"):
            s2 = s2[2:]
        if re.fullmatch(r"[0-9a-f]+", s2) and len(s2) % 2 == 0:
            return bytes.fromhex(s2)
        return None
    except Exception:
        return None

def parse_key_iv(key_s: str, iv_s: str) -> Tuple[bytes, bytes]:
    """
    Accept key/iv in:
      - hex
      - base64
      - raw string (utf-8)
    Key must be 16/24/32 bytes; IV must be 16 bytes.
    """
    if not key_s or not iv_s:
        raise ValueError("GEODNET_AES_KEY ve GEODNET_AES_IV env değişkenleri gerekli.")

    key_b = _try_hex(key_s) or _try_b64(key_s) or key_s.encode("utf-8")
    iv_b = _try_hex(iv_s) or _try_b64(iv_s) or iv_s.encode("utf-8")

    if len(iv_b) != 16:
        raise ValueError(f"AES IV 16 byte olmalı. Şu an: {len(iv_b)} byte")
    if len(key_b) not in (16, 24, 32):
        raise ValueError(f"AES key 16/24/32 byte olmalı. Şu an: {len(key_b)} byte")

    return key_b, iv_b


# ----------------------------
# GEODNET API (AES-CBC params)
# ----------------------------
def aes_cbc_encrypt_to_b64(payload: dict, key: bytes, iv: bytes) -> str:
    """
    Encrypt JSON payload with AES-CBC and PKCS#7 padding.
    Return base64 string.
    """
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    enc = cipher.encrypt(pad(raw, AES.block_size))
    return base64.b64encode(enc).decode("utf-8")

@dataclass
class RewardRow:
    miner_sn: str
    payout_ts: dt.datetime  # timezone-aware (TR)
    geod: float

def geodnet_call_rewards_timeline(
    base_url: str,
    miner_sn: str,
    start_utc_ms: int,
    end_utc_ms: int,
    key: bytes,
    iv: bytes,
    token: str,
    client_id: str,
    timeout: int = 30,
) -> List[RewardRow]:
    """
    Calls getRewardsTimeLine with encrypted params.
    This is the part that differs across implementations.
    If your endpoint/path differs, adjust here only.
    """
    # Payload shape: keep minimal + compatible
    params_payload = {
        "minerSn": miner_sn,
        "startTime": start_utc_ms,
        "endTime": end_utc_ms,
    }
    enc_params = aes_cbc_encrypt_to_b64(params_payload, key, iv)

    url = f"{base_url.rstrip('/')}/console/v1/miner/getRewardsTimeLine"
    headers = {
        "Authorization": token,
        "clientId": client_id,
        "Content-Type": "application/json",
    }

    # Some deployments expect params in body; some expect query.
    # We'll send in body as {"params": "<b64>"} (commonly used pattern).
    payload = {"params": enc_params}

    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"GEODNET API HTTP {r.status_code}: {r.text[:500]}")

    data = r.json()
    # We try multiple common shapes:
    # - {"code":0,"data":[{"time":...,"reward":...},...]}
    # - {"success":true,"data":{"list":[...]}}
    # - {"data":{"rewards":[...]}}
    if isinstance(data, dict) and data.get("code") not in (None, 0) and data.get("success") is not True:
        # If server uses code semantics:
        raise RuntimeError(f"GEODNET API error: {json.dumps(data)[:500]}")

    items = None
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            items = data["data"]
        elif isinstance(data.get("data"), dict):
            d = data["data"]
            items = d.get("list") or d.get("rewards") or d.get("items")
        elif isinstance(data.get("result"), list):
            items = data["result"]

    if items is None:
        # Fallback: if already list
        if isinstance(data, list):
            items = data
        else:
            raise RuntimeError(f"Beklenmeyen response formatı: {str(data)[:500]}")

    out: List[RewardRow] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # time fields can be ms or seconds; try common keys
        t = it.get("time") or it.get("timestamp") or it.get("payoutTime") or it.get("dateTime")
        geod = it.get("reward") or it.get("geod") or it.get("amount") or it.get("value")
        if t is None or geod is None:
            continue

        # normalize time
        try:
            t = int(t)
        except Exception:
            continue
        if t < 10_000_000_000:  # seconds
            t_ms = t * 1000
        else:
            t_ms = t

        # convert to TR tz-aware datetime
        payout_utc = dt.datetime.fromtimestamp(t_ms / 1000.0, tz=dt.timezone.utc)
        payout_tr = payout_utc.astimezone(TR_TZ)

        try:
            geod_f = float(geod)
        except Exception:
            continue

        out.append(RewardRow(miner_sn=miner_sn, payout_ts=payout_tr, geod=geod_f))
    return out


# ----------------------------
# Date logic (TR 08:30 cutoff fix)
# ----------------------------
def performance_window_to_payout_filter(start_date: dt.date, end_date: dt.date) -> Tuple[dt.datetime, dt.datetime]:
    """
    User selects [start_date, end_date] as performance days (local TR calendar).
    Rewards are paid daily at ~08:30 TR for previous day's performance.

    We filter payout timestamps in [start_date 08:30, (end_date + 1 day) 08:30).

    Example:
      performance: 2026-02-01 .. 2026-02-10
      payout window filter: 2026-02-01 08:30 .. 2026-02-11 08:30 (exclusive)
    """
    start_dt = dt.datetime.combine(start_date, PAYOUT_CUTOFF_TR, tzinfo=TR_TZ)
    end_dt_excl = dt.datetime.combine(end_date + dt.timedelta(days=1), PAYOUT_CUTOFF_TR, tzinfo=TR_TZ)
    return start_dt, end_dt_excl

def dt_to_utc_ms(d: dt.datetime) -> int:
    if d.tzinfo is None:
        raise ValueError("datetime tz-aware olmalı")
    return int(d.astimezone(dt.timezone.utc).timestamp() * 1000)


# ----------------------------
# Chunked fetch
# ----------------------------
def chunk_ranges(start_dt: dt.datetime, end_dt_excl: dt.datetime, chunk_days: int = 30) -> List[Tuple[dt.datetime, dt.datetime]]:
    """
    Return list of [chunk_start, chunk_end_excl] ranges, each up to chunk_days.
    """
    ranges = []
    cur = start_dt
    while cur < end_dt_excl:
        nxt = min(cur + dt.timedelta(days=chunk_days), end_dt_excl)
        ranges.append((cur, nxt))
        cur = nxt
    return ranges

@st.cache_data(ttl=600, show_spinner=False)
def get_prices_cached() -> Dict[str, float]:
    """
    Cache prices for 10 minutes.
    """
    out = {"GEOD_USD": np.nan, "USD_TRY": np.nan}

    # GEOD/USD via CoinGecko
    try:
        cg = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "geodnet", "vs_currencies": "usd"},
            timeout=15,
        )
        if cg.status_code == 200:
            out["GEOD_USD"] = float(cg.json()["geodnet"]["usd"])
    except Exception:
        pass

    # USD/TRY via exchangerate-api.com (or fallback to frankfurter if key missing)
    if EXCHANGERATE_API_KEY:
        try:
            ex = requests.get(
                f"https://v6.exchangerate-api.com/v6/{EXCHANGERATE_API_KEY}/latest/USD",
                timeout=15,
            )
            if ex.status_code == 200:
                out["USD_TRY"] = float(ex.json()["conversion_rates"]["TRY"])
        except Exception:
            pass

    if not np.isfinite(out["USD_TRY"]):
        # fallback
        try:
            fx = requests.get(
                "https://api.frankfurter.app/latest",
                params={"from": "USD", "to": "TRY"},
                timeout=15,
            )
            if fx.status_code == 200:
                out["USD_TRY"] = float(fx.json()["rates"]["TRY"])
        except Exception:
            pass

    return out


def safe_float(x, default=np.nan) -> float:
    try:
        return float(x)
    except Exception:
        return default


# ----------------------------
# Excel parsing + auto mapping
# ----------------------------
def normalize_colname(s: str) -> str:
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    # Turkish chars normalize
    tr_map = str.maketrans({"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"})
    s = s.translate(tr_map)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s

def pick_best_column(cols: List[str], keywords: List[str]) -> Optional[str]:
    """
    Choose the column that matches most keywords (substring match).
    """
    scores = []
    for c in cols:
        nc = normalize_colname(c)
        score = 0
        for kw in keywords:
            if kw in nc:
                score += 1
        scores.append((score, c))
    scores.sort(reverse=True, key=lambda x: x[0])
    if scores and scores[0][0] > 0:
        return scores[0][1]
    return None

def auto_map_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    cols = list(df.columns)

    # SN / Miner Serial
    sn_col = pick_best_column(cols, ["sn", "serial", "seri", "miner", "device", "cihaz"])
    # Partner / İş Ortağı
    partner_col = pick_best_column(cols, ["is ortagi", "partner", "bayi", "distributor", "musteri", "firma"])
    # City / İl
    il_col = pick_best_column(cols, ["il", "sehir", "city", "province"])
    # Location / Konum
    konum_col = pick_best_column(cols, ["konum", "lokasyon", "location", "adres", "address", "bolge", "region"])

    return {
        "sn": sn_col,
        "partner": partner_col,
        "il": il_col,
        "konum": konum_col,
    }

def load_excel(file_bytes: bytes) -> pd.DataFrame:
    bio = io.BytesIO(file_bytes)
    df = pd.read_excel(bio, engine="openpyxl")
    # drop completely empty rows
    df = df.dropna(how="all")
    # ensure columns are strings
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ----------------------------
# PDF
# ----------------------------
class SimplePDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "MonsPro | GEOD Raporu", ln=True, align="L")
        self.ln(2)

def df_to_pdf_bytes(df: pd.DataFrame, title: str, meta: Dict[str, str]) -> bytes:
    pdf = SimplePDF(orientation="L", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=10)

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, title, ln=True)
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 9)
    for k, v in meta.items():
        pdf.cell(0, 5, f"{k}: {v}", ln=True)
    pdf.ln(3)

    # Table
    # Limit columns for readability; keep important ones
    keep_cols = [c for c in df.columns if c][:12]
    table = df[keep_cols].copy()

    pdf.set_font("Helvetica", "B", 8)
    col_widths = []
    for c in keep_cols:
        w = max(18, min(45, 6 + len(str(c)) * 2.2))
        col_widths.append(w)

    for i, c in enumerate(keep_cols):
        pdf.cell(col_widths[i], 6, str(c)[:40], border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for _, row in table.iterrows():
        for i, c in enumerate(keep_cols):
            txt = str(row[c]) if pd.notna(row[c]) else ""
            pdf.cell(col_widths[i], 6, txt[:40], border=1)
        pdf.ln()

    return pdf.output(dest="S").encode("latin1")


# ----------------------------
# Core computation
# ----------------------------
def compute_rewards_for_miners(
    miner_sns: List[str],
    start_date: dt.date,
    end_date: dt.date,
    base_url: str,
    key: bytes,
    iv: bytes,
    token: str,
    client_id: str,
    show_progress: bool = True,
) -> pd.DataFrame:
    """
    Fetch rewards, apply TR 08:30 cutoff filter, aggregate per miner.
    """
    payout_start, payout_end_excl = performance_window_to_payout_filter(start_date, end_date)

    # Chunk ranges (UTC ms for API)
    ranges = chunk_ranges(payout_start, payout_end_excl, chunk_days=30)

    rows: List[RewardRow] = []
    total_steps = max(1, len(miner_sns) * max(1, len(ranges)))
    step = 0

    prog = st.progress(0) if show_progress else None
    status = st.empty() if show_progress else None

    for sn in miner_sns:
        for (rs, re_) in ranges:
            step += 1
            if show_progress:
                prog.progress(min(step / total_steps, 1.0))
                status.caption(f"Sorgulanıyor: {sn} | {rs.date()} → {re_.date()} ({step}/{total_steps})")

            part = geodnet_call_rewards_timeline(
                base_url=base_url,
                miner_sn=sn,
                start_utc_ms=dt_to_utc_ms(rs),
                end_utc_ms=dt_to_utc_ms(re_),
                key=key,
                iv=iv,
                token=token,
                client_id=client_id,
            )
            rows.extend(part)

            # gentle pacing (avoid rate limit spikes)
            time.sleep(0.05)

    # Apply strict payout window filter (TR tz)
    filtered = [r for r in rows if (r.payout_ts >= payout_start and r.payout_ts < payout_end_excl)]

    if show_progress:
        prog.progress(1.0)
        status.caption("Tamamlandı.")
        time.sleep(0.2)

    if not filtered:
        return pd.DataFrame(columns=["Miner_SN", "Toplam_GEOD", "Odul_Sayisi", "Ilk_Odul_TR", "Son_Odul_TR"])

    df = pd.DataFrame([{
        "Miner_SN": r.miner_sn,
        "Payout_TR": r.payout_ts,
        "GEOD": r.geod
    } for r in filtered])

    agg = df.groupby("Miner_SN", as_index=False).agg(
        Toplam_GEOD=("GEOD", "sum"),
        Odul_Sayisi=("GEOD", "count"),
        Ilk_Odul_TR=("Payout_TR", "min"),
        Son_Odul_TR=("Payout_TR", "max"),
    )
    agg["Toplam_GEOD"] = agg["Toplam_GEOD"].round(6)
    return agg


# ----------------------------
# UI
# ----------------------------
def sidebar_auth_state():
    st.sidebar.subheader("Yetkilendirme / Ayarlar")
    st.sidebar.write("ENV’den okunur: `GEODNET_CLIENT_ID`, `GEODNET_TOKEN`, `GEODNET_AES_KEY`, `GEODNET_AES_IV`")

    base_url = st.sidebar.text_input("GEODNET Base URL", value=DEFAULT_BASE_URL)

    # show env presence
    st.sidebar.caption(f"CLIENT_ID: {'✅' if GEODNET_CLIENT_ID else '❌'}")
    st.sidebar.caption(f"TOKEN: {'✅' if GEODNET_TOKEN else '❌'}")

    aes_key = os.getenv("GEODNET_AES_KEY", "")
    aes_iv = os.getenv("GEODNET_AES_IV", "")
    st.sidebar.caption(f"AES_KEY: {'✅' if aes_key else '❌'}")
    st.sidebar.caption(f"AES_IV: {'✅' if aes_iv else '❌'}")

    return base_url


def main():
    st.title(APP_TITLE)

    base_url = sidebar_auth_state()

    # Prices
    prices = get_prices_cached()
    geod_usd = prices.get("GEOD_USD", np.nan)
    usd_try = prices.get("USD_TRY", np.nan)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("GEOD / USD", "—" if not np.isfinite(geod_usd) else f"{geod_usd:.6f}")
    c2.metric("USD / TRY", "—" if not np.isfinite(usd_try) else f"{usd_try:.4f}")
    c3.metric("Payout Cut-off (TR)", "08:30")
    c4.metric("Zaman Dilimi", "Europe/Istanbul (UTC+3)")

    st.divider()

    st.subheader("1) Tarih Aralığı (Performans Günleri)")
    colA, colB, colC = st.columns([1, 1, 2])

    today_tr = dt.datetime.now(TR_TZ).date()
    default_end = today_tr - dt.timedelta(days=1)  # yesterday performance ends
    default_start = default_end - dt.timedelta(days=29)

    start_date = colA.date_input("Başlangıç (performans)", value=default_start)
    end_date = colB.date_input("Bitiş (performans)", value=default_end)

    payout_start, payout_end_excl = performance_window_to_payout_filter(start_date, end_date)
    colC.info(
        f"Bu seçim **payout timestamp** filtrelemesiyle yapılır:\n\n"
        f"- Dahil: {payout_start.strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"- Hariç: {payout_end_excl.strftime('%Y-%m-%d %H:%M %Z')}\n\n"
        f"Yani **başlangıç gününü + önceki gün ödülünü** yanlışlıkla dahil etme problemi çözülür."
    )

    st.subheader("2) Cihaz Listesi (Excel + Manuel)")
    left, right = st.columns([1.2, 1])

    if "miner_df" not in st.session_state:
        st.session_state.miner_df = pd.DataFrame(columns=["Miner_SN", "Is_Ortagi", "Il", "Konum"])
    if "offline_only_cache" not in st.session_state:
        st.session_state.offline_only_cache = None  # to avoid rescan when user filters

    with left:
        up = st.file_uploader("Excel yükle (.xlsx)", type=["xlsx"])
        if up is not None:
            raw_df = load_excel(up.read())
            mapping = auto_map_columns(raw_df)

            st.caption("Otomatik sütun eşleme (gerekirse Excel başlıklarını sadeleştir):")
            mc1, mc2, mc3, mc4 = st.columns(4)
            sn_col = mc1.selectbox("SN sütunu", options=[None] + list(raw_df.columns), index=(0 if mapping["sn"] is None else (1 + list(raw_df.columns).index(mapping["sn"])) ))
            partner_col = mc2.selectbox("İş Ortağı", options=[None] + list(raw_df.columns), index=(0 if mapping["partner"] is None else (1 + list(raw_df.columns).index(mapping["partner"])) ))
            il_col = mc3.selectbox("İl", options=[None] + list(raw_df.columns), index=(0 if mapping["il"] is None else (1 + list(raw_df.columns).index(mapping["il"])) ))
            konum_col = mc4.selectbox("Konum", options=[None] + list(raw_df.columns), index=(0 if mapping["konum"] is None else (1 + list(raw_df.columns).index(mapping["konum"])) ))

            if sn_col is None:
                st.error("SN sütunu seçmeden devam edemem.")
            else:
                df = pd.DataFrame()
                df["Miner_SN"] = raw_df[sn_col].astype(str).str.strip()
                df["Is_Ortagi"] = raw_df[partner_col].astype(str).str.strip() if partner_col else ""
                df["Il"] = raw_df[il_col].astype(str).str.strip() if il_col else ""
                df["Konum"] = raw_df[konum_col].astype(str).str.strip() if konum_col else ""

                # drop blanks
                df = df[df["Miner_SN"].replace({"nan": "", "None": ""}).astype(str).str.len() > 0].copy()
                # de-dup
                df = df.drop_duplicates(subset=["Miner_SN"], keep="first")

                st.session_state.miner_df = df.reset_index(drop=True)
                st.success(f"Yüklendi: {len(df)} cihaz")

        st.dataframe(st.session_state.miner_df, use_container_width=True, height=260)

    with right:
        st.caption("Manuel SN ekle")
        new_sn = st.text_input("SN", placeholder="ör: GEOD-XXXX...")
        new_partner = st.text_input("İş Ortağı (opsiyonel)")
        new_il = st.text_input("İl (opsiyonel)")
        new_konum = st.text_input("Konum (opsiyonel)")
        if st.button("SN Ekle", use_container_width=True):
            sn = (new_sn or "").strip()
            if not sn:
                st.warning("SN boş olamaz.")
            else:
                df = st.session_state.miner_df.copy()
                if (df["Miner_SN"] == sn).any():
                    st.info("Bu SN zaten listede.")
                else:
                    df = pd.concat([df, pd.DataFrame([{
                        "Miner_SN": sn,
                        "Is_Ortagi": (new_partner or "").strip(),
                        "Il": (new_il or "").strip(),
                        "Konum": (new_konum or "").strip(),
                    }])], ignore_index=True)
                    st.session_state.miner_df = df

        st.caption("Hedef TL tamamlama")
        target_tl = st.number_input("Hedef (TL)", min_value=0.0, value=0.0, step=100.0)
        low_threshold = st.number_input("Düşük üretim eşiği (GEOD)", min_value=0.0, value=180.0, step=10.0)

    st.divider()

    st.subheader("3) Hesapla")
    colX, colY, colZ = st.columns([1, 1, 2])

    do_calc = colX.button("Ödülleri Çek ve Hesapla", type="primary", use_container_width=True)
    refresh_prices = colY.button("Fiyatları Yenile", use_container_width=True)

    if refresh_prices:
        get_prices_cached.clear()
        st.experimental_rerun()

    if do_calc:
        if not GEODNET_CLIENT_ID or not GEODNET_TOKEN:
            st.error("GEODNET_CLIENT_ID ve GEODNET_TOKEN env değişkenleri eksik.")
            st.stop()

        try:
            key, iv = parse_key_iv(os.getenv("GEODNET_AES_KEY", ""), os.getenv("GEODNET_AES_IV", ""))
        except Exception as e:
            st.error(str(e))
            st.stop()

        miner_df = st.session_state.miner_df.copy()
        miner_sns = miner_df["Miner_SN"].astype(str).str.strip().tolist()
        if not miner_sns:
            st.warning("Önce Excel yükle veya manuel SN ekle.")
            st.stop()

        with st.spinner("Ödüller çekiliyor..."):
            agg = compute_rewards_for_miners(
                miner_sns=miner_sns,
                start_date=start_date,
                end_date=end_date,
                base_url=base_url,
                key=key,
                iv=iv,
                token=GEODNET_TOKEN,
                client_id=GEODNET_CLIENT_ID,
                show_progress=True,
            )

        # merge back metadata
        out = miner_df.merge(agg, how="left", left_on="Miner_SN", right_on="Miner_SN")
        out["Toplam_GEOD"] = out["Toplam_GEOD"].fillna(0.0)
        out["Odul_Sayisi"] = out["Odul_Sayisi"].fillna(0).astype(int)

        # Money conversions
        geod_usd = get_prices_cached().get("GEOD_USD", np.nan)
        usd_try = get_prices_cached().get("USD_TRY", np.nan)

        out["GEOD_USD"] = geod_usd
        out["USD_TRY"] = usd_try

        out["Toplam_USD"] = np.where(np.isfinite(geod_usd), out["Toplam_GEOD"] * geod_usd, np.nan)
        out["Toplam_TL"] = np.where(np.isfinite(geod_usd) & np.isfinite(usd_try), out["Toplam_GEOD"] * geod_usd * usd_try, np.nan)

        # Low production flag
        out["Dusuk_Uretim"] = out["Toplam_GEOD"] < float(low_threshold)

        # Totals
        total_geod = float(out["Toplam_GEOD"].sum())
        total_tl = float(out["Toplam_TL"].sum()) if np.isfinite(out["Toplam_TL"].sum()) else np.nan

        st.session_state["last_result_df"] = out

        st.success("Hesap tamamlandı.")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Toplam GEOD", f"{total_geod:.6f}")
        m2.metric("Toplam TL", "—" if not np.isfinite(total_tl) else f"{total_tl:,.2f} TL")
        if target_tl > 0 and np.isfinite(total_tl):
            pct = min(100.0, (total_tl / target_tl) * 100.0)
            m3.metric("Hedef Tamamlama", f"{pct:.1f}%")
            m4.metric("Kalan", f"{max(0.0, target_tl - total_tl):,.2f} TL")
        else:
            m3.metric("Hedef Tamamlama", "—")
            m4.metric("Kalan", "—")

    if "last_result_df" in st.session_state:
        st.subheader("4) Sonuçlar")
        out = st.session_state["last_result_df"].copy()

        # Filter controls WITHOUT rescanning
        colF1, colF2, colF3 = st.columns([1, 1, 2])
        show_offline = colF1.checkbox("Sadece düşük üretim (offline/low)", value=False)
        sort_by = colF2.selectbox("Sırala", ["Toplam_GEOD (desc)", "Toplam_GEOD (asc)", "İş Ortağı", "İl"])
        search = colF3.text_input("Ara (SN / İş Ortağı / İl / Konum)", "")

        view = out.copy()
        if show_offline:
            view = view[view["Dusuk_Uretim"] == True]

        if search.strip():
            s = search.strip().lower()
            mask = (
                view["Miner_SN"].astype(str).str.lower().str.contains(s, na=False)
                | view["Is_Ortagi"].astype(str).str.lower().str.contains(s, na=False)
                | view["Il"].astype(str).str.lower().str.contains(s, na=False)
                | view["Konum"].astype(str).str.lower().str.contains(s, na=False)
            )
            view = view[mask]

        if sort_by == "Toplam_GEOD (desc)":
            view = view.sort_values("Toplam_GEOD", ascending=False)
        elif sort_by == "Toplam_GEOD (asc)":
            view = view.sort_values("Toplam_GEOD", ascending=True)
        elif sort_by == "İş Ortağı":
            view = view.sort_values("Is_Ortagi", ascending=True)
        elif sort_by == "İl":
            view = view.sort_values("Il", ascending=True)

        # Style: low production highlight
        def style_rows(row):
            if bool(row.get("Dusuk_Uretim", False)):
                return ["background-color: rgba(255, 0, 0, 0.12)"] * len(row)
            return [""] * len(row)

        st.dataframe(view.style.apply(style_rows, axis=1), use_container_width=True, height=420)

        # Export buttons
        colE1, colE2, colE3 = st.columns([1, 1, 2])

        csv_bytes = view.to_csv(index=False).encode("utf-8-sig")
        colE1.download_button("CSV indir", data=csv_bytes, file_name="geod_rapor.csv", mime="text/csv", use_container_width=True)

        # PDF
        meta = {
            "Tarih (performans)": f"{start_date} → {end_date}",
            "Payout filter": f"{performance_window_to_payout_filter(start_date, end_date)[0]} → {performance_window_to_payout_filter(start_date, end_date)[1]} (excl)",
            "GEOD/USD": "—" if not np.isfinite(view["GEOD_USD"].iloc[0]) else f"{view['GEOD_USD'].iloc[0]:.6f}",
            "USD/TRY": "—" if not np.isfinite(view["USD_TRY"].iloc[0]) else f"{view['USD_TRY'].iloc[0]:.4f}",
            "Cihaz adedi": str(len(view)),
        }
        pdf_bytes = df_to_pdf_bytes(view, title="GEOD Ödül Özeti", meta=meta)
        colE2.download_button("PDF indir", data=pdf_bytes, file_name="geod_rapor.pdf", mime="application/pdf", use_container_width=True)

        # WhatsApp message
        if colE3.button("WhatsApp mesajı hazırla", use_container_width=True):
            total_geod = float(view["Toplam_GEOD"].sum())
            total_tl = float(view["Toplam_TL"].sum()) if np.isfinite(view["Toplam_TL"].sum()) else np.nan
            low_cnt = int((view["Dusuk_Uretim"] == True).sum())

            msg = (
                f"GEOD Raporu (performans): {start_date} - {end_date}\n"
                f"Toplam GEOD: {total_geod:.6f}\n"
                + (f"Toplam TL: {total_tl:,.2f}\n" if np.isfinite(total_tl) else "")
                + (f"Düşük üretim cihaz: {low_cnt}\n" if low_cnt else "")
                +f"Not: Ödül kesim saati TR 08:30'a göre düzeltilmiş hesap.\n"
            )
            st.session_state["wa_msg"] = msg

        if "wa_msg" in st.session_state:
            msg = st.session_state["wa_msg"]
            st.text_area("Mesaj", value=msg, height=140)
            # wa.me needs URL-encoded
            import urllib.parse
            wa_link = "https://wa.me/?text=" + urllib.parse.quote(msg)
            st.link_button("WhatsApp’ta aç", wa_link)

    st.divider()
    st.caption("Not: Bu uygulama 'performans günleri' seçimini payout cut-off (TR 08:30) ile doğru toplar. "
               "Eğer senin backend endpoint/response alan adları farklıysa sadece `geodnet_call_rewards_timeline()` içinde düzeltmen yeterli.")


if __name__ == "__main__":
    main()

