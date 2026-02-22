# app.py
# MonsPro | GEOD Token Hesaplama Aracı (Streamlit)
# - Streamlit UI credentials (session_state) + secrets/env fallback
# - GEODNET getRewardsTimeLine (AES-CBC encrypted params)
# - 30-day chunked fetch
# - TR 08:30 payout cut-off fix (performance-day aligned)
# - Daily GEOD trend chart
# - Offline/Low devices: blinking red top banner + offline list
# - Excel upload + auto column mapping + manual SN
# - PDF export + CSV export + WhatsApp message (syntax fixed)
#
# Requirements:
#   pip install streamlit pandas numpy requests pycryptodome fpdf2 openpyxl
#
# Optional for deploy:
#   st.secrets can include:
#     GEODNET_BASE_URL, GEODNET_CLIENT_ID, GEODNET_TOKEN, GEODNET_AES_KEY, GEODNET_AES_IV, EXCHANGERATE_API_KEY

import os
import io
import re
import json
import time
import base64
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
# App Config
# ----------------------------
APP_TITLE = "MonsPro | GEOD Token Hesaplama Aracı"
TR_TZ = dt.timezone(dt.timedelta(hours=3))
PAYOUT_CUTOFF_TR = dt.time(hour=8, minute=30)  # 08:30 TR
DEFAULT_BASE_URL = "https://console-api.geodnet.com"

st.set_page_config(page_title=APP_TITLE, layout="wide")


# ----------------------------
# Credentials Helpers
# ----------------------------
def _secret_get(key: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            v = st.secrets.get(key)
            return str(v).strip() if v is not None else default
    except Exception:
        pass
    return default

def get_cred(key: str, default: str = "") -> str:
    # Priority: session_state -> st.secrets -> env
    v = st.session_state.get(key)
    if v is not None and str(v).strip():
        return str(v).strip()
    v = _secret_get(key, "")
    if v:
        return v
    return os.getenv(key, default).strip()

def sidebar_auth_state() -> Tuple[str, str, str, str, str, str]:
    """
    Returns:
      base_url, client_id, token, aes_key, aes_iv, exchangerate_api_key
    """
    st.sidebar.subheader("Yetkilendirme / Ayarlar")

    base_url_default = get_cred("GEODNET_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    base_url = st.sidebar.text_input("GEODNET_BASE URL", value=base_url_default, key="ui_base_url")

    show_sensitive = st.sidebar.checkbox("Token/AES göster", value=False)

    client_id = st.sidebar.text_input(
        "GEODNET_CLIENT_ID",
        value=get_cred("GEODNET_CLIENT_ID", ""),
        key="ui_client_id",
    )

    token = st.sidebar.text_input(
        "GEODNET_TOKEN",
        value=get_cred("GEODNET_TOKEN", ""),
        type="default" if show_sensitive else "password",
        key="ui_token",
    )

    aes_key = st.sidebar.text_input(
        "GEODNET_AES_KEY",
        value=get_cred("GEODNET_AES_KEY", ""),
        type="default" if show_sensitive else "password",
        key="ui_aes_key",
        help="Hex / Base64 / raw string kabul eder. Key 16/24/32 byte olmalı.",
    )

    aes_iv = st.sidebar.text_input(
        "GEODNET_AES_IV",
        value=get_cred("GEODNET_AES_IV", ""),
        type="default" if show_sensitive else "password",
        key="ui_aes_iv",
        help="Hex / Base64 / raw string kabul eder. IV 16 byte olmalı.",
    )

    ex_key = st.sidebar.text_input(
        "EXCHANGERATE_API_KEY (opsiyonel)",
        value=get_cred("EXCHANGERATE_API_KEY", ""),
        type="default" if show_sensitive else "password",
        key="ui_ex_key",
    )

    c1, c2 = st.sidebar.columns(2)
    if c1.button("Kaydet", use_container_width=True):
        st.session_state["GEODNET_BASE_URL"] = base_url.strip()
        st.session_state["GEODNET_CLIENT_ID"] = client_id.strip()
        st.session_state["GEODNET_TOKEN"] = token.strip()
        st.session_state["GEODNET_AES_KEY"] = aes_key.strip()
        st.session_state["GEODNET_AES_IV"] = aes_iv.strip()
        st.session_state["EXCHANGERATE_API_KEY"] = ex_key.strip()
        st.sidebar.success("Kaydedildi (session).")

    if c2.button("Temizle", use_container_width=True):
        for k in [
            "GEODNET_BASE_URL", "GEODNET_CLIENT_ID", "GEODNET_TOKEN",
            "GEODNET_AES_KEY", "GEODNET_AES_IV", "EXCHANGERATE_API_KEY"
        ]:
            st.session_state.pop(k, None)
        st.sidebar.warning("Session temizlendi.")

    # Status
    st.sidebar.caption(f"CLIENT_ID: {'✅' if client_id.strip() else '❌'}")
    st.sidebar.caption(f"TOKEN: {'✅' if token.strip() else '❌'}")
    st.sidebar.caption(f"AES_KEY: {'✅' if aes_key.strip() else '❌'}")
    st.sidebar.caption(f"AES_IV: {'✅' if aes_iv.strip() else '❌'}")

    return base_url.strip(), client_id.strip(), token.strip(), aes_key.strip(), aes_iv.strip(), ex_key.strip()


# ----------------------------
# Key/IV parsing
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
    if not key_s or not iv_s:
        raise ValueError("GEODNET_AES_KEY ve GEODNET_AES_IV gerekli (sidebar’dan gir).")

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
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    enc = cipher.encrypt(pad(raw, AES.block_size))
    return base64.b64encode(enc).decode("utf-8")

@dataclass
class RewardRow:
    miner_sn: str
    payout_ts: dt.datetime  # TR tz-aware
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
    Adjust endpoint/headers if your working version differs.
    """
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
    payload = {"params": enc_params}

    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"GEODNET API HTTP {r.status_code}: {r.text[:500]}")

    data = r.json()

    # Flexible shapes
    if isinstance(data, dict) and data.get("code") not in (None, 0) and data.get("success") is not True:
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
        if isinstance(data, list):
            items = data
        else:
            raise RuntimeError(f"Beklenmeyen response formatı: {str(data)[:500]}")

    out: List[RewardRow] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("time") or it.get("timestamp") or it.get("payoutTime") or it.get("dateTime")
        geod = it.get("reward") or it.get("geod") or it.get("amount") or it.get("value")
        if t is None or geod is None:
            continue

        try:
            t = int(t)
        except Exception:
            continue
        t_ms = t * 1000 if t < 10_000_000_000 else t

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
    Filter payout timestamps in:
      [start_date 08:30 TR, (end_date + 1 day) 08:30 TR)
    """
    start_dt = dt.datetime.combine(start_date, PAYOUT_CUTOFF_TR, tzinfo=TR_TZ)
    end_dt_excl = dt.datetime.combine(end_date + dt.timedelta(days=1), PAYOUT_CUTOFF_TR, tzinfo=TR_TZ)
    return start_dt, end_dt_excl

def dt_to_utc_ms(d: dt.datetime) -> int:
    return int(d.astimezone(dt.timezone.utc).timestamp() * 1000)

def chunk_ranges(start_dt: dt.datetime, end_dt_excl: dt.datetime, chunk_days: int = 30) -> List[Tuple[dt.datetime, dt.datetime]]:
    ranges = []
    cur = start_dt
    while cur < end_dt_excl:
        nxt = min(cur + dt.timedelta(days=chunk_days), end_dt_excl)
        ranges.append((cur, nxt))
        cur = nxt
    return ranges


# ----------------------------
# Prices
# ----------------------------
@st.cache_data(ttl=600, show_spinner=False)
def get_prices_cached(exchangerate_api_key: str) -> Dict[str, float]:
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

    # USD/TRY via exchangerate-api, fallback Frankfurter
    if exchangerate_api_key:
        try:
            ex = requests.get(
                f"https://v6.exchangerate-api.com/v6/{exchangerate_api_key}/latest/USD",
                timeout=15,
            )
            if ex.status_code == 200:
                out["USD_TRY"] = float(ex.json()["conversion_rates"]["TRY"])
        except Exception:
            pass

    if not np.isfinite(out["USD_TRY"]):
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


# ----------------------------
# Excel parsing + auto mapping
# ----------------------------
def normalize_colname(s: str) -> str:
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    tr_map = str.maketrans({"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"})
    s = s.translate(tr_map)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s

def pick_best_column(cols: List[str], keywords: List[str]) -> Optional[str]:
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
    return {
        "sn": pick_best_column(cols, ["sn", "serial", "seri", "miner", "device", "cihaz"]),
        "partner": pick_best_column(cols, ["is ortagi", "partner", "bayi", "distributor", "musteri", "firma"]),
        "il": pick_best_column(cols, ["il", "sehir", "city", "province"]),
        "konum": pick_best_column(cols, ["konum", "lokasyon", "location", "adres", "address", "bolge", "region"]),
    }

def load_excel(file_bytes: bytes) -> pd.DataFrame:
    bio = io.BytesIO(file_bytes)
    df = pd.read_excel(bio, engine="openpyxl")
    df = df.dropna(how="all")
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
# Fetch + compute
# ----------------------------
def fetch_rewards_raw(
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
    payout_start, payout_end_excl = performance_window_to_payout_filter(start_date, end_date)
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
            time.sleep(0.05)

    if show_progress:
        prog.progress(1.0)
        status.caption("Tamamlandı.")
        time.sleep(0.15)

    # strict filter
    filtered = [r for r in rows if (r.payout_ts >= payout_start and r.payout_ts < payout_end_excl)]
    if not filtered:
        return pd.DataFrame(columns=["Miner_SN", "Payout_TR", "GEOD"])

    df = pd.DataFrame([{
        "Miner_SN": r.miner_sn,
        "Payout_TR": r.payout_ts,
        "GEOD": r.geod
    } for r in filtered])

    return df

def aggregates_from_raw(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["Miner_SN", "Toplam_GEOD", "Odul_Sayisi", "Ilk_Odul_TR", "Son_Odul_TR"])
    agg = raw.groupby("Miner_SN", as_index=False).agg(
        Toplam_GEOD=("GEOD", "sum"),
        Odul_Sayisi=("GEOD", "count"),
        Ilk_Odul_TR=("Payout_TR", "min"),
        Son_Odul_TR=("Payout_TR", "max"),
    )
    agg["Toplam_GEOD"] = agg["Toplam_GEOD"].round(6)
    return agg

def daily_trend_from_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """
    performance_day = payout_date_tr - 1 day
    """
    if raw.empty:
        return pd.DataFrame(columns=["Performance_Day", "GEOD"])
    tmp = raw.copy()
    tmp["Payout_Date_TR"] = tmp["Payout_TR"].dt.date
    tmp["Performance_Day"] = tmp["Payout_Date_TR"].apply(lambda d: d - dt.timedelta(days=1))
    daily = tmp.groupby("Performance_Day", as_index=False)["GEOD"].sum()
    daily = daily.sort_values("Performance_Day")
    daily["GEOD"] = daily["GEOD"].round(6)
    return daily


# ----------------------------
# UI helpers
# ----------------------------
def render_offline_banner(offline_count: int):
    if offline_count <= 0:
        return
    html = f"""
    <style>
      .offline-banner {{
        width: 100%;
        padding: 14px 16px;
        border-radius: 12px;
        background: rgba(255, 0, 0, 0.18);
        border: 1px solid rgba(255, 0, 0, 0.35);
        color: #fff;
        font-weight: 800;
        letter-spacing: 0.3px;
        margin: 8px 0 14px 0;
        animation: blink 1.1s infinite;
      }}
      @keyframes blink {{
        0%   {{ filter: brightness(1.0); }}
        50%  {{ filter: brightness(1.8); }}
        100% {{ filter: brightness(1.0); }}
      }}
      .offline-badge {{
        display: inline-block;
        padding: 4px 10px;
        margin-left: 8px;
        border-radius: 999px;
        background: rgba(255,0,0,0.55);
        border: 1px solid rgba(255,0,0,0.7);
      }}
    </style>
    <div class="offline-banner">
      ⚠️ OFFLINE / LOW ÜRETİM CİHAZLAR VAR
      <span class="offline-badge">Adet: {offline_count}</span>
      <span style="font-weight:600; opacity:0.9; margin-left:10px;">
        (Aşağıdan listeyi görebilirsin)
      </span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# ----------------------------
# Main
# ----------------------------
def main():
    st.title(APP_TITLE)

    base_url, client_id, token, aes_key_s, aes_iv_s, ex_key = sidebar_auth_state()

    prices = get_prices_cached(ex_key)
    geod_usd = prices.get("GEOD_USD", np.nan)
    usd_try = prices.get("USD_TRY", np.nan)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("GEOD / USD", "—" if not np.isfinite(geod_usd) else f"{geod_usd:.6f}")
    c2.metric("USD / TRY", "—" if not np.isfinite(usd_try) else f"{usd_try:.4f}")
    c3.metric("Payout Cut-off (TR)", "08:30")
    c4.metric("Zaman Dilimi", "Europe/Istanbul (UTC+3)")

    st.divider()

    # Defaults
    today_tr = dt.datetime.now(TR_TZ).date()
    default_end = today_tr - dt.timedelta(days=1)
    default_start = default_end - dt.timedelta(days=29)

    st.subheader("1) Tarih Aralığı (Performans Günleri)")
    colA, colB, colC = st.columns([1, 1, 2])
    start_date = colA.date_input("Başlangıç (performans)", value=default_start)
    end_date = colB.date_input("Bitiş (performans)", value=default_end)
    payout_start, payout_end_excl = performance_window_to_payout_filter(start_date, end_date)
    colC.info(
        f"**Payout filtrelemesi**:\n\n"
        f"- Dahil: {payout_start.strftime('%Y-%m-%d %H:%M')}\n"
        f"- Hariç: {payout_end_excl.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Başlangıç gününe 'bir önceki gün ödülü' sızmaz."
    )

    st.subheader("2) Cihaz Listesi (Excel + Manuel)")
    left, right = st.columns([1.2, 1])

    if "miner_df" not in st.session_state:
        st.session_state.miner_df = pd.DataFrame(columns=["Miner_SN", "Is_Ortagi", "Il", "Konum"])

    with left:
        up = st.file_uploader("Excel yükle (.xlsx)", type=["xlsx"])
        if up is not None:
            raw_df = load_excel(up.read())
            mapping = auto_map_columns(raw_df)

            st.caption("Otomatik sütun eşleme:")
            mc1, mc2, mc3, mc4 = st.columns(4)
            sn_col = mc1.selectbox("SN sütunu", options=[None] + list(raw_df.columns),
                                   index=(0 if mapping["sn"] is None else (1 + list(raw_df.columns).index(mapping["sn"]))))
            partner_col = mc2.selectbox("İş Ortağı", options=[None] + list(raw_df.columns),
                                        index=(0 if mapping["partner"] is None else (1 + list(raw_df.columns).index(mapping["partner"]))))
            il_col = mc3.selectbox("İl", options=[None] + list(raw_df.columns),
                                   index=(0 if mapping["il"] is None else (1 + list(raw_df.columns).index(mapping["il"]))))
            konum_col = mc4.selectbox("Konum", options=[None] + list(raw_df.columns),
                                      index=(0 if mapping["konum"] is None else (1 + list(raw_df.columns).index(mapping["konum"]))))

            if sn_col is None:
                st.error("SN sütunu seçmeden devam edemem.")
            else:
                df = pd.DataFrame()
                df["Miner_SN"] = raw_df[sn_col].astype(str).str.strip()
                df["Is_Ortagi"] = raw_df[partner_col].astype(str).str.strip() if partner_col else ""
                df["Il"] = raw_df[il_col].astype(str).str.strip() if il_col else ""
                df["Konum"] = raw_df[konum_col].astype(str).str.strip() if konum_col else ""

                df = df[df["Miner_SN"].replace({"nan": "", "None": ""}).astype(str).str.len() > 0].copy()
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

        st.caption("Eşikler")
        target_tl = st.number_input("Hedef (TL)", min_value=0.0, value=0.0, step=100.0)
        low_threshold = st.number_input("Offline/Low eşiği (GEOD)", min_value=0.0, value=180.0, step=10.0)

    st.divider()

    st.subheader("3) Hesapla")
    colX, colY, colZ = st.columns([1, 1, 2])
    do_calc = colX.button("Ödülleri Çek ve Hesapla", type="primary", use_container_width=True)
    refresh_prices = colY.button("Fiyatları Yenile", use_container_width=True)

    if refresh_prices:
        get_prices_cached.clear()
        st.experimental_rerun()

    if do_calc:
        if not client_id or not token:
            st.error("CLIENT_ID / TOKEN eksik. Sidebar’dan girip Kaydet.")
            st.stop()

        try:
            key, iv = parse_key_iv(aes_key_s, aes_iv_s)
        except Exception as e:
            st.error(str(e))
            st.stop()

        miner_df = st.session_state.miner_df.copy()
        miner_sns = miner_df["Miner_SN"].astype(str).str.strip().tolist()
        if not miner_sns:
            st.warning("Önce Excel yükle veya manuel SN ekle.")
            st.stop()

        with st.spinner("Ödüller çekiliyor..."):
            raw = fetch_rewards_raw(
                miner_sns=miner_sns,
                start_date=start_date,
                end_date=end_date,
                base_url=base_url,
                key=key,
                iv=iv,
                token=token,
                client_id=client_id,
                show_progress=True,
            )

        agg = aggregates_from_raw(raw)
        daily = daily_trend_from_raw(raw)

        out = miner_df.merge(agg, how="left", on="Miner_SN")
        out["Toplam_GEOD"] = out["Toplam_GEOD"].fillna(0.0)
        out["Odul_Sayisi"] = out["Odul_Sayisi"].fillna(0).astype(int)

        prices2 = get_prices_cached(ex_key)
        geod_usd = prices2.get("GEOD_USD", np.nan)
        usd_try = prices2.get("USD_TRY", np.nan)

        out["GEOD_USD"] = geod_usd
        out["USD_TRY"] = usd_try
        out["Toplam_USD"] = np.where(np.isfinite(geod_usd), out["Toplam_GEOD"] * geod_usd, np.nan)
        out["Toplam_TL"] = np.where(np.isfinite(geod_usd) & np.isfinite(usd_try), out["Toplam_GEOD"] * geod_usd * usd_try, np.nan)

        thr = float(low_threshold)
        out["Offline"] = (out["Toplam_GEOD"] < thr) | (out["Odul_Sayisi"] == 0)

        st.session_state["last_result_df"] = out
        st.session_state["last_raw_df"] = raw
        st.session_state["last_daily_df"] = daily

        st.success("Hesap tamamlandı.")

    # ----------------------------
    # Results
    # ----------------------------
    if "last_result_df" in st.session_state:
        out = st.session_state["last_result_df"].copy()
        daily = st.session_state.get("last_daily_df", pd.DataFrame())

        offline_df = out[out["Offline"] == True].copy()
        offline_count = len(offline_df)

        render_offline_banner(offline_count)

        total_geod = float(out["Toplam_GEOD"].sum())
        total_tl = float(out["Toplam_TL"].sum()) if np.isfinite(out["Toplam_TL"].sum()) else np.nan
        low_cnt = int((out["Offline"] == True).sum())

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

        st.divider()

        st.subheader("4) Günlük GEOD Üretim Trendi (Performans Günleri)")
        if daily.empty:
            st.info("Bu tarih aralığında trend verisi yok.")
        else:
            d2 = daily.copy()
            d2["Performance_Day"] = pd.to_datetime(d2["Performance_Day"])
            d2 = d2.set_index("Performance_Day")
            st.line_chart(d2["GEOD"], height=260)
            with st.expander("Trend tablosu"):
                st.dataframe(daily, use_container_width=True, height=220)

        st.divider()

        st.subheader("5) Offline / Low Üretim Cihazlar")
        if offline_count == 0:
            st.success("Offline/Low cihaz yok 🎉")
        else:
            cols = ["Miner_SN", "Is_Ortagi", "Il", "Konum", "Toplam_GEOD", "Odul_Sayisi", "Son_Odul_TR"]
            cols = [c for c in cols if c in offline_df.columns]
            st.dataframe(
                offline_df[cols].sort_values(["Toplam_GEOD", "Odul_Sayisi"], ascending=[True, True]),
                use_container_width=True,
                height=260,
            )

        st.divider()

        st.subheader("6) Tüm Sonuçlar")
        colF1, colF2, colF3 = st.columns([1, 1, 2])
        only_offline = colF1.checkbox("Sadece offline/low göster", value=False)
        sort_by = colF2.selectbox("Sırala", ["Toplam_GEOD (desc)", "Toplam_GEOD (asc)", "İş Ortağı", "İl"])
        search = colF3.text_input("Ara (SN / İş Ortağı / İl / Konum)", "")

        view = out.copy()
        if only_offline:
            view = view[view["Offline"] == True]

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

        def style_rows(row):
            if bool(row.get("Offline", False)):
                return ["background-color: rgba(255, 0, 0, 0.12)"] * len(row)
            return [""] * len(row)

        st.dataframe(view.style.apply(style_rows, axis=1), use_container_width=True, height=420)

        st.divider()

        st.subheader("7) Dışa Aktar")
        colE1, colE2, colE3 = st.columns([1, 1, 2])

        csv_bytes = view.to_csv(index=False).encode("utf-8-sig")
        colE1.download_button(
            "CSV indir",
            data=csv_bytes,
            file_name="geod_rapor.csv",
            mime="text/csv",
            use_container_width=True,
        )

        meta = {
            "Tarih (performans)": f"{start_date} → {end_date}",
            "Payout filter": f"{performance_window_to_payout_filter(start_date, end_date)[0]} → {performance_window_to_payout_filter(start_date, end_date)[1]} (excl)",
            "Cihaz adedi": str(len(view)),
            "Offline/Low adedi": str(int((view["Offline"] == True).sum())),
        }
        pdf_bytes = df_to_pdf_bytes(view, title="GEOD Ödül Özeti", meta=meta)
        colE2.download_button(
            "PDF indir",
            data=pdf_bytes,
            file_name="geod_rapor.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

        # WhatsApp message (SYNTAX FIXED)
        if colE3.button("WhatsApp mesajı hazırla", use_container_width=True):
            msg = (
                f"GEOD Raporu (performans): {start_date} - {end_date}\n"
                f"Toplam GEOD: {total_geod:.6f}\n"
                + (f"Toplam TL: {total_tl:,.2f}\n" if np.isfinite(total_tl) else "")
                + (f"Düşük üretim cihaz: {low_cnt}\n" if low_cnt else "")
                + "Not: Ödül kesim saati TR 08:30'a göre düzeltilmiş hesap.\n"
            )
            st.session_state["wa_msg"] = msg

        if "wa_msg" in st.session_state:
            import urllib.parse
            msg = st.session_state["wa_msg"]
            st.text_area("Mesaj", value=msg, height=140)
            wa_link = "https://wa.me/?text=" + urllib.parse.quote(msg)
            st.link_button("WhatsApp’ta aç", wa_link)

    st.caption(
        "Not: Günlük trend, payout günü değil **performans günü** olarak hesaplanır: "
        "Performance_Day = (Payout_TR tarihi - 1 gün)."
    )


if __name__ == "__main__":
    main()
