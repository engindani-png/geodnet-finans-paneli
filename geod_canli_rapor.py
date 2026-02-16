import streamlit as st
import requests
import time
import binascii
import pandas as pd
from datetime import datetime, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from fpdf import FPDF
import warnings

warnings.filterwarnings('ignore')

# --- GEODNET API VE SECRETS ---
try:
    CLIENT_ID = st.secrets["CLIENT_ID"]
    TOKEN = st.secrets["TOKEN"]
except:
    st.error("Secrets bulunamadi! Lutfen Settings > Secrets kismini kontrol edin.")
    st.stop()

BASE_URL = "https://consoleresapi.geodnet.com"

# --- FONKSIYONLAR ---
def get_live_prices():
    try:
        geod_p = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=geodnet&vs_currencies=usd", timeout=5).json()['geodnet']['usd']
        usd_t = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()['rates']['TRY']
        return geod_p, usd_t
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
        params = {
            "clientId": CLIENT_ID,
            "timeStamp": encrypt_param(ts, TOKEN),
            "sn": encrypt_param(sn, TOKEN),
            "minTime": encrypt_param(curr_start.strftime('%Y-%m-%d'), TOKEN),
            "maxTime": encrypt_param(curr_end.strftime('%Y-%m-%d'), TOKEN)
        }
        try:
            r = requests.get(f"{BASE_URL}/getRewardsTimeLine", params=params, verify=False, timeout=15)
            res = r.json()
            if res.get('statusCode') == 200:
                all_data.extend(res.get('data', []))
        except: pass
        curr_start = curr_end + timedelta(days=1)
    return all_data

def create_pdf(musteri_adi, data_df, g_price, u_try, s_date, e_date):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(190, 10, "GEODNET HAKEDIS RAPORU", ln=True, align='C')
    
    pdf.set_font("helvetica", '', 11)
    pdf.ln(5)
    pdf.cell(95, 8, f"Musteri: {musteri_adi}")
    pdf.cell(95, 8, f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y')}", ln=True, align='R')
    pdf.cell(190, 8, f"Donem: {s_date.strftime('%d.%m.%Y')} - {e_date.strftime('%d.%m.%Y')}", ln=True)
    pdf.cell(190, 8, f"GEOD Fiyat: ${g_price:.4f} | USD Kuru: {u_try:.2f} TL", ln=True)
    pdf.ln(5)
    
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font("helvetica", 'B', 10)
    pdf.cell(50, 10, "Cihaz SN", 1, 0, 'C', True)
    pdf.cell(35, 10, "Top. GEOD", 1, 0, 'C', True)
    pdf.cell(35, 10, "Pay (25%)", 1, 0, 'C', True)
    pdf.cell(35, 10, "Kur (TL)", 1, 0, 'C', True)
    pdf.cell(35, 10, "Toplam (TL)", 1, 1, 'C', True)
    
    pdf.set_font("helvetica", '', 10)
    total_val = 0
    geod_tl = g_price * u_try
    for _, row in data_df.iterrows():
        pdf.cell(50, 10, str(row['SN']), 1)
        pdf.cell(35, 10, f"{row['Top_GEOD']:.2f}", 1, 0, 'C')
        pdf.cell(35, 10, f"{row['Musteri_Pay_Token']:.2f}", 1, 0, 'C')
        pdf.cell(35, 10, f"{geod_tl:.2f}", 1, 0, 'C')
        pdf.cell(35, 10, f"{row['Musteri_Toplam_TL']:.2f} TL", 1, 1, 'C')
        total_val += row['Musteri_Toplam_TL']
    
    pdf.ln(5)
    pdf.set_font("helvetica", 'B', 12)
    pdf.cell(190, 10, f"Genel Toplam: {total_val:.2f} TL", ln=True, align='R')
    
    return bytes(pdf.output())

# --- ARA YUZ ---
st.set_page_config(page_title="GEODNET Finans", layout="wide")
st.title("🛰️ GEODNET Hakedis ve PDF Raporlama")

if 'geod_p' not in st.session_state:
    st.session_state.geod_p, st.session_state.usd_t = get_live_prices()

with st.sidebar:
    st.header("💹 Canli Kurlar")
    st.write(f"GEOD/USD: **${st.session_state.geod_p:.4f}**")
    st.write(f"USD/TRY: **{st.session_state.usd_t:.2f}**")
    if st.button("Kurlari Guncelle"):
        st.session_state.geod_p, st.session_state.usd_t = get_live_prices()
        st.rerun()
    st.divider()
    uploaded_file = st.file_uploader("Dosya Yukle", type=['xlsx', 'csv'])
    start_date = st.date_input("Baslangic", datetime.now() - timedelta(days=31))
    end_date = st.date_input("Bitis", datetime.now())
    process_btn = st.button("HESAPLA", type="primary", use_container_width=True)

if process_btn and uploaded_file:
    try:
        input_df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('.xlsx') else pd.read_csv(uploaded_file)
        input_df.columns = ['Musteri', 'SN'] + list(input_df.columns[2:])
        
        results = []
        geod_tl_rate = st.session_state.geod_p * st.session_state.usd_t
        p_bar = st.progress(0)
        status_text = st.empty()
        
        for index, row in input_df.iterrows():
            m_name = str(row['Musteri']).strip()
            sn_no = str(row['SN']).strip()
            status_text.text(f"Sorgulaniyor: {m_name} - {sn_no}")
            
            raw_data = get_all_rewards(sn_no, start_date, end_date)
            total_token = sum([pd.to_numeric(d['reward'], errors='coerce') or 0 for d in raw_data])
            
            # --- DETAYLI HESAPLAMALAR ---
            token_25 = total_token * 0.25
            tl_25 = token_25 * geod_tl_rate
            
            needs_fix = "EVET" if tl_25 < 500 else "HAYIR"
            fix_tl = max(0, 500 - tl_25)
            fix_token = fix_tl / geod_tl_rate if geod_tl_rate > 0 else 0
            
            total_paid_token = token_25 + fix_token
            net_us_token = total_token - total_paid_token
            
            results.append({
                "Musteri": m_name,
                "SN": sn_no,
                "Top_GEOD": total_token,
                "Musteri_Pay_Token": token_25,
                "Musteri_Pay_TL": tl_25,
                "Tamamlama_Gerekiyor": needs_fix,
                "Tamamlama_TL": fix_tl,
                "Musteri_Toplam_TL": tl_25 + fix_tl,
                "Musteri_Odenecek_Toplam_Token": total_paid_token,
                "Bize_Net_Kalan_Token": net_us_token
            })
            p_bar.progress((index + 1) / len(input_df))

        status_text.success("Hesaplama tamamlandi!")
        res_df = pd.DataFrame(results)
        
        # --- TABLO GOSTERIMI ---
        st.header("📋 Detayli Cihaz Analizi")
        st.dataframe(res_df.style.format({
            "Top_GEOD": "{:.2f}", "Musteri_Pay_Token": "{:.2f}", "Musteri_Pay_TL": "{:.2f} TL",
            "Tamamlama_TL": "{:.2f} TL", "Musteri_Toplam_TL": "{:.2f} TL", 
            "Musteri_Odenecek_Toplam_Token": "{:.2f}", "Bize_Net_Kalan_Token": "{:.2f}"
        }), use_container_width=True)
        
        st.divider()
        st.subheader("📄 Musteri PDF Raporlari")
        cols = st.columns(3)
        for i, m_name in enumerate(res_df['Musteri'].unique()):
            m_data = res_df[res_df['Musteri'] == m_name]
            pdf_bytes = create_pdf(m_name, m_data, st.session_state.geod_p, st.session_state.usd_t, start_date, end_date)
            with cols[i % 3]:
                st.download_button(f"📥 {m_name} PDF", data=pdf_bytes, file_name=f"{m_name}_Rapor.pdf", mime="application/pdf", key=f"p_{i}")
    except Exception as e:
        st.error(f"Hata detayi: {e}")
