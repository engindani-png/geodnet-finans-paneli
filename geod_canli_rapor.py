import streamlit as st
import requests
import time
import binascii
import pandas as pd
import urllib.parse
import re
from datetime import datetime, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from fpdf import FPDF
import warnings

# --- GENEL AYARLAR ---
warnings.filterwarnings('ignore')
st.set_page_config(page_title="MonsPro | Operasyonel Portal", layout="wide")

# --- 1. FONKSİYONLAR ---
def tel_no_temizle(tel):
    """Numaradaki tüm rakam dışı karakterleri temizler ve formatı düzeltir."""
    if tel is None: return None
    # Sadece rakamları tut
    temiz_tel = re.sub(r'\D', '', str(tel))
    # Eğer numara 0 ile başlıyorsa (0532...) 0'ı kaldır ve 90 ekle
    if temiz_tel.startswith('0'):
        temiz_tel = '90' + temiz_tel[1:]
    # Eğer numara doğrudan 5 ile başlıyorsa (532...) 90 ekle
    elif temiz_tel.startswith('5') and len(temiz_tel) == 10:
        temiz_tel = '90' + temiz_tel
    return temiz_tel

def temizle(text):
    if text is None: return ""
    mapping = {"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ı": "i", "İ": "I", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"}
    for key, val in mapping.items():
        text = str(text).replace(key, val)
    return text

def get_live_prices():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=geodnet&vs_currencies=usd", timeout=5).json()
        return res['geodnet']['usd'], requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()['rates']['TRY']
    except:
        return 0.1500, 33.00

def encrypt_param(data, key):
    k_fixed = str(key).rjust(16, '0')[:16].encode('utf-8')
    cipher = AES.new(k_fixed, AES.MODE_CBC, iv=k_fixed)
    padded_data = pad(str(data).encode('utf-8'), 16)
    return binascii.hexlify(cipher.encrypt(padded_data)).decode('utf-8')

def get_all_rewards(sn, start, end):
    all_data = []
    curr_start = start
    while curr_start <= end:
        curr_end = min(curr_start + timedelta(days=29), end)
        ts = str(int(time.time() * 1000))
        params = {"clientId": st.secrets["CLIENT_ID"], "timeStamp": encrypt_param(ts, st.secrets["TOKEN"]), "sn": encrypt_param(sn, st.secrets["TOKEN"]), "minTime": encrypt_param(curr_start.strftime('%Y-%m-%d'), st.secrets["TOKEN"]), "maxTime": encrypt_param(curr_end.strftime('%Y-%m-%d'), st.secrets["TOKEN"])}
        try:
            r = requests.get("https://consoleresapi.geodnet.com/getRewardsTimeLine", params=params, verify=False, timeout=15)
            res = r.json()
            if res.get('statusCode') == 200: all_data.extend(res.get('data', []))
        except: pass
        curr_start = curr_end + timedelta(days=1)
    return all_data

def create_pdf(m_name, data_df, g_price, u_try, s_date):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 14)
    pdf.cell(190, 10, "MonsPro GEODNET HAKEDIS RAPORU", ln=True, align='C')
    pdf.set_font("helvetica", '', 10)
    pdf.ln(5)
    pdf.cell(95, 8, f"Is Ortagi: {temizle(m_name)}")
    pdf.cell(95, 8, f"Donem: {s_date}", ln=True, align='R')
    pdf.cell(190, 8, f"GEOD Fiyat: ${g_price:.4f} | Kur: {u_try:.2f} TL", ln=True)
    pdf.ln(5)
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("helvetica", 'B', 7)
    pdf.cell(30, 10, "Miner No", 1, 0, 'C', True)
    pdf.cell(20, 10, "Kazanc", 1, 0, 'C', True)
    pdf.cell(25, 10, "Durum", 1, 0, 'C', True)
    pdf.cell(25, 10, "Hakedis", 1, 0, 'C', True)
    pdf.cell(25, 10, "Eklenen", 1, 0, 'C', True)
    pdf.cell(30, 10, "Top.GEOD", 1, 0, 'C', True)
    pdf.cell(35, 10, "Tutar(TL)", 1, 1, 'C', True)
    pdf.set_font("helvetica", '', 7)
    for _, row in data_df.iterrows():
        pdf.cell(30, 10, str(row['SN']), 1)
        pdf.cell(20, 10, f"{row['Toplam_GEOD_Kazanc']:.2f}", 1)
        pdf.cell(25, 10, temizle(row['Durum_Etiket']), 1, 0, 'C')
        pdf.cell(25, 10, f"{row['Hakedis_Baz']:.2f}", 1)
        pdf.cell(25, 10, f"{row['EKLENEN_GEOD']:.2f}", 1)
        pdf.cell(30, 10, f"{row['GEOD_HAKEDIS']:.2f}", 1)
        pdf.cell(35, 10, f"{row['Hakedis_TL']:.2f} TL", 1, 1, 'C')
    pdf.ln(5)
    pdf.set_font("helvetica", 'B', 10)
    pdf.cell(190, 10, f"Genel Toplam: {data_df['Hakedis_TL'].sum():.2f} TL", ln=True, align='R')
    return bytes(pdf.output())

def wp_mesaj_olustur(m_name, m_data, donem, kur_geod, kur_usd):
    msg = f"*📄 MonsPro GEODNET Hakedis Raporu*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n"
    msg += f"*👤 Is Ortagi:* {temizle(m_name)}\n"
    msg += f"*📅 Donem:* {donem}\n"
    msg += f"*💰 Anlik Kur:* 1 GEOD = ${kur_geod:.4f} ({kur_geod * kur_usd:.2f} TL)\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n\n"
    for _, row in m_data.iterrows():
        simge = "✅" if row['Durum_Etiket'] == "TAM KAZANC" else "🎁" if row['Durum_Etiket'] == "DESTEKLENDI" else "⚠️"
        msg += f"{simge} *Miner:* {row['SN']}\n"
        msg += f"   └ Kazanc: {row['Toplam_GEOD_Kazanc']:.2f} GEOD\n"
        if row['EKLENEN_GEOD'] > 0:
            msg += f"   └ Destek: +{row['EKLENEN_GEOD']:.2f} GEOD\n"
        msg += f"   └ *Hakedis:* {row['Hakedis_TL']:.2f} TL\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n"
    msg += f"*💳 TOPLAM ODEME: {m_data['Hakedis_TL'].sum():.2f} TL*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🚀 *MonsPro Team*"
    return msg

# --- 2. SESSION STATE ---
if 'arsiv' not in st.session_state: st.session_state.arsiv = {}
if 'last_results' not in st.session_state: st.session_state.last_results = None
if 'geod_p' not in st.session_state:
    g_val, u_val = get_live_prices()
    st.session_state.geod_p, st.session_state.usd_t = g_val, u_val

# --- 3. SIDEBAR ---
with st.sidebar:
    st.markdown("<h1 style='color: #FF4B4B;'>🛰️ MonsPro</h1>", unsafe_allow_html=True)
    menu = st.radio("Menü Seçimi", ["📊 Yeni Sorgu", "📚 Arşiv"])
    st.divider()
    
    price_mode = st.toggle("Manuel Fiyat Girişi", value=False)
    if price_mode:
        st.session_state.geod_p = st.number_input("GEOD Fiyat ($)", value=st.session_state.geod_p, format="%.4f")
    
    if menu == "📊 Yeni Sorgu":
        target_tl = st.number_input("Tamamlanacak TL Tutarı", min_value=0, value=500, step=50)
        input_type = st.radio("Yöntem", ["Excel Yükle", "Manuel SN"])
        
        today = datetime.now()
        start_date = st.date_input("Başlangıç Tarihi", value=today.replace(day=1))
        end_date = st.date_input("Bitiş Tarihi", value=today)
        
        if input_type == "Excel Yükle":
            uploaded_file = st.file_uploader("Excel Yukle", type=['xlsx'])
        else:
            m_manual = st.text_input("Is Ortagi Adi", "Ozel Sorgu")
            sn_manual = st.text_input("Miner Numarasi (SN)")
            kp_manual = st.number_input("Kar Payi Orani (%)", min_value=1, max_value=100, value=25)
            tel_manual = st.text_input("Telefon", "")
            
        kayit_adi = st.text_input("Arsiv Ismi", value=today.strftime("%d.%m.%Y %H:%M"))
        
        if st.button("HESAPLA", type="primary", use_container_width=True):
            source_df = None
            if input_type == "Excel Yükle" and uploaded_file:
                df_raw = pd.read_excel(uploaded_file)
                source_df = pd.DataFrame({'Musteri': df_raw['İş Ortağı'], 'SN': df_raw['Miner Numarası'], 'Kar_Payi': df_raw['Kar Payı'], 'Telefon': df_raw.get('Telefon', None)})
            elif input_type == "Manuel SN" and sn_manual:
                source_df = pd.DataFrame([{'Musteri': m_manual, 'SN': sn_manual, 'Kar_Payi': kp_manual/100, 'Telefon': tel_manual}])
            
            if source_df is not None:
                results = []
                geod_tl_rate = st.session_state.geod_p * st.session_state.usd_t
                p_bar = st.progress(0)
                for index, row in source_df.iterrows():
                    m_name, sn_no, tel_raw = str(row['Musteri']).strip(), str(row['SN']).strip(), row['Telefon']
                    # NUMARA TEMİZLEME BURADA YAPILIYOR
                    tel = tel_no_temizle(tel_raw)
                    
                    kp_raw = float(row['Kar_Payi'])
                    kp_rate = kp_raw / 100 if kp_raw > 1 else kp_raw
                    raw_data = get_all_rewards(sn_no, start_date, end_date)
                    total_token = sum([pd.to_numeric(d['reward'], errors='coerce') or 0 for d in raw_data])
                    
                    mevcut_pay_token = total_token * kp_rate
                    mevcut_tl = mevcut_pay_token * geod_tl_rate
                    eklenen_geod = 0
                    
                    if total_token < 180:
                        geod_hakedis = mevcut_pay_token
                        durum_etiket = "AZ URETIM"
                    else:
                        if mevcut_tl < target_tl:
                            eksik_tl = target_tl - mevcut_tl
                            eklenen_geod = eksik_tl / geod_tl_rate if geod_tl_rate > 0 else 0
                            geod_hakedis = mevcut_pay_token + eklenen_geod
                            durum_etiket = "DESTEKLENDI"
                        else:
                            geod_hakedis = mevcut_pay_token
                            durum_etiket = "TAM KAZANC"

                    results.append({
                        "Is_Ortagi": m_name, "SN": sn_no, "Telefon": tel, "Toplam_GEOD_Kazanc": total_token,
                        "Hakedis_Baz": mevcut_pay_token, "EKLENEN_GEOD": eklenen_geod,
                        "GEOD_HAKEDIS": geod_hakedis, "Hakedis_TL": geod_hakedis * geod_tl_rate,
                        "MONSPRO_KAZANC": total_token - geod_hakedis, "Durum_Etiket": durum_etiket
                    })
                    p_bar.progress((index + 1) / len(source_df))
                
                st.session_state.last_results = {
                    "df": pd.DataFrame(results), 
                    "donem": f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}",
                    "ay": start_date.strftime("%B %Y"), 
                    "kur_geod": st.session_state.geod_p, 
                    "kur_usd": st.session_state.usd_t, 
                    "target": target_tl
                }
                if kayit_adi: st.session_state.arsiv[kayit_adi] = st.session_state.last_results

# --- 4. ANA EKRAN ---
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
    
    st.subheader("📊 Dönem Finansal Özeti")
    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a: st.info(f"📅 **Hesap Dönemi:**\n\n{res['ay']}")
    with col_b: st.success(f"🛰️ **Total GEOD Kazancı:**\n\n{df['Toplam_GEOD_Kazanc'].sum():.2f}")
    with col_c: st.warning(f"💸 **Total İş Ortağı Ödemesi:**\n\n{df['GEOD_HAKEDIS'].sum():.2f}")
    with col_d: st.error(f"📈 **Monspor Net GEOD Kazancı:**\n\n{df['MONSPRO_KAZANC'].sum():.2f}")
    
    st.divider()
    st.header(f"📋 Hakediş Detayları (Hedef: {res['target']} TL)")
    
    def style_rows(row):
        if row.Toplam_GEOD_Kazanc < 180:
            return ['background-color: #ffffcc; color: #000080; font-weight: bold'] * len(row)
        return [''] * len(row)

    st.dataframe(df.style.apply(style_rows, axis=1).format({
        "Hakedis_TL": "{:.2f} TL", "Toplam_GEOD_Kazanc": "{:.2f}", 
        "Hakedis_Baz": "{:.2f}", "EKLENEN_GEOD": "{:.2f}", 
        "GEOD_HAKEDIS": "{:.2f}", "MONSPRO_KAZANC": "{:.2f}"
    }), use_container_width=True)
    
    st.subheader("📲 Rapor Gönderim ve İndirme")
    for i, m_name in enumerate(df['Is_Ortagi'].unique()):
        m_data = df[df['Is_Ortagi'] == m_name]
        tel = str(m_data['Telefon'].iloc[0]).strip()
        
        col_m, col_p, col_w = st.columns([3, 1, 1])
        col_m.write(f"👤 **{m_name}**")
        
        pdf_bytes = create_pdf(m_name, m_data, res["kur_geod"], res["kur_usd"], res["donem"])
        col_p.download_button("📂 PDF İndir", data=pdf_bytes, file_name=f"{temizle(m_name)}_Hakedis.pdf", key=f"dl_{i}")
        
        if tel and tel not in ["", "nan", "None", "90"]:
            msg_text = wp_mesaj_olustur(m_name, m_data, res['donem'], res['kur_geod'], res['kur_usd'])
            encoded_msg = urllib.parse.quote(msg_text)
            # UYGULAMA PROTOKOLÜ
            wp_app_url = f"whatsapp://send?phone={tel}&text={encoded_msg}"
            
            col_w.markdown(f'<a href="{wp_app_url}"><button style="background-color: #25D366; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; width: 100%;">💬 Uygulama Aç</button></a>', unsafe_allow_html=True)
        else:
            col_w.markdown(f'<button disabled style="background-color: #FF4B4B; color: white; border: none; padding: 8px 15px; border-radius: 5px; width: 100%; cursor: not-allowed; opacity: 1;">Numara Hatalı</button>', unsafe_allow_html=True)
