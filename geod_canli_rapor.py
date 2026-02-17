import streamlit as st
import requests
import time
import binascii
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from fpdf import FPDF
import warnings

# --- GENEL AYARLAR ---
warnings.filterwarnings('ignore')
st.set_page_config(page_title="MonsPro | Operasyonel Portal", layout="wide")

# --- 1. DİNAMİK SELAMLAMA ---
def get_greeting():
    hour = datetime.now().hour
    if 5 <= hour < 12: greet = "Günaydın"
    elif 12 <= hour < 18: greet = "Tünaydın"
    elif 18 <= hour < 22: greet = "İyi Akşamlar"
    else: greet = "İyi Geceler"
    return f"✨ {greet}, MonsPro Team Hoşgeldiniz."

# --- 2. HAVA DURUMU ---
def get_weather():
    cities = {
        "İstanbul": {"lat": 41.0082, "lon": 28.9784}, "Ankara": {"lat": 39.9334, "lon": 32.8597},
        "İzmir": {"lat": 38.4192, "lon": 27.1287}, "Erzurum": {"lat": 39.9000, "lon": 41.2700},
        "Antalya": {"lat": 36.8969, "lon": 30.7133}, "Sinop": {"lat": 42.0268, "lon": 35.1625},
        "Gaziantep": {"lat": 37.0662, "lon": 37.3833}
    }
    weather_results = []
    try:
        for city, coords in cities.items():
            url = f"https://api.open-meteo.com/v1/forecast?latitude={coords['lat']}&longitude={coords['lon']}&current_weather=true&daily=precipitation_sum,windspeed_10m_max&timezone=auto"
            res = requests.get(url, timeout=5).json()
            curr = res['current_weather']
            precip = res['daily']['precipitation_sum'][0]
            wind = res['daily']['windspeed_10m_max'][0]
            weather_results.append({"city": city, "temp": curr['temperature'], "precip": precip, "wind": wind, "risky": (precip > 10 or wind > 40)})
        return weather_results
    except: return []

# --- 3. YARDIMCI FONKSİYONLAR ---
def temizle(text):
    mapping = {"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ı": "i", "İ": "I", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"}
    for key, val in mapping.items(): text = str(text).replace(key, val)
    return text

def get_live_prices():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/coins/geodnet/market_chart?vs_currency=usd&days=30", timeout=10).json()
        df_p = pd.DataFrame(res['prices'], columns=['time', 'price'])
        df_p['time'] = pd.to_datetime(df_p['time'], unit='ms')
        geod_p, usd_t = df_p['price'].iloc[-1], requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()['rates']['TRY']
        return geod_p, usd_t, df_p
    except: return 0.1500, 33.00, pd.DataFrame()

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
    pdf.cell(95, 8, f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y')}", ln=True, align='R')
    pdf.cell(190, 8, f"Donem: {s_date}", ln=True)
    pdf.cell(190, 8, f"GEOD Fiyat: ${g_price:.4f} | USD Kuru: {u_try:.2f} TL", ln=True)
    pdf.ln(5)
    pdf.set_fill_color(200, 200, 200)
    pdf.set_font("helvetica", 'B', 8)
    pdf.cell(40, 10, "Miner No", 1, 0, 'C', True)
    pdf.cell(20, 10, "Pay (%)", 1, 0, 'C', True)
    pdf.cell(25, 10, "Kazanc (T)", 1, 0, 'C', True)
    pdf.cell(25, 10, "Fix (T)", 1, 0, 'C', True)
    pdf.cell(30, 10, "Top. Token", 1, 0, 'C', True)
    pdf.cell(50, 10, "Tutar (TL)", 1, 1, 'C', True)
    pdf.set_font("helvetica", '', 8)
    for _, row in data_df.iterrows():
        pdf.cell(40, 10, str(row['SN']), 1)
        pdf.cell(20, 10, f"%{row['Pay_Orani']*100:.0f}", 1, 0, 'C')
        pdf.cell(25, 10, f"{row['Pay_Token']:.2f}", 1, 0, 'C')
        pdf.cell(25, 10, f"{row['Eklenen_Token_Fix']:.2f}", 1, 0, 'C')
        pdf.cell(30, 10, f"{row['Odenecek_Toplam_Token']:.2f}", 1, 0, 'C')
        pdf.cell(50, 10, f"{row['Toplam_TL']:.2f} TL", 1, 1, 'C')
    pdf.ln(5)
    pdf.set_font("helvetica", 'B', 11)
    pdf.cell(190, 10, f"Genel Toplam: {data_df['Toplam_TL'].sum():.2f} TL", ln=True, align='R')
    return bytes(pdf.output())

# --- 4. SESSION STATE & UI ---
if 'arsiv' not in st.session_state: st.session_state.arsiv = {}
if 'last_results' not in st.session_state: st.session_state.last_results = None
if 'geod_p' not in st.session_state: st.session_state.geod_p, st.session_state.usd_t, st.session_state.price_df = get_live_prices()

st.markdown(f"<h3 style='text-align: center; color: #4A4A4A;'>{get_greeting()}</h3>", unsafe_allow_html=True)

# Weather Bar
weather_data = get_weather()
if weather_data:
    cols = st.columns(len(weather_data))
    for i, data in enumerate(weather_data):
        with cols[i]:
            color = "#FF4B4B" if data['risky'] else "#28A745"
            st.markdown(f'<div style="text-align: center; border-radius: 8px; padding: 4px; border: 1.5px solid {color}; background-color: rgba(0,0,0,0.05);"><b style="color: {color}; font-size: 0.85em;">{data["city"]}</b><br><span style="font-size: 1em; font-weight: bold;">{data["temp"]}°C</span><br>{"<span style=\'color: #FF4B4B; font-size: 0.6em; font-weight: bold;\'>⚠️ RISK</span>" if data["risky"] else "<span style=\'color: #28A745; font-size: 0.6em;\'>✅ UYGUN</span>"}</div>', unsafe_allow_html=True)

# --- 5. SIDEBAR ---
with st.sidebar:
    st.markdown("<h1 style='color: #FF4B4B;'>🛰️ MonsPro</h1>", unsafe_allow_html=True)
    menu = st.radio("Menü Seçimi", ["📊 Yeni Sorgu", "📚 Arşiv"])
    st.divider()
    
    if menu == "📊 Yeni Sorgu":
        input_type = st.radio("Yöntem", ["Excel Yükle", "Manuel SN"])
        if input_type == "Excel Yükle":
            uploaded_file = st.file_uploader("Excel Seç (İş Ortağı, İl, Konum, Miner Numarası, Kar Payı)", type=['xlsx'])
        else:
            m_manual = st.text_input("İş Ortağı Adı", "Özel Sorgu")
            sn_manual = st.text_input("Miner Numarası (SN)")
            kp_manual = st.number_input("Kar Payı Oranı (%)", min_value=1, max_value=100, value=25)
            
        start_date = st.date_input("Başlangıç", datetime.now() - timedelta(days=31))
        end_date = st.date_input("Bitiş", datetime.now())
        kayit_adi = st.text_input("Arşiv İsmi", value=datetime.now().strftime("%d.%m.%Y %H:%M"))
        
        if st.button("HESAPLA", type="primary", use_container_width=True):
            source_df = None
            if input_type == "Excel Yükle" and uploaded_file:
                df_raw = pd.read_excel(uploaded_file)
                # Yeni sütun yapısına göre eşleştirme
                source_df = pd.DataFrame({
                    'Musteri': df_raw['İş Ortağı'],
                    'SN': df_raw['Miner Numarası'],
                    'İl': df_raw['İl'],
                    'Kar_Payi': df_raw['Kar Payı'] # Örn: 0.25 veya 25
                })
            elif input_type == "Manuel SN" and sn_manual:
                source_df = pd.DataFrame([{'Musteri': m_manual, 'SN': sn_manual, 'Kar_Payi': kp_manual/100}])
            
            if source_df is not None:
                results = []
                geod_tl = st.session_state.geod_p * st.session_state.usd_t
                p_bar = st.progress(0)
                for index, row in source_df.iterrows():
                    m_name, sn_no = str(row['Musteri']).strip(), str(row['SN']).strip()
                    # Kar payı verisini normalize et (25 gelirse 0.25 yap)
                    kp_raw = float(row['Kar_Payi'])
                    kp_rate = kp_raw / 100 if kp_raw > 1 else kp_raw
                    
                    raw_data = get_all_rewards(sn_no, start_date, end_date)
                    total_token = sum([pd.to_numeric(d['reward'], errors='coerce') or 0 for d in raw_data])
                    
                    pay_token = total_token * kp_rate
                    tl_val = pay_token * geod_tl
                    fix_tl = max(0, 500 - tl_val)
                    fix_token = fix_tl / geod_tl if geod_tl > 0 else 0
                    
                    results.append({
                        "Musteri": m_name, "SN": sn_no, "Toplam_Uretim": total_token,
                        "Pay_Orani": kp_rate, "Pay_Token": pay_token, 
                        "Eklenen_Token_Fix": fix_token, "Toplam_TL": tl_val + fix_tl,
                        "Odenecek_Toplam_Token": pay_token + fix_token,
                        "Bize_Net_Kalan_Token": total_token - (pay_token + fix_token)
                    })
                    p_bar.progress((index + 1) / len(source_df))
                
                st.session_state.last_results = {"df": pd.DataFrame(results), "donem": f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}", "kur_geod": st.session_state.geod_p, "kur_usd": st.session_state.usd_t}
                if kayit_adi: st.session_state.arsiv[kayit_adi] = st.session_state.last_results

    else: # Arşiv
        if st.session_state.arsiv:
            selected_h = st.selectbox("Geçmiş Kayıtlar", list(st.session_state.arsiv.keys()))
            if st.button("Görüntüle", use_container_width=True): st.session_state.last_results = st.session_state.arsiv[selected_h]
            if st.button("Sil", type="secondary", use_container_width=True): 
                del st.session_state.arsiv[selected_h]
                st.rerun()

# --- 6. DASHBOARD ---
st.divider()
c1, c2, c3 = st.columns(3)
c1.metric("GEOD / USD", f"${st.session_state.geod_p:.4f}")
c2.metric("USD / TRY", f"{st.session_state.usd_t:.2f} ₺")
c3.metric("GEOD / TRY", f"{(st.session_state.geod_p * st.session_state.usd_t):.2f} ₺")

if not st.session_state.price_df.empty:
    st.divider()
    col_chart, col_info = st.columns([2, 1])
    with col_chart:
        fig = px.line(st.session_state.price_df, x='time', y='price', title="30 Günlük GEOD Trendi")
        fig.add_hline(y=0.12, line_dash="dash", line_color="red", annotation_text="Kritik Eşik (0.12$)")
        st.plotly_chart(fig, use_container_width=True)
    with col_info:
        st.subheader("🎯 Stratejik Analiz")
        if st.session_state.geod_p >= 0.12: st.success("**GÜVENLİ BÖLGE**\nFiyat 0.12$ üzerinde.")
        else: st.error("**RİSKLİ BÖLGE**\nFiyat 0.12$ altında!")

if st.session_state.last_results:
    st.divider()
    st.header("📋 Hakediş Detayları")
    res = st.session_state.last_results
    df = res["df"]
    sm1, sm2, sm3 = st.columns(3)
    sm1.metric("Toplam Üretim (T)", f"{df['Toplam_Uretim'].sum():.2f}")
    sm2.metric("Ödenen (T)", f"{df['Odenecek_Toplam_Token'].sum():.2f}")
    sm3.metric("Bize Kalan (T)", f"{df['Bize_Net_Kalan_Token'].sum():.2f}")
    st.dataframe(df, use_container_width=True)
    
    st.subheader("📥 Raporlar")
    for i, m_name in enumerate(df['Musteri'].unique()):
        m_data = df[df['Musteri'] == m_name]
        pdf_bytes = create_pdf(m_name, m_data, res["kur_geod"], res["kur_usd"], res["donem"])
        col_m, col_b = st.columns([4, 1])
        col_m.write(f"📄 {m_name}")
        col_b.download_button("İndir", data=pdf_bytes, file_name=f"{temizle(m_name)}_Rapor.pdf", key=f"dl_{i}")
