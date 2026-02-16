import streamlit as st
import requests
import time
import binascii
import pandas as pd
from datetime import datetime, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import warnings

warnings.filterwarnings('ignore')

# --- GEODNET API AYARLARI ---
try:
    # Streamlit Secrets üzerinden çekiyoruz
    CLIENT_ID = st.secrets["CLIENT_ID"] [cite: 5, 6]
    TOKEN = st.secrets["TOKEN"] [cite: 6, 7]
except Exception as e:
    st.error("Secrets (Hassas Bilgiler) bulunamadı! Lütfen Streamlit panelinden ayarları kontrol edin.") [cite: 166]
    st.stop()

# Bu adres dökümandaki yapının güncel halidir
BASE_URL = "https://consoleresapi.geodnet.com" [cite: 32]

# --- CANLI KUR FONKSİYONLARI ---
def get_live_prices():
    """Canlı GEOD ve USD/TRY kurunu çeker"""
    try:
        # GEOD Fiyatı (CoinGecko)
        geod_res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=geodnet&vs_currencies=usd", timeout=5)
        geod_p = geod_res.json()['geodnet']['usd']
    except:
        geod_p = 0.1510 # Hata durumunda fallback
        
    try:
        # USD/TRY Kuru (Serbest Piyasa API)
        usd_res = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        usd_t = usd_res.json()['rates']['TRY']
    except:
        usd_t = 32.50 # Hata durumunda fallback
        
    return geod_p, usd_t

def encrypt_param(data, key):
    """Dökümanda belirtilen AES-CBC şifreleme"""
    k_fixed = str(key).rjust(16, '0')[:16].encode('utf-8')
    cipher = AES.new(k_fixed, AES.MODE_CBC, iv=k_fixed)
    padded_data = pad(str(data).encode('utf-8'), 16)
    return binascii.hexlify(cipher.encrypt(padded_data)).decode('utf-8')

# --- STREAMLIT ARAYÜZ ---
st.set_page_config(page_title="GEODNET Canlı Finans", layout="wide")
st.title("🛰️ GEODNET Canlı Kur ve Müşteri Hakediş Sistemi")

# Kur Verilerini Al
if 'geod_p' not in st.session_state:
    st.session_state.geod_p, st.session_state.usd_t = get_live_prices()

# --- YAN PANEL ---
with st.sidebar:
    st.header("📈 Canlı Piyasalar")
    st.metric("GEOD / USD", f"${st.session_state.geod_p:.4f}")
    st.metric("USD / TRY", f"₺{st.session_state.usd_t:.2f}")
    if st.button("Kurları Güncelle"):
        st.session_state.geod_p, st.session_state.usd_t = get_live_prices()
        st.rerun()
    
    st.divider()
    st.header("📂 Rapor Ayarları")
    uploaded_file = st.file_uploader("Müşteri Listesi (Excel/CSV)", type=['xlsx', 'csv'])
    start_date = st.date_input("Başlangıç", datetime.now() - timedelta(days=30))
    end_date = st.date_input("Bitiş", datetime.now())
    process_btn = st.button("HESAPLA VE RAPORLA", type="primary", use_container_width=True)

if process_btn and uploaded_file:
    # Kur bilgilerini sabitle
    current_geod = st.session_state.geod_p
    current_usd = st.session_state.usd_t
    geod_tl_price = current_geod * current_usd
    
    try:
        input_df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        input_df.columns = ['Musteri', 'SN'] + list(input_df.columns[2:])
        
        results = []
        pbar = st.progress(0)
        
        for index, row in input_df.iterrows():
            sn = str(row['SN']).strip()
            musteri = str(row['Musteri']).strip()
            
            ts = str(int(time.time() * 1000))
            params = {
                "clientId": CLIENT_ID,
                "timeStamp": encrypt_param(ts, TOKEN),
                "sn": encrypt_param(sn, TOKEN),
                "minTime": encrypt_param(start_date.strftime('%Y-%m-%d'), TOKEN),
                "maxTime": encrypt_param(end_date.strftime('%Y-%m-%d'), TOKEN)
            }
            
            try:
                # Döküman Sayfa 6: getRewardsTimeLine
                r = requests.get(f"{BASE_URL}/getRewardsTimeLine", params=params, verify=False, timeout=15)
                res = r.json()
                
                if res.get('statusCode') == 200:
                    data = res.get('data', [])
                    total_token = sum([pd.to_numeric(d['reward'], errors='coerce') or 0 for d in data])
                    
                    # Finansal Hesaplamalar
                    cust_share = total_token * 0.25
                    cust_tl = cust_share * geod_tl_price
                    fix_tl = max(0, 500 - cust_tl)
                    fix_token = fix_tl / geod_tl_price if geod_tl_price > 0 else 0
                    total_paid = cust_share + fix_token
                    
                    results.append({
                        "Müşteri": musteri, "SN": sn, "Top. GEOD": total_token,
                        "Müşteri %25": cust_share, "Müşteri TL": cust_tl,
                        "Tamamlama (TL)": fix_tl, "Tamamlama (GEOD)": fix_token,
                        "Müşteri Toplam GEOD": total_paid, "Net Bize Kalan GEOD": total_token - total_paid,
                        "GEOD Fiyatı": current_geod, "USD Kuru": current_usd
                    })
            except: pass
            pbar.progress((index + 1) / len(input_df))

        # --- TABLO VE RAPOR ---
        df = pd.DataFrame(results)
        st.header(f"📋 Finansal Rapor ({start_date} / {end_date})")
        st.dataframe(df)
        
        # Müşteri Bazlı Dip Toplam
        st.subheader("👥 Müşteri Ödeme Özeti")
        summary = df.groupby('Müşteri').agg({
            'SN': 'count', 'Müşteri Toplam GEOD': 'sum', 'Net Bize Kalan GEOD': 'sum', 
            'Tamamlama (TL)': 'sum', 'GEOD Fiyatı': 'first', 'USD Kuru': 'first'
        }).rename(columns={'SN': 'Cihaz Sayısı'})
        st.table(summary)

    except Exception as e:

        st.error(f"Hata: {e}")
