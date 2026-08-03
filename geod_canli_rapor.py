import streamlit as st
import requests
import time
import binascii
import pandas as pd
import urllib.parse
import json
import os
import tempfile
from datetime import datetime, timedelta, date
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from fpdf import FPDF
import warnings

warnings.filterwarnings("ignore")
st.set_page_config(page_title="MonsPro | Operasyonel Portal", layout="wide")

PAYOUT_CUTOFF_TR = "08:30"
LOW_PROD_THRESHOLD_DEFAULT = 180

# --- GEOD Halving (1 Temmuz 2026) ---
# 1 Temmuz 2026 itibarıyla günlük normal kazanç 12 → 6 GEOD'a düştü.
# Dönem halving tarihini kapsıyorsa limit gün bazında karma hesaplanır:
#   maks_normal = (halving öncesi gün × eski limit) + (halving sonrası gün × yeni limit)
HALVING_DATE = date(2026, 7, 1)
DAILY_NORMAL_GEOD_PRE = 12       # halving ÖNCESİ günlük normal limit
DAILY_NORMAL_GEOD_POST = 6       # halving SONRASI günlük normal limit
DAILY_SUPERHEX_GEOD_PRE = 48     # superhex: normalin 4 katı
DAILY_SUPERHEX_GEOD_POST = 24
# Geriye uyum (arşiv kayıtları vb. eski sabit adını kullanıyor)
DAILY_NORMAL_GEOD = DAILY_NORMAL_GEOD_POST
DAILY_SUPERHEX_GEOD = DAILY_SUPERHEX_GEOD_POST


def max_normal_for_period(start_d: date, end_d: date, is_superhex: bool):
    """Halving-bilinçli maks normal GEOD limiti.

    Dönüş: (maks_normal_geod, halving_oncesi_gun, halving_sonrasi_gun)
    """
    pre_rate = DAILY_SUPERHEX_GEOD_PRE if is_superhex else DAILY_NORMAL_GEOD_PRE
    post_rate = DAILY_SUPERHEX_GEOD_POST if is_superhex else DAILY_NORMAL_GEOD_POST
    total_days = (end_d - start_d).days + 1
    if end_d < HALVING_DATE:                       # tamamen halving öncesi
        pre_days = total_days
    elif start_d >= HALVING_DATE:                  # tamamen halving sonrası
        pre_days = 0
    else:                                          # karma dönem
        pre_days = (HALVING_DATE - start_d).days
    post_days = total_days - pre_days
    return pre_days * pre_rate + post_days * post_rate, pre_days, post_days

HTTP = requests.Session()

TR_MAP = str.maketrans(
    {"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ı": "i", "İ": "I", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"}
)

# -------------------------
# Utils
# -------------------------
def temizle(text):
    if text is None:
        return ""
    return str(text).translate(TR_MAP)

def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def normalize_phone(raw):
    tel = str(raw).replace(".0", "").strip() if raw is not None else ""
    if tel.startswith("5"):
        tel = "90" + tel
    elif tel.startswith("0"):
        tel = "9" + tel
    return tel

def _pick_col(df: pd.DataFrame, candidates):
    """Excel kolon başlıklarını esnek yakalamak için."""
    cols = {str(c).strip(): c for c in df.columns}
    for k in candidates:
        if k in cols:
            return cols[k]

    def norm(s):
        return temizle(str(s)).strip().lower()

    ncols = {norm(k): v for k, v in cols.items()}
    for k in candidates:
        nk = norm(k)
        if nk in ncols:
            return ncols[nk]
    return None

@st.cache_data(ttl=600, show_spinner=False)
def get_live_prices_cached():
    try:
        res = HTTP.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=geodnet&vs_currencies=usd",
            timeout=5,
        ).json()
        geod_p = float(res["geodnet"]["usd"])
        usd_t = float(HTTP.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()["rates"]["TRY"])
        return geod_p, usd_t
    except Exception:
        return 0.1500, 33.00

def encrypt_param(data, key):
    # TOKEN -> 16 byte sabitlenip hem key hem iv
    k_fixed = str(key).rjust(16, "0")[:16].encode("utf-8")
    cipher = AES.new(k_fixed, AES.MODE_CBC, iv=k_fixed)
    padded_data = pad(str(data).encode("utf-8"), 16)
    return binascii.hexlify(cipher.encrypt(padded_data)).decode("utf-8")

def parse_reward_date(item: dict):
    candidates = ("date", "day", "rewardDate", "createDate", "time", "timestamp", "ts")
    for k in candidates:
        v = item.get(k)
        if not v:
            continue
        try:
            iv = int(v)
            if iv > 10_000_000_000:  # ms
                return datetime.utcfromtimestamp(iv / 1000).date()
            if iv > 1_000_000_000:  # s
                return datetime.utcfromtimestamp(iv).date()
        except Exception:
            pass
        if isinstance(v, str) and len(v) >= 10:
            try:
                return datetime.strptime(v[:10], "%Y-%m-%d").date()
            except Exception:
                pass
    return None


# -------------------------
# Checkpoint (dosya tabanlı, app restart'a dayanıklı)
# -------------------------
CHECKPOINT_DIR = os.path.join(tempfile.gettempdir(), "geodnet_calc")
CHECKPOINT_FILE = os.path.join(CHECKPOINT_DIR, "checkpoint.json")

def _ensure_cp_dir():
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)

def _date_serializer(obj):
    if isinstance(obj, date):
        return {"__date__": obj.isoformat()}
    raise TypeError(f"Not serializable: {type(obj)}")

def _date_deserializer(obj):
    if "__date__" in obj:
        return date.fromisoformat(obj["__date__"])
    return obj

def save_checkpoint(calc_index, calc_results, calc_daily_sum, calc_params, source_records):
    """Hesaplama ilerlemesini dosyaya yaz."""
    _ensure_cp_dir()
    # daily_sum key'leri date objesi, stringe çevir
    ds_ser = {k.isoformat() if isinstance(k, date) else str(k): v for k, v in calc_daily_sum.items()}
    # params içindeki date objeleri
    params_ser = {}
    for k, v in calc_params.items():
        if isinstance(v, date):
            params_ser[k] = {"__date__": v.isoformat()}
        else:
            params_ser[k] = v
    data = {
        "calc_index": calc_index,
        "calc_results": calc_results,
        "calc_daily_sum": ds_ser,
        "calc_params": params_ser,
        "source_records": source_records,
        "updated_at": datetime.now().isoformat(),
    }
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=_date_serializer)

def load_checkpoint():
    """Checkpoint varsa yükle, yoksa None döndür."""
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # daily_sum key'lerini date'e çevir
        ds = {}
        for k, v in data.get("calc_daily_sum", {}).items():
            try:
                ds[date.fromisoformat(k)] = v
            except Exception:
                ds[k] = v
        data["calc_daily_sum"] = ds
        # params içindeki date objelerini geri çevir
        params = data.get("calc_params", {})
        for k, v in params.items():
            if isinstance(v, dict) and "__date__" in v:
                params[k] = date.fromisoformat(v["__date__"])
        data["calc_params"] = params
        return data
    except Exception:
        return None

def clear_checkpoint():
    """Checkpoint dosyasını sil."""
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)


# -------------------------
# API Calls
# -------------------------
def get_all_rewards(sn: str, payout_start: date, payout_end: date, client_id: str, token: str):
    all_data = []
    curr = payout_start
    chunk_idx = 0
    while curr <= payout_end:
        curr_end = min(curr + timedelta(days=29), payout_end)
        ts = str(int(time.time() * 1000))
        params = {
            "clientId": client_id,
            "timeStamp": encrypt_param(ts, token),
            "sn": encrypt_param(sn, token),
            "minTime": encrypt_param(curr.strftime("%Y-%m-%d"), token),
            "maxTime": encrypt_param(curr_end.strftime("%Y-%m-%d"), token),
        }
        max_retries = 3
        for attempt in range(max_retries):
            try:
                r = HTTP.get(
                    "https://consoleresapi.geodnet.com/getRewardsTimeLine",
                    params=params,
                    verify=False,
                    timeout=15,
                )
                res = r.json()
                status_code = res.get("statusCode")
                msg = str(res.get("msg", "") or "")
                if status_code == 200:
                    data = res.get("data", [])
                    if data:
                        all_data.extend(data)
                    break
                elif status_code == 602 or "excessive" in msg.lower():
                    time.sleep(2.0 * (attempt + 1))
                    continue
                else:
                    break
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(1.5)
                    continue
                break
        curr = curr_end + timedelta(days=1)
        chunk_idx += 1
        if chunk_idx > 0:
            time.sleep(1.5)
    return all_data


def _get_sn_info(sn: str, client_id: str, token: str, url: str):
    """
    Dokümana göre /getSnInfo:
      clientId = plain text
      timeStamp = encrypted (ms)
      sn = encrypted
    """
    ts = str(int(time.time() * 1000))
    params = {
        "clientId": client_id,
        "timeStamp": encrypt_param(ts, token),
        "sn": encrypt_param(sn, token),
    }
    try:
        r = HTTP.get(url, params=params, verify=False, timeout=15)
        return r.json()
    except Exception:
        return {"statusCode": -1, "msg": "request_error", "data": {}}


def _extract_online_and_ts(sninfo_resp: dict):
    """
    getSnInfo response:
      statusCode == 200 OK
      data.online (1/0)
      data.timestamp (latest update time)
    """
    status_code = None
    msg = ""
    data = {}

    if isinstance(sninfo_resp, dict):
        status_code = sninfo_resp.get("statusCode")
        msg = str(sninfo_resp.get("msg", "") or "")
        if isinstance(sninfo_resp.get("data"), dict):
            data = sninfo_resp["data"]

    online = None
    if "online" in data:
        try:
            online = int(data.get("online"))
        except Exception:
            online = None

    ts_val = data.get("timestamp", "")
    ts_str = ""
    if ts_val:
        try:
            iv = int(ts_val)
            if iv > 10_000_000_000:
                ts_str = datetime.utcfromtimestamp(iv / 1000).strftime("%Y-%m-%d %H:%M:%S")
            elif iv > 1_000_000_000:
                ts_str = datetime.utcfromtimestamp(iv).strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts_str = str(ts_val)
        except Exception:
            ts_str = str(ts_val)

    return status_code, msg, online, ts_str


def offline_check_getsninfo(device_df: pd.DataFrame, client_id: str, token: str, url: str):
    """
    Offline = online == 0
    Ayrıca API hatası/limit durumlarını da görünür yap:
      - statusCode != 200 => DURUM = ERROR / RATE_LIMIT
      - online None => UNKNOWN
    Bu satırları da listeleriz (operasyonel görünürlük için).
    """
    if device_df is None or device_df.empty:
        return pd.DataFrame(columns=["SN", "Is_Ortagi", "Il", "Konum", "Durum", "Son_Guncelleme"])

    rows = []
    p = st.progress(0)
    sns = device_df["SN"].astype(str).tolist()
    n = max(1, len(sns))

    def add_row(sn, meta, durum, ts_str):
        rows.append(
            {
                "SN": str(sn),
                "Is_Ortagi": meta.get("Is_Ortagi", ""),
                "Il": meta.get("Il", ""),
                "Konum": meta.get("Konum", ""),
                "Durum": durum,
                "Son_Guncelleme": ts_str,
            }
        )

    for i, sn in enumerate(sns):
        meta = device_df.loc[device_df["SN"].astype(str) == str(sn)].iloc[0].to_dict()

        resp = _get_sn_info(sn, client_id, token, url)
        status_code, msg, online, ts_str = _extract_online_and_ts(resp)

        # Rate-limit yakala: dokümanda 602 excessive request frequency var.
        if status_code == 602 or "excessive" in msg.lower():
            # kısa bekle + 1 retry
            time.sleep(0.8)
            resp2 = _get_sn_info(sn, client_id, token, url)
            status_code, msg, online, ts_str = _extract_online_and_ts(resp2)

        if status_code != 200:
            # hata olanları da listeye alalım (kaçırmayalım)
            durum = "RATE_LIMIT" if status_code == 602 else "ERROR"
            add_row(sn, meta, durum, ts_str)
        else:
            if online == 0:
                add_row(sn, meta, "OFFLINE", ts_str)
            elif online is None:
                add_row(sn, meta, "UNKNOWN", ts_str)
            # online==1 ise listeye alma

        p.progress((i + 1) / n)

        # her cihaz sorgusundan sonra 2-3 sn bekle
        if i < len(sns) - 1:
            time.sleep(2.5)

    return pd.DataFrame(rows)


# -------------------------
# UI helpers (görsel aynı)
# -------------------------
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
      ⚠️ OFFLINE / HATA DURUMU OLAN CİHAZLAR VAR
      <span class="offline-badge">Adet: {offline_count}</span>
      <span style="font-weight:600; opacity:0.9; margin-left:10px;">
        (Aşağıdan listeyi görebilirsin)
      </span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# -------------------------
# PDF / WP (UI aynı, PDF’e İl/Konum + TOPLAM GEOD/TL eklendi)
# -------------------------
def create_pdf(m_name, data_df, g_price, u_try, s_date, device_df=None):
    """
    PDF görselini bozmadan:
      - Her Miner satırının altına: Il | Konum
      - PDF en altına (multi-device dahil): TOPLAM ÖDENECEK GEOD ve TOPLAM TL
    """
    # SN -> (Il, Konum) map
    loc_map = {}
    try:
        if device_df is not None and not device_df.empty:
            for _, r in device_df.iterrows():
                sn = str(r.get("SN", "")).strip()
                if not sn:
                    continue
                il = str(r.get("Il", "")).strip()
                konum = str(r.get("Konum", "")).strip()
                loc_map[sn] = (il, konum)
    except Exception:
        loc_map = {}

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(190, 10, "MonsPro GEODNET HAKEDIS RAPORU", ln=True, align="C")
    pdf.set_font("helvetica", "", 10)
    pdf.ln(5)
    pdf.cell(95, 8, f"Is Ortagi: {temizle(m_name)}")
    pdf.cell(95, 8, f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y')}", ln=True, align="R")
    pdf.cell(190, 8, f"Donem: {s_date}", ln=True)
    pdf.cell(190, 8, f"GEOD Fiyat: ${g_price:.4f} | Kur: {u_try:.2f} TL", ln=True)
    pdf.ln(5)

    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("helvetica", "B", 7)
    pdf.cell(30, 10, "Miner No", 1, 0, "C", True)
    pdf.cell(20, 10, "Kazanc", 1, 0, "C", True)
    pdf.cell(25, 10, "Durum", 1, 0, "C", True)
    pdf.cell(25, 10, "Hakedis", 1, 0, "C", True)
    pdf.cell(25, 10, "Eklenen", 1, 0, "C", True)
    pdf.cell(30, 10, "Top.GEOD", 1, 0, "C", True)
    pdf.cell(35, 10, "Tutar(TL)", 1, 1, "C", True)

    pdf.set_font("helvetica", "", 7)
    for _, row in data_df.iterrows():
        sn = str(row["SN"]).strip()
        pm = safe_float(row.get("Power_Mining", 0), 0.0)
        # Power Mining varsa Hesaba Katılan göster, yoksa Toplam (zaten aynı)
        if pm > 0:
            kazanc = safe_float(row.get("Hesaba_Katilan", row.get("Toplam_GEOD_Kazanc", 0)), 0.0)
        else:
            kazanc = safe_float(row.get("Toplam_GEOD_Kazanc", 0), 0.0)

        pdf.cell(30, 10, sn, 1)
        pdf.cell(20, 10, f"{kazanc:.2f}", 1)
        pdf.cell(25, 10, temizle(row["Durum_Etiket"]), 1, 0, "C")
        pdf.cell(25, 10, f"{row['Hakedis_Baz']:.2f}", 1)
        pdf.cell(25, 10, f"{row['EKLENEN_GEOD']:.2f}", 1)
        pdf.cell(30, 10, f"{row['GEOD_HAKEDIS']:.2f}", 1)
        pdf.cell(35, 10, f"{row['Hakedis_TL']:.2f} TL", 1, 1, "C")

        # İl + Konum satırı
        il, konum = loc_map.get(sn, ("", ""))
        pdf.set_font("helvetica", "", 6)
        pdf.cell(190, 6, f"Il: {temizle(il)} | Konum: {temizle(konum)}", 1, 1)
        pdf.set_font("helvetica", "", 7)

    # ✅ TOPLAM ÖDENECEK GEOD + TL (özellikle birden fazla cihaz varsa)
    toplam_geod = safe_float(data_df["GEOD_HAKEDIS"].sum(), 0.0) if "GEOD_HAKEDIS" in data_df.columns else 0.0
    toplam_tl = safe_float(data_df["Hakedis_TL"].sum(), 0.0) if "Hakedis_TL" in data_df.columns else 0.0

    pdf.ln(4)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(190, 8, f"Toplam Odenecek GEOD: {toplam_geod:.2f} GEOD", ln=True, align="R")
    pdf.cell(190, 8, f"Genel Toplam: {toplam_tl:.2f} TL", ln=True, align="R")

    return bytes(pdf.output())

def wp_mesaj_olustur(m_name, m_data, donem, kur_geod, kur_usd):
    msg = f"*📄 MonsPro GEODNET Hakedis Raporu*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n"
    msg += f"*👤 Is Ortagi:* {temizle(m_name)}\n"
    msg += f"*📅 Donem:* {donem}\n"
    msg += f"*💰 Anlik Kur:* 1 GEOD = ${kur_geod:.4f} ({kur_geod * kur_usd:.2f} TL)\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n\n"
    for _, row in m_data.iterrows():
        simge = "✅" if row["Durum_Etiket"] == "TAM KAZANC" else "🎁" if row["Durum_Etiket"] == "DESTEKLENDI" else "⚠️"
        pm = safe_float(row.get("Power_Mining", 0), 0.0)
        # Power Mining varsa Hesaba Katılan, yoksa Toplam göster
        if pm > 0:
            kazanc = safe_float(row.get("Hesaba_Katilan", row.get("Toplam_GEOD_Kazanc", 0)), 0.0)
        else:
            kazanc = safe_float(row.get("Toplam_GEOD_Kazanc", 0), 0.0)
        msg += f"{simge} *Miner:* {row['SN']}\n"
        msg += f"   └ Kazanc: {kazanc:.2f} GEOD\n"
        if row["EKLENEN_GEOD"] > 0:
            msg += f"   └ Destek: +{row['EKLENEN_GEOD']:.2f} GEOD\n"
        msg += f"   └ *Hakedis:* {row['Hakedis_TL']:.2f} TL\n\n"

    # ✅ WhatsApp mesajına da toplamlar (isteğe bağlı ama faydalı)
    try:
        toplam_geod = safe_float(m_data["GEOD_HAKEDIS"].sum(), 0.0)
        toplam_tl = safe_float(m_data["Hakedis_TL"].sum(), 0.0)
        msg += f"━━━━━━━━━━━━━━━━━━━\n"
        msg += f"*💳 TOPLAM ODEME: {toplam_tl:.2f} TL*\n"
        msg += f"*🧾 TOPLAM GEOD: {toplam_geod:.2f} GEOD*\n"
        msg += f"━━━━━━━━━━━━━━━━━━━\n\n"
    except Exception:
        msg += f"━━━━━━━━━━━━━━━━━━━\n"
        msg += f"*💳 TOPLAM ODEME: {m_data['Hakedis_TL'].sum():.2f} TL*\n"
        msg += f"━━━━━━━━━━━━━━━━━━━\n\n"

    msg += f"🚀 *MonsPro Team*"
    return msg


# -------------------------
# Session State
# -------------------------
if "arsiv" not in st.session_state:
    st.session_state.arsiv = {}
if "last_results" not in st.session_state:
    st.session_state.last_results = None
if "device_df" not in st.session_state:
    st.session_state.device_df = None
if "offline_results" not in st.session_state:
    st.session_state.offline_results = None
if "geod_p" not in st.session_state:
    g_val, u_val = get_live_prices_cached()
    st.session_state.geod_p = g_val
    st.session_state.usd_t = u_val
# Incremental hesaplama state (rerun'da kaldığı yerden devam eder)
# Önce checkpoint dosyasından kurtarma dene (app restart durumu)
if "calc_in_progress" not in st.session_state:
    cp = load_checkpoint()
    if cp and cp.get("calc_index", 0) > 0 and cp.get("source_records"):
        # App restart olmuş ama yarım kalmış hesaplama var - kurtarılıyor
        st.session_state.calc_in_progress = True
        st.session_state.calc_source_df = pd.DataFrame(cp["source_records"])
        st.session_state.calc_index = cp["calc_index"]
        st.session_state.calc_results = cp["calc_results"]
        st.session_state.calc_daily_sum = cp["calc_daily_sum"]
        st.session_state.calc_params = cp["calc_params"]
        st.toast(f"♻️ Yarım kalan hesaplama kurtarıldı! ({cp['calc_index']} cihaz tamamlanmıştı)", icon="♻️")
    else:
        st.session_state.calc_in_progress = False
        st.session_state.calc_source_df = None
        st.session_state.calc_index = 0
        st.session_state.calc_results = []
        st.session_state.calc_daily_sum = {}
        st.session_state.calc_params = {}


# -------------------------
# Sidebar
# -------------------------
with st.sidebar:
    st.markdown("<h1 style='color: #FF4B4B;'>🛰️ MonsPro</h1>", unsafe_allow_html=True)
    menu = st.radio("Menü Seçimi", ["📊 Yeni Sorgu", "📚 Arşiv"])
    st.divider()

    secrets_ok = ("CLIENT_ID" in st.secrets) and ("TOKEN" in st.secrets)
    if not secrets_ok:
        st.error("st.secrets içinde CLIENT_ID ve TOKEN yok. .streamlit/secrets.toml ekle.")
    else:
        st.success("Credentials OK (st.secrets) ✅")

    price_mode = st.toggle("Manuel Fiyat Girişi", value=False)
    if price_mode:
        st.session_state.geod_p = st.number_input("GEOD Fiyat ($)", value=st.session_state.geod_p, format="%.4f")
    else:
        g_val, u_val = get_live_prices_cached()
        st.session_state.geod_p = g_val
        st.session_state.usd_t = u_val

    if menu == "📊 Yeni Sorgu":
        st.session_state.setdefault("mode", "Ödül Hesapla")
        mode = st.radio("Mod", ["Ödül Hesapla", "Offline Takibi"], index=0 if st.session_state.mode == "Ödül Hesapla" else 1)
        st.session_state.mode = mode

        hesap_tipi = st.radio("Hesap Tipi", ["Normal Hesap", "Superhex Hesabı"], index=0,
                              help="Günlük limit — normal: 12 GEOD (1 Tem 2026'dan itibaren 6), "
                                   "superhex: 48 GEOD (1 Tem 2026'dan itibaren 24). "
                                   "Halving dönem içinde gün bazında otomatik uygulanır.")
        is_superhex = hesap_tipi == "Superhex Hesabı"

        if is_superhex:
            st.info("🔷 **Superhex Modu**: Günlük limit 24 GEOD/cihaz (1 Tem 2026 öncesi günler 48). "
                    "Fazlası Power Mining olarak değerlendirilir.")
        st.caption(f"⚡ Halving: {HALVING_DATE.strftime('%d.%m.%Y')} itibarıyla günlük normal kazanç "
                   f"{DAILY_NORMAL_GEOD_PRE} → {DAILY_NORMAL_GEOD_POST} GEOD. Dönem limiti gün bazında hesaplanır.")

        target_tl = st.number_input("Tamamlanacak TL Tutarı", min_value=0, value=500, step=50)
        low_threshold = st.number_input("Low üretim eşiği (GEOD)", min_value=0, value=LOW_PROD_THRESHOLD_DEFAULT, step=10)

        input_type = st.radio("Yöntem", ["Excel Yükle", "Manuel SN"])
        today = datetime.now()
        start_date = st.date_input("Başlangıç Tarihi (Performans)", value=today.replace(day=1).date())
        end_date = st.date_input("Bitiş Tarihi (Performans)", value=today.date())

        st.caption(
            f"Not: GEOD ödülleri TR {PAYOUT_CUTOFF_TR} civarı yatar. "
            f"Ödül hesaplamada API sorgusu otomatik +1 gün kaydırılır."
        )

        if input_type == "Excel Yükle":
            uploaded_file = st.file_uploader("Excel Yukle", type=["xlsx"])
        else:
            m_manual = st.text_input("Is Ortagi Adi", "Ozel Sorgu")
            sn_manual = st.text_input("Miner Numarasi (SN)")
            kp_manual = st.number_input("Kar Payi Orani (%)", min_value=1, max_value=100, value=25)
            tel_manual = st.text_input("Telefon", "")

        kayit_adi = st.text_input("Arsiv Ismi", value=today.strftime("%d.%m.%Y %H:%M"))

        # HESAPLA butonu: hesaplamayı başlatır, session_state'e kaydeder
        if st.button("HESAPLA", type="primary", use_container_width=True):
            if not secrets_ok:
                st.error("Secrets eksik (CLIENT_ID/TOKEN).")
                st.stop()
            if start_date > end_date:
                st.error("Başlangıç tarihi bitişten büyük olamaz.")
                st.stop()

            source_df = None
            device_df = None

            if input_type == "Excel Yükle" and uploaded_file:
                df_raw = pd.read_excel(uploaded_file, dtype={"Telefon": str, "Miner Numarası": str, "SN": str})
                col_partner = _pick_col(df_raw, ["İş Ortağı", "Is Ortagi", "Musteri", "Partner"])
                col_sn = _pick_col(df_raw, ["Miner Numarası", "Miner Numarasi", "SN", "Serial", "Seri No"])
                col_kp = _pick_col(df_raw, ["Kar Payı", "Kar Payi", "KP", "Kar_Payi"])
                col_tel = _pick_col(df_raw, ["Telefon", "Tel", "Phone"])

                if col_partner is None or col_sn is None or col_kp is None:
                    st.error("Excel içinde İş Ortağı / SN / Kar Payı kolonları bulunamadı.")
                    st.stop()

                col_il = _pick_col(df_raw, ["İl", "Il", "Sehir", "City"])
                col_konum = _pick_col(df_raw, ["Konum", "Lokasyon", "Location", "Adres", "Address"])

                source_df = pd.DataFrame({
                    "Musteri": df_raw[col_partner],
                    "SN": df_raw[col_sn].astype(str).str.strip(),
                    "Kar_Payi": df_raw[col_kp],
                    "Telefon": df_raw[col_tel] if col_tel else None,
                })

                device_df = pd.DataFrame({
                    "SN": df_raw[col_sn].astype(str).str.strip(),
                    "Is_Ortagi": df_raw[col_partner].astype(str),
                    "Il": df_raw[col_il].astype(str) if col_il else "",
                    "Konum": df_raw[col_konum].astype(str) if col_konum else "",
                })

            elif input_type == "Manuel SN" and sn_manual:
                source_df = pd.DataFrame([{
                    "Musteri": m_manual,
                    "SN": str(sn_manual).strip(),
                    "Kar_Payi": kp_manual / 100,
                    "Telefon": tel_manual,
                }])

                device_df = pd.DataFrame([{
                    "SN": str(sn_manual).strip(),
                    "Is_Ortagi": str(m_manual),
                    "Il": "",
                    "Konum": "",
                }])

            if source_df is None or source_df.empty:
                st.warning("Kaynak veri yok.")
                st.stop()

            st.session_state.device_df = device_df

            # Hesaplamayı başlat - parametreleri session_state'e kaydet
            gun_sayisi = (end_date - start_date).days + 1
            max_normal_geod, halving_pre_days, halving_post_days = max_normal_for_period(
                start_date, end_date, is_superhex)
            calc_params = {
                "payout_start": start_date + timedelta(days=1),
                "payout_end": end_date + timedelta(days=1),
                "geod_tl_rate": st.session_state.geod_p * st.session_state.usd_t,
                "thr": float(low_threshold),
                "tgt": float(target_tl),
                "client_id": st.secrets["CLIENT_ID"],
                "token": st.secrets["TOKEN"],
                "start_date": start_date,
                "end_date": end_date,
                "target_tl": target_tl,
                "kayit_adi": kayit_adi,
                "gun_sayisi": gun_sayisi,
                "is_superhex": is_superhex,
                "daily_limit": DAILY_SUPERHEX_GEOD_POST if is_superhex else DAILY_NORMAL_GEOD_POST,
                "max_normal_geod": max_normal_geod,          # halving-bilinçli (gün bazında karma)
                "halving_pre_days": halving_pre_days,
                "halving_post_days": halving_post_days,
            }
            st.session_state.calc_in_progress = True
            st.session_state.calc_source_df = source_df
            st.session_state.calc_index = 0
            st.session_state.calc_results = []
            st.session_state.calc_daily_sum = {}
            st.session_state.calc_params = calc_params

            # İlk checkpoint'i dosyaya yaz (app restart'a karşı)
            save_checkpoint(0, [], {}, calc_params, source_df.to_dict(orient="records"))
            st.rerun()

        # İptal butonu
        if st.session_state.calc_in_progress:
            if st.button("⏹ İPTAL ET", type="secondary", use_container_width=True):
                st.session_state.calc_in_progress = False
                st.session_state.calc_source_df = None
                st.session_state.calc_index = 0
                st.session_state.calc_results = []
                st.session_state.calc_daily_sum = {}
                st.session_state.calc_params = {}
                clear_checkpoint()
                st.rerun()



# -------------------------
# Main UI
# -------------------------
st.divider()
c1, c2, c3 = st.columns(3)
geod_try_val = st.session_state.geod_p * st.session_state.usd_t
c1.metric("GEOD / USD", f"${st.session_state.geod_p:.4f}")
c2.metric("USD / TRY", f"{st.session_state.usd_t:.2f} TL")
c3.metric("GEOD / TRY", f"{geod_try_val:.2f} TL")

# ---- Incremental hesaplama motoru (ana dashboard'da, rerun-safe) ----
if st.session_state.calc_in_progress and st.session_state.calc_source_df is not None:
    source_df = st.session_state.calc_source_df
    params = st.session_state.calc_params
    n = len(source_df)
    dev_i = st.session_state.calc_index

    # Önceki batch'lerden gelen sonuçları hemen göster
    if st.session_state.calc_results:
        st.divider()
        partial_df = pd.DataFrame(st.session_state.calc_results)
        _shex = params.get("is_superhex", False)
        _badge = "🔷 SUPERHEX" if _shex else "🟢 NORMAL"
        st.subheader(f"📊 Canlı Sonuçlar ({len(partial_df)}/{n} cihaz) — {_badge}")

        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Tamamlanan", f"{len(partial_df)} / {n}")
        col_b.metric("Toplam GEOD", f"{partial_df['Toplam_GEOD_Kazanc'].sum():.2f}")
        col_c.metric("Power Mining", f"{partial_df['Power_Mining'].sum():.2f}")
        col_d.metric("Toplam TL", f"{partial_df['Hakedis_TL'].sum():.2f} TL")

        st.dataframe(
            partial_df[["Is_Ortagi", "SN", "Toplam_GEOD_Kazanc", "Power_Mining", "Hesaba_Katilan", "Durum_Etiket", "GEOD_HAKEDIS", "Hakedis_TL"]].style.format({
                "Toplam_GEOD_Kazanc": "{:.2f}",
                "Power_Mining": "{:.2f}",
                "Hesaba_Katilan": "{:.2f}",
                "GEOD_HAKEDIS": "{:.2f}",
                "Hakedis_TL": "{:.2f} TL",
            }),
            use_container_width=True,
            height=min(400, 35 * len(partial_df) + 38),
        )
        st.divider()

    if dev_i < n:
        # Progress bar göster
        st.info(f"⏳ Hesaplama devam ediyor... ({dev_i}/{n} cihaz tamamlandı, şimdi {dev_i+1}-{min(dev_i+10, n)} arası işleniyor)")
        p_bar = st.progress(dev_i / n)

        # Her rerun'da BATCH_SIZE kadar cihaz işle
        BATCH_SIZE = 10
        batch_end = min(dev_i + BATCH_SIZE, n)

        for bi in range(dev_i, batch_end):
            row = source_df.iloc[bi]
            m_name = str(row["Musteri"]).strip()
            sn_no = str(row["SN"]).strip()
            tel = normalize_phone(row.get("Telefon"))
            kp_raw = safe_float(row["Kar_Payi"], 0.0)
            kp_rate = kp_raw / 100 if kp_raw > 1 else kp_raw

            raw_data = get_all_rewards(
                sn_no, params["payout_start"], params["payout_end"],
                params["client_id"], params["token"]
            )

            total_token = 0.0
            daily_sum = st.session_state.calc_daily_sum
            for d in raw_data:
                rw = safe_float(d.get("reward", 0), 0.0)
                total_token += rw
                payout_day = parse_reward_date(d)
                if payout_day:
                    perf_day = payout_day - timedelta(days=1)
                    if params["start_date"] <= perf_day <= params["end_date"]:
                        daily_sum[perf_day] = daily_sum.get(perf_day, 0.0) + rw
            st.session_state.calc_daily_sum = daily_sum

            geod_tl_rate = params["geod_tl_rate"]
            thr = params["thr"]
            tgt = params["tgt"]
            max_normal = params.get("max_normal_geod", total_token)

            # Power Mining ayrımı: maks normal üstü şirkete kalır
            hesaba_katilan = min(total_token, max_normal)
            power_mining = max(0.0, total_token - max_normal)

            mevcut_pay_token = hesaba_katilan * kp_rate
            mevcut_tl = mevcut_pay_token * geod_tl_rate

            eklenen_geod = 0.0
            if hesaba_katilan < thr:
                geod_hakedis = mevcut_pay_token
                durum_etiket = "AZ URETIM"
            else:
                if mevcut_tl < tgt:
                    eksik_tl = tgt - mevcut_tl
                    eklenen_geod = eksik_tl / geod_tl_rate if geod_tl_rate > 0 else 0.0
                    geod_hakedis = mevcut_pay_token + eklenen_geod
                    durum_etiket = "DESTEKLENDI"
                else:
                    geod_hakedis = mevcut_pay_token
                    durum_etiket = "TAM KAZANC"

            st.session_state.calc_results.append({
                "Is_Ortagi": m_name,
                "SN": sn_no,
                "Telefon": tel,
                "Toplam_GEOD_Kazanc": total_token,
                "Power_Mining": power_mining,
                "Hesaba_Katilan": hesaba_katilan,
                "Hakedis_Baz": mevcut_pay_token,
                "EKLENEN_GEOD": eklenen_geod,
                "GEOD_HAKEDIS": geod_hakedis,
                "Hakedis_TL": geod_hakedis * geod_tl_rate,
                "MONSPRO_KAZANC": hesaba_katilan - geod_hakedis + power_mining,
                "Durum_Etiket": durum_etiket
            })

            p_bar.progress((bi + 1) / n)

            if bi < batch_end - 1:
                time.sleep(1.5)

        # Batch bitti, checkpoint'e yaz, rerun ile devam et
        st.session_state.calc_index = batch_end
        save_checkpoint(
            batch_end,
            st.session_state.calc_results,
            st.session_state.calc_daily_sum,
            st.session_state.calc_params,
            st.session_state.calc_source_df.to_dict(orient="records"),
        )
        time.sleep(0.5)
        st.rerun()

    else:
        # Tüm cihazlar tamamlandı - sonuçları oluştur
        df_res = pd.DataFrame(st.session_state.calc_results)
        daily_sum = st.session_state.calc_daily_sum
        daily = (
            pd.DataFrame([{"Performance_Day": k, "GEOD": v} for k, v in daily_sum.items()])
            .sort_values("Performance_Day") if daily_sum else
            pd.DataFrame(columns=["Performance_Day", "GEOD"])
        )

        st.session_state.last_results = {
            "df": df_res,
            "donem": f"{params['start_date'].strftime('%d.%m.%Y')} - {params['end_date'].strftime('%d.%m.%Y')}",
            "ay": params["start_date"].strftime("%B %Y"),
            "kur_geod": st.session_state.geod_p,
            "kur_usd": st.session_state.usd_t,
            "target": params["target_tl"],
            "low_threshold": params["thr"],
            "daily": daily,
            "is_superhex": params.get("is_superhex", False),
            "daily_limit": params.get("daily_limit", DAILY_NORMAL_GEOD),
            "gun_sayisi": params.get("gun_sayisi", 30),
            "max_normal_geod": params.get("max_normal_geod"),
            "halving_pre_days": params.get("halving_pre_days", 0),
            "halving_post_days": params.get("halving_post_days", 0),
        }

        kayit_adi = params.get("kayit_adi", "")
        if kayit_adi:
            st.session_state.arsiv[kayit_adi] = st.session_state.last_results

        # Hesaplama state'ini temizle + checkpoint sil
        clear_checkpoint()
        st.session_state.calc_in_progress = False
        st.session_state.calc_source_df = None
        st.session_state.calc_index = 0
        st.session_state.calc_results = []
        st.session_state.calc_daily_sum = {}
        st.session_state.calc_params = {}
        st.success(f"✅ Hesaplama tamamlandı! {len(df_res)} cihaz işlendi.")
        st.rerun()

if menu == "📊 Yeni Sorgu":
    mode = st.session_state.get("mode", "Ödül Hesapla")

    # ---- OFFLINE TAKİBİ MODU ----
    if mode == "Offline Takibi":
        st.divider()
        st.subheader("🛑 Offline Takibi (getSnInfo)")

        if st.session_state.device_df is None or st.session_state.device_df.empty:
            st.info("Önce Excel/Manuel listeyi girip HESAPLA yap (listeyi oluşturmak için).")
        else:
            get_sn_info_url = st.secrets.get("GET_SN_INFO_URL", "https://consoleresapi.geodnet.com/getSnInfo").strip()

            auto_ok = False
            try:
                from streamlit_autorefresh import st_autorefresh
                st_autorefresh(interval=30 * 60 * 1000, key="offline_autorefresh_30m")
                auto_ok = True
            except Exception:
                auto_ok = False

            colA, colB, colC = st.columns([1, 1, 2])
            do_check = colA.button("OFFLINE CHECK", type="primary", use_container_width=True)
            manual_refresh = colB.button("Yenile", use_container_width=True)
            if not auto_ok:
                colC.warning("30 dk otomatik yenileme (opsiyonel): `pip install streamlit-autorefresh`")

            if do_check or manual_refresh or (st.session_state.offline_results is None):
                if ("CLIENT_ID" not in st.secrets) or ("TOKEN" not in st.secrets):
                    st.error("Secrets eksik (CLIENT_ID/TOKEN).")
                else:
                    with st.spinner("Offline kontrol ediliyor..."):
                        off_df = offline_check_getsninfo(
                            st.session_state.device_df,
                            st.secrets["CLIENT_ID"],
                            st.secrets["TOKEN"],
                            get_sn_info_url
                        )
                    st.session_state.offline_results = {
                        "df": off_df,
                        "checked_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                    }

            if st.session_state.offline_results:
                off_df = st.session_state.offline_results["df"]
                checked_at = st.session_state.offline_results["checked_at"]
                st.caption(f"Son kontrol: **{checked_at}**")

                render_offline_banner(len(off_df))
                if off_df.empty:
                    st.success("Offline/ERROR/UNKNOWN cihaz yok 🎉")
                else:
                    st.dataframe(
                        off_df[["SN", "Is_Ortagi", "Il", "Konum", "Durum", "Son_Guncelleme"]],
                        use_container_width=True,
                        height=380
                    )

    # ---- ÖDÜL HESAPLAMA MODU ----
    else:
        if st.session_state.last_results:
            st.divider()
            res = st.session_state.last_results
            df = res["df"]
            daily = res.get("daily", pd.DataFrame(columns=["Performance_Day", "GEOD"]))

            is_shex = res.get("is_superhex", False)
            d_limit = res.get("daily_limit", DAILY_NORMAL_GEOD)
            gun_s = res.get("gun_sayisi", 30)
            max_norm = res.get("max_normal_geod") or (d_limit * gun_s)
            pre_d = res.get("halving_pre_days", 0)
            post_d = res.get("halving_post_days", 0)
            pre_rate = DAILY_SUPERHEX_GEOD_PRE if is_shex else DAILY_NORMAL_GEOD_PRE
            post_rate = DAILY_SUPERHEX_GEOD_POST if is_shex else DAILY_NORMAL_GEOD_POST

            hesap_badge = "🔷 SUPERHEX" if is_shex else "🟢 NORMAL"
            mod_adi = "Superhex modu" if is_shex else "Normal mod"
            st.subheader(f"📊 Dönem Finansal Özeti — {hesap_badge}")
            if pre_d > 0 and post_d > 0:
                st.caption(f"{mod_adi}: {pre_d} gün × {pre_rate} GEOD (halving öncesi) + "
                           f"{post_d} gün × {post_rate} GEOD (halving sonrası) → Maks **{max_norm} GEOD**/cihaz")
            elif pre_d > 0:
                st.caption(f"{mod_adi}: Günlük limit **{pre_rate} GEOD** (halving öncesi dönem), "
                           f"{gun_s} gün → Maks {max_norm} GEOD/cihaz")
            else:
                st.caption(f"{mod_adi}: Günlük limit **{post_rate} GEOD** "
                           f"(halving sonrası, {HALVING_DATE.strftime('%d.%m.%Y')}+), "
                           f"{gun_s} gün → Maks {max_norm} GEOD/cihaz")

            col_a, col_b, col_c, col_d, col_e = st.columns(5)
            with col_a:
                st.info(f"📅 **Hesap Dönemi:**\n\n{res['donem']}")
            with col_b:
                st.success(f"🛰️ **Total GEOD:**\n\n{df['Toplam_GEOD_Kazanc'].sum():.2f}")
            with col_c:
                pm_total = df['Power_Mining'].sum() if 'Power_Mining' in df.columns else 0.0
                st.metric("⚡ Power Mining", f"{pm_total:.2f} GEOD",
                          help="Günlük limitin üzerindeki kazanç (Power Mining ödülü). PDF raporunda yer almaz.")
            with col_d:
                st.warning(f"💸 **İş Ortağı Ödemesi:**\n\n{df['GEOD_HAKEDIS'].sum():.2f}")
            with col_e:
                st.error(f"📈 **MonsPro Net:**\n\n{df['MONSPRO_KAZANC'].sum():.2f}")

            st.divider()
            st.subheader("📈 Günlük GEOD Üretim Trendi (Performans Günleri)")
            if daily.empty:
                st.info("Trend verisi üretilemedi (API response içinde tarih alanı bulunamadı olabilir).")
            else:
                d2 = daily.copy()
                d2["Performance_Day"] = pd.to_datetime(d2["Performance_Day"])
                d2 = d2.set_index("Performance_Day")
                st.line_chart(d2["GEOD"], height=260)

            st.divider()
            st.header(f"📋 Hakediş Detayları (Hedef: {res['target']} TL)")

            def style_rows(row):
                if row.Hesaba_Katilan < res["low_threshold"]:
                    return ["background-color: #ffffcc; color: #000080; font-weight: bold"] * len(row)
                return [""] * len(row)

            display_cols = ["Is_Ortagi", "SN", "Toplam_GEOD_Kazanc", "Power_Mining", "Hesaba_Katilan",
                            "Durum_Etiket", "Hakedis_Baz", "EKLENEN_GEOD", "GEOD_HAKEDIS", "Hakedis_TL", "MONSPRO_KAZANC"]
            # Sadece mevcut kolonları göster (eski checkpoint uyumluluğu)
            display_cols = [c for c in display_cols if c in df.columns]

            fmt = {
                "Hakedis_TL": "{:.2f} TL",
                "Toplam_GEOD_Kazanc": "{:.2f}",
                "Power_Mining": "{:.2f}",
                "Hesaba_Katilan": "{:.2f}",
                "Hakedis_Baz": "{:.2f}",
                "EKLENEN_GEOD": "{:.2f}",
                "GEOD_HAKEDIS": "{:.2f}",
                "MONSPRO_KAZANC": "{:.2f}",
            }
            fmt = {k: v for k, v in fmt.items() if k in df.columns}

            st.dataframe(
                df[display_cols].style.apply(style_rows, axis=1).format(fmt),
                use_container_width=True
            )

            st.subheader("📲 Rapor Gönderim ve İndirme")
            for i, m_name in enumerate(df["Is_Ortagi"].unique()):
                m_data = df[df["Is_Ortagi"] == m_name]
                tel = str(m_data["Telefon"].iloc[0])

                col_m, col_p, col_w = st.columns([3, 1, 1])
                col_m.write(f"👤 **{m_name}**")

                # ✅ PDF: İl/Konum + Toplam GEOD/TL
                pdf_bytes = create_pdf(
                    m_name,
                    m_data,
                    res["kur_geod"],
                    res["kur_usd"],
                    res["donem"],
                    device_df=st.session_state.device_df
                )
                col_p.download_button("📂 PDF İndir", data=pdf_bytes, file_name=f"{temizle(m_name)}_Hakedis.pdf", key=f"dl_{i}")

                if tel and tel not in ["nan", "None", "", "90"]:
                    msg_text = wp_mesaj_olustur(m_name, m_data, res["donem"], res["kur_geod"], res["kur_usd"])
                    wp_url = f"https://wa.me/{tel}?text={urllib.parse.quote(msg_text)}"
                    col_w.markdown(
                        f'<a href="{wp_url}" target="_blank" style="text-decoration: none;">'
                        f'<button style="background-color: #25D366; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; width: 100%;">'
                        f'💬 WP Gönder</button></a>',
                        unsafe_allow_html=True
                    )
                else:
                    col_w.markdown(
                        '<button disabled style="background-color: #FF4B4B; color: white; border: none; padding: 8px 15px; border-radius: 5px; width: 100%; cursor: not-allowed; opacity: 1;">Telefon No Yok</button>',
                        unsafe_allow_html=True
                    )
        else:
            st.info("Henüz sonuç yok. Sidebar’dan listeyi girip HESAPLA’ya bas.")

else:
    st.header("📚 Arşiv")
    if not st.session_state.arsiv:
        st.info("Arşiv boş.")
    else:
        keys = list(st.session_state.arsiv.keys())[::-1]
        pick = st.selectbox("Kayıt seç", keys)
        if pick:
            st.session_state.last_results = st.session_state.arsiv[pick]
            st.success("Arşiv kaydı yüklendi. Sol menüden Yeni Sorgu → Mod seçebilirsin.")
