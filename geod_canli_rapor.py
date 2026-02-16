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
    st.error("Secrets bulunamadı! Lütfen Settings > Secrets kısmını kontrol edin.")
    st.stop()

BASE_URL = "https://consoleresapi.geodnet.com"

# --- FONKSİYONLAR ---
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
    """30 gün kısıtlamasını aşmak için parçalı sorgu yapar"""
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

# --- PDF OLUŞTURMA ---
def create_pdf(musteri, data_df, g_price, u_try):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, "GEODNET Hakedis Raporu", ln=True, align='C')
    pdf.set_font("Arial", '', 12)
    pdf.cell(190, 10, f"Musteri: {musteri}", ln=True)
    pdf.cell(190, 10, f"Tarih: {datetime.now().strftime('%Y-%m-%d')}", ln=True)
    pdf.ln(5)
    
    # Tablo Başlığı
    pdf.set_fill_color(200, 220, 255)
    pdf.cell(60, 10, "Cihaz SN", 1, 0, 'C', True)
    pdf.cell(40, 10, "Top. GEOD", 1, 0, 'C', True)
    pdf.cell(40, 10, "Pay (%25)", 1, 0, 'C', True)
    pdf.cell(50, 10, "Toplam TL", 1, 1, 'C', True)
    
    total_payment = 0
    for _, row in data_df.iterrows():
        pdf.cell(60, 10, str(row['SN']), 1)
        pdf.cell(40, 10, f"{row['Top. GEOD']:.2f}", 1)
        pdf.cell(40, 10, f"{row['Musteri %25']:.2f}", 1)
        pdf.cell(50, 10, f"{row['Musteri Toplam TL']:.2f} TL", 1)
        total_payment += row['Musteri Toplam TL']
        pdf.ln(0)
    
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(190, 10, f"Genel Toplam Odenecek: {total_payment:.2f} TL", ln=True)
    return pdf.output(dest='S').encode('latin-1')

# --- ARA YÜZ ---
st.set_page_config(page_title="GEODNET Finans", layout="wide")
st.title("🛰️ GEODNET Gelir Garanti ve PDF Rapor Sistemi")

if 'geod_p' not in st.session_state:
    st.session_state.geod_p, st.session_state.usd_t = get_live_prices()

with st.sidebar:
    st.metric("GEOD/USD", f"${st.session_state.geod_p:.4f}")
    st.metric("USD/TRY", f"₺{st.session_state.usd_t:.2f}")
    uploaded_file = st.file_uploader("Cihaz Listesi", type=['xlsx', 'csv'])
    start_date = st.date_input("Baslangic", datetime.now() - timedelta(days=31))
    end_date = st.date_input("Bitis", datetime.now())
    process_btn = st.button("HESAPLA")

if process_btn and uploaded_file:
    input_df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('.xlsx') else pd.read_csv(uploaded_file)
    input_df.columns = ['Musteri', 'SN'] + list(input_df.columns[2:])
    
    results = []
    geod_tl = st.session_state.geod_p * st.session_state.usd_t
    
    for _, row in input_df.iterrows():
        raw_data = get_all_rewards(str(row['SN']).strip(), start_date, end_date)
        total_token = sum([pd.to_numeric(d['reward'], errors='coerce') or 0 for d in raw_data])
        
        c_share = total_token * 0.25
        c_tl = c_share * geod_tl
        fix_tl = max(0, 500 - c_tl)
        total_tl = c_tl + fix_tl
        
        results.append({
            "Müşteri": row['Musteri'], "SN": row['SN'], "Top. GEOD": total_token,
            "Musteri %25": c_share, "Musteri Toplam TL": total_tl
        })
    
    res_df = pd.DataFrame(results)
    st.dataframe(res_df)
    
    st.divider()
    st.subheader("📄 Müşteri Özel PDF Raporları")
    for musteri in res_df['Müşteri'].unique():
        m_data = res_df[res_df['Müşteri'] == musteri]
        pdf_bytes = create_pdf(musteri, m_data, st.session_state.geod_p, st.session_state.usd_t)
        st.download_button(f"📥 {musteri} Raporunu İndir", data=pdf_bytes, file_name=f"{musteri}_Rapor.pdf", mime="application/pdf")
