import streamlit as st
import requests
import time
import binascii
import pandas as pd
import urllib.parse
from datetime import datetime, timedelta, date
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from fpdf import FPDF
import warnings

# --- GENEL AYARLAR ---
warnings.filterwarnings("ignore")
st.set_page_config(page_title="MonsPro | Operasyonel Portal", layout="wide")

PAYOUT_CUTOFF_TR = "08:30"  # bilgi amaçlı
LOW_PROD_THRESHOLD_DEFAULT = 180

# Tek session: daha hızlı (TCP reuse)
HTTP = requests.Session()


# --- 1) UTIL ---
TR_MAP = str.maketrans({"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ı": "i", "İ": "I", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"})


def temizle(text):
    if text is None:
        return ""
    return str(text).translate(TR_MAP)


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


@st.cache_data(ttl=600, show_spinner=False)
def get_live_prices_cached():
    """10 dk cache: UI aynı, daha az API çağrısı."""
    try:
        res = HTTP.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=geodnet&vs_currencies=usd",
            timeout=5,
        ).json()
        geod_p = float(res["geodnet"]["usd"])
        usd_t = float(
            HTTP.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()["rates"]["TRY"]
        )
        return geod_p, usd_t
    except Exception:
        return 0.1500, 33.00


def encrypt_param(data, key):
    # Eski çalışan mantık: TOKEN -> 16 byte sabitlenip hem key hem iv
    k_fixed = str(key).rjust(16, "0")[:16].encode("utf-8")
    cipher = AES.new(k_fixed, AES.MODE_CBC, iv=k_fixed)
    padded_data = pad(str(data).encode("utf-8"), 16)
    return binascii.hexlify(cipher.encrypt(padded_data)).decode("utf-8")


def parse_reward_date(item: dict):
    """
    API item içinde tarih alanını yakalamaya çalışır.
    Bulamazsa None.
    """
    candidates = ("date", "day", "rewardDate", "createDate", "time", "timestamp", "ts")
    for k in candidates:
        v = item.get(k)
        if not v:
            continue

        # epoch ms / s
        try:
            iv = int(v)
            if iv > 10_000_000_000:  # ms
                return datetime.utcfromtimestamp(iv / 1000).date()
            if iv > 1_000_000_000:  # s
                return datetime.utcfromtimestamp(iv).date()
        except Exception:
            pass

        # string date
        if isinstance(v, str) and len(v) >= 10:
            try:
                return datetime.strptime(v[:10], "%Y-%m-%d").date()
            except Exception:
                pass

    return None


def get_all_rewards(sn: str, payout_start: date, payout_end: date, client_id: str, token: str):
    """
    30 günlük parçalı sorgu (aynı endpoint/paramlar).
    """
    all_data = []
    curr = payout_start
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

        try:
            r = HTTP.get(
                "https://consoleresapi.geodnet.com/getRewardsTimeLine",
                params=params,
                verify=False,
                timeout=15,
            )
            res = r.json()
            if res.get("statusCode") == 200:
                data = res.get("data", [])
                if data:
                    all_data.extend(data)
        except Exception:
            pass

        curr = curr_end + timedelta(days=1)

    return all_data


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


# --- PDF / WP (aynı görsel/format) ---
class _PDF(FPDF):
    pass


def create_pdf(m_name, data_df, g_price, u_try, s_date):
    pdf = _PDF()
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
        pdf.cell(30, 10, str(row["SN"]), 1)
        pdf.cell(20, 10, f"{row['Toplam_GEOD_Kazanc']:.2f}", 1)
        pdf.cell(25, 10, temizle(row["Durum_Etiket"]), 1, 0, "C")
        pdf.cell(25, 10, f"{row['Hakedis_Baz']:.2f}", 1)
        pdf.cell(25, 10, f"{row['EKLENEN_GEOD']:.2f}", 1)
        pdf.cell(30, 10, f"{row['GEOD_HAKEDIS']:.2f}", 1)
        pdf.cell(35, 10, f"{row['Hakedis_TL']:.2f} TL", 1, 1, "C")

    pdf.ln(5)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(190, 10, f"Genel Toplam: {data_df['Hakedis_TL'].sum():.2f} TL", ln=True, align="R")
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
        msg += f"{simge} *Miner:* {row['SN']}\n"
        msg += f"   └ Kazanc: {row['Toplam_GEOD_Kazanc']:.2f} GEOD\n"
        if row["EKLENEN_GEOD"] > 0:
            msg += f"   └ Destek: +{row['EKLENEN_GEOD']:.2f} GEOD\n"
        msg += f"   └ *Hakedis:* {row['Hakedis_TL']:.2f} TL\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n"
    msg += f"*💳 TOPLAM ODEME: {m_data['Hakedis_TL'].sum():.2f} TL*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🚀 *MonsPro Team*"
    return msg


# --- 2) SESSION STATE ---
if "arsiv" not in st.session_state:
    st.session_state.arsiv = {}
if "last_results" not in st.session_state:
    st.session_state.last_results = None
if "geod_p" not in st.session_state:
    g_val, u_val = get_live_prices_cached()
    st.session_state.geod_p = g_val
    st.session_state.usd_t = u_val


# --- 3) SIDEBAR (görsel aynı) ---
with st.sidebar:
    st.markdown("<h1 style='color: #FF4B4B;'>🛰️ MonsPro</h1>", unsafe_allow_html=True)
    menu = st.radio("Menü Seçimi", ["📊 Yeni Sorgu", "📚 Arşiv"])
    st.divider()

    # Secrets kontrolü
    secrets_ok = ("CLIENT_ID" in st.secrets) and ("TOKEN" in st.secrets)
    if not secrets_ok:
        st.error("st.secrets içinde CLIENT_ID ve TOKEN yok. .streamlit/secrets.toml ekle.")
    else:
        st.success("Credentials OK (st.secrets) ✅")

    price_mode = st.toggle("Manuel Fiyat Girişi", value=False)
    if price_mode:
        st.session_state.geod_p = st.number_input("GEOD Fiyat ($)", value=st.session_state.geod_p, format="%.4f")
    else:
        # cache’den tazele (sessiz)
        g_val, u_val = get_live_prices_cached()
        st.session_state.geod_p = g_val
        st.session_state.usd_t = u_val

    if menu == "📊 Yeni Sorgu":
        target_tl = st.number_input("Tamamlanacak TL Tutarı", min_value=0, value=500, step=50)
        low_threshold = st.number_input("Offline/Low eşiği (GEOD)", min_value=0, value=LOW_PROD_THRESHOLD_DEFAULT, step=10)

        input_type = st.radio("Yöntem", ["Excel Yükle", "Manuel SN"])

        today = datetime.now()
        start_date = st.date_input("Başlangıç Tarihi (Performans)", value=today.replace(day=1).date())
        end_date = st.date_input("Bitiş Tarihi (Performans)", value=today.date())

        st.caption(
            f"Not: GEOD ödülleri TR {PAYOUT_CUTOFF_TR} civarı yatar. "
            f"Bu yüzden API sorgusu otomatik +1 gün kaydırılır (payout day)."
        )

        if input_type == "Excel Yükle":
            uploaded_file = st.file_uploader("Excel Yukle", type=["xlsx"])
        else:
            m_manual = st.text_input("Is Ortagi Adi", "Ozel Sorgu")
            sn_manual = st.text_input("Miner Numarasi (SN)")
            kp_manual = st.number_input("Kar Payi Orani (%)", min_value=1, max_value=100, value=25)
            tel_manual = st.text_input("Telefon", "")

        kayit_adi = st.text_input("Arsiv Ismi", value=today.strftime("%d.%m.%Y %H:%M"))

        if st.button("HESAPLA", type="primary", use_container_width=True):
            if not secrets_ok:
                st.error("Secrets eksik. CLIENT_ID ve TOKEN olmadan sorgu yapılamaz.")
                st.stop()

            if start_date > end_date:
                st.error("Başlangıç tarihi bitişten büyük olamaz.")
                st.stop()

            # Kaynak dataframe
            source_df = None
            if input_type == "Excel Yükle" and uploaded_file:
                df_raw = pd.read_excel(uploaded_file, dtype={"Telefon": str, "Miner Numarası": str})
                source_df = pd.DataFrame(
                    {
                        "Musteri": df_raw["İş Ortağı"],
                        "SN": df_raw["Miner Numarası"],
                        "Kar_Payi": df_raw["Kar Payı"],
                        "Telefon": df_raw.get("Telefon", None),
                    }
                )
            elif input_type == "Manuel SN" and sn_manual:
                source_df = pd.DataFrame(
                    [{"Musteri": m_manual, "SN": sn_manual, "Kar_Payi": kp_manual / 100, "Telefon": tel_manual}]
                )

            if source_df is None or source_df.empty:
                st.warning("Kaynak veri yok. Excel yükle veya manuel SN gir.")
                st.stop()

            # 08:30 payout mantığı (performans -> payout +1 gün)
            payout_start = start_date + timedelta(days=1)
            payout_end = end_date + timedelta(days=1)

            client_id = st.secrets["CLIENT_ID"]
            token = st.secrets["TOKEN"]

            geod_tl_rate = st.session_state.geod_p * st.session_state.usd_t
            thr = float(low_threshold)
            tgt = float(target_tl)

            results = []
            daily_sum = {}  # perf_day -> geod toplam (trend için hızlı)

            p_bar = st.progress(0)
            n = len(source_df)

            for idx, row in source_df.iterrows():
                m_name = str(row["Musteri"]).strip()
                sn_no = str(row["SN"]).strip()

                # Telefon normalize (eski mantık aynı)
                tel = str(row["Telefon"]).replace(".0", "").strip() if row.get("Telefon") is not None else ""
                if tel.startswith("5"):
                    tel = "90" + tel
                elif tel.startswith("0"):
                    tel = "9" + tel

                # KP normalize (eski mantık aynı)
                kp_raw = safe_float(row["Kar_Payi"], 0.0)
                kp_rate = kp_raw / 100 if kp_raw > 1 else kp_raw

                raw_data = get_all_rewards(sn_no, payout_start, payout_end, client_id, token)

                total_token = 0.0
                for d in raw_data:
                    rw = safe_float(d.get("reward", 0), 0.0)
                    total_token += rw

                    payout_day = parse_reward_date(d)
                    if payout_day:
                        perf_day = payout_day - timedelta(days=1)
                        if start_date <= perf_day <= end_date:
                            daily_sum[perf_day] = daily_sum.get(perf_day, 0.0) + rw

                mevcut_pay_token = total_token * kp_rate
                mevcut_tl = mevcut_pay_token * geod_tl_rate

                eklenen_geod = 0.0
                if total_token < thr:
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

                results.append(
                    {
                        "Is_Ortagi": m_name,
                        "SN": sn_no,
                        "Telefon": tel,
                        "Toplam_GEOD_Kazanc": total_token,
                        "Hakedis_Baz": mevcut_pay_token,
                        "EKLENEN_GEOD": eklenen_geod,
                        "GEOD_HAKEDIS": geod_hakedis,
                        "Hakedis_TL": geod_hakedis * geod_tl_rate,
                        "MONSPRO_KAZANC": total_token - geod_hakedis,
                        "Durum_Etiket": durum_etiket,
                    }
                )

                p_bar.progress((idx + 1) / n)

            df_res = pd.DataFrame(results)
            df_res["OFFLINE"] = (df_res["Toplam_GEOD_Kazanc"] < thr) | (df_res["Toplam_GEOD_Kazanc"] <= 0)

            if daily_sum:
                daily = pd.DataFrame(
                    [{"Performance_Day": k, "GEOD": v} for k, v in daily_sum.items()]
                ).sort_values("Performance_Day")
            else:
                daily = pd.DataFrame(columns=["Performance_Day", "GEOD"])

            st.session_state.last_results = {
                "df": df_res,
                "donem": f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}",
                "ay": start_date.strftime("%B %Y"),
                "kur_geod": st.session_state.geod_p,
                "kur_usd": st.session_state.usd_t,
                "target": target_tl,
                "low_threshold": thr,
                "daily": daily,
            }

            if kayit_adi:
                st.session_state.arsiv[kayit_adi] = st.session_state.last_results


# --- 4) ANA EKRAN (görsel aynı) ---
st.divider()
c1, c2, c3 = st.columns(3)
geod_try_val = st.session_state.geod_p * st.session_state.usd_t
c1.metric("GEOD / USD", f"${st.session_state.geod_p:.4f}")
c2.metric("USD / TRY", f"{st.session_state.usd_t:.2f} TL")
c3.metric("GEOD / TRY", f"{geod_try_val:.2f} TL")

if st.session_state.last_results:
    st.divider()
    res = st.session_state.last_results
    df = res["df"]
    daily = res.get("daily", pd.DataFrame(columns=["Performance_Day", "GEOD"]))

    offline_df = df[df["OFFLINE"] == True].copy()
    render_offline_banner(len(offline_df))

    st.subheader("📊 Dönem Finansal Özeti")
    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.info(f"📅 **Hesap Dönemi (Performans):**\n\n{res['donem']}")
    with col_b:
        st.success(f"🛰️ **Total GEOD Kazancı:**\n\n{df['Toplam_GEOD_Kazanc'].sum():.2f}")
    with col_c:
        st.warning(f"💸 **Total İş Ortağı Ödemesi:**\n\n{df['GEOD_HAKEDIS'].sum():.2f}")
    with col_d:
        st.error(f"📈 **Monspor Net GEOD Kazancı:**\n\n{df['MONSPRO_KAZANC'].sum():.2f}")

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
        if row.Toplam_GEOD_Kazanc < res["low_threshold"]:
            return ["background-color: #ffdddd; color: #7a0000; font-weight: bold"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df.style.apply(style_rows, axis=1).format(
            {
                "Hakedis_TL": "{:.2f} TL",
                "Toplam_GEOD_Kazanc": "{:.2f}",
                "Hakedis_Baz": "{:.2f}",
                "EKLENEN_GEOD": "{:.2f}",
                "GEOD_HAKEDIS": "{:.2f}",
                "MONSPRO_KAZANC": "{:.2f}",
            }
        ),
        use_container_width=True,
    )

    st.divider()
    st.subheader("🚨 Offline / Low Üretim Cihaz Listesi")
    if offline_df.empty:
        st.success("Offline/Low cihaz yok 🎉")
    else:
        show_cols = ["Is_Ortagi", "SN", "Telefon", "Toplam_GEOD_Kazanc", "Durum_Etiket", "Hakedis_TL"]
        st.dataframe(
            offline_df[show_cols].sort_values("Toplam_GEOD_Kazanc", ascending=True),
            use_container_width=True,
        )

    st.subheader("📲 Rapor Gönderim ve İndirme")
    for i, m_name in enumerate(df["Is_Ortagi"].unique()):
        m_data = df[df["Is_Ortagi"] == m_name]
        tel = str(m_data["Telefon"].iloc[0])

        col_m, col_p, col_w = st.columns([3, 1, 1])
        col_m.write(f"👤 **{m_name}**")

        pdf_bytes = create_pdf(m_name, m_data, res["kur_geod"], res["kur_usd"], res["donem"])
        col_p.download_button("📂 PDF İndir", data=pdf_bytes, file_name=f"{temizle(m_name)}_Hakedis.pdf", key=f"dl_{i}")

        if tel and tel not in ["nan", "None", "", "90"]:
            msg_text = wp_mesaj_olustur(m_name, m_data, res["donem"], res["kur_geod"], res["kur_usd"])
            wp_url = f"https://wa.me/{tel}?text={urllib.parse.quote(msg_text)}"
            col_w.markdown(
                f'<a href="{wp_url}" target="_blank" style="text-decoration: none;">'
                f'<button style="background-color: #25D366; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; width: 100%;">'
                f'💬 WP Gönder</button></a>',
                unsafe_allow_html=True,
            )
        else:
            col_w.markdown(
                '<button disabled style="background-color: #FF4B4B; color: white; border: none; padding: 8px 15px; border-radius: 5px; width: 100%; cursor: not-allowed; opacity: 1;">'
                "Telefon No Yok</button>",
                unsafe_allow_html=True,
            )
