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

warnings.filterwarnings('ignore')

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="MonsPro | Operasyonel Portal", layout="wide")

# --- HAVA DURUMU FONKSİYONU ---
def get_weather():
    cities = {
        "İstanbul": {"lat": 41.0082, "lon": 28.9784},
        "Ankara": {"lat": 39.9334, "lon": 32.8597},
        "İzmir": {"lat": 38.4192, "lon": 27.1287},
        "Erzurum": {"lat": 39.9000, "lon": 41.2700},
        "Antalya": {"lat": 36.8969, "lon": 30.7133},
        "Sinop": {"lat": 42.0268, "lon": 35.1625},
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
            
            # Risk Analizi: Yağış > 10mm veya Rüzgar > 40km/s ise kritik
            is_risky = precip > 10 or wind > 40
            weather_results.append({
                "city": city,
                "temp": curr['temperature'],
                "precip": precip,
                "wind": wind,
                "risky": is_risky
            })
        return weather_results
    except:
        return []

# --- DİĞER FONKSİYONLAR (ÖNCEKİLERLE AYNI) ---
def temizle(text):
    mapping = {"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ı": "i", "İ": "I", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"}
    for key, val in mapping.items(): text = str(text).replace(key, val)
    return text

def get_live_prices():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/coins/geodnet/market_chart?vs_currency=usd&days=30", timeout=10).json()
        df_p = pd.DataFrame(res['prices'], columns=['time', 'price'])
        df_p['time'] = pd.to_datetime(df_p['time'], unit='ms')
        geod_p = df_p['price'].iloc[-1]
        usd_t = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()['rates']['TRY']
        return geod_p, usd_t, df_p
    except: return 0.1500, 33.00, pd.DataFrame()

# --- SIDEBAR NAVİGASYON ---
st.sidebar.markdown(
    """<div style="cursor: pointer;" onclick="window.location.reload();">
    <h1 style='color: #FF4B4B; font-family: sans-serif; margin-bottom: 0;'>🛰️ MonsPro</h1>
    <p style='font-size: 0.8em; color: gray;'>Operasyonel Kontrol Merkezi</p></div>""", unsafe_allow_html=True
)

# --- ÜST PANEL: HAVA DURUMU (SAĞ ÜST) ---
weather_data = get_weather()
if weather_data:
    cols = st.columns(len(weather_data))
    for i, data in enumerate(weather_data):
        with cols[i]:
            color = "#FF4B4B" if data['risky'] else "#28A745"
            st.markdown(f"""
                <div style="text-align: center; border-radius: 10px; padding: 5px; border: 1px solid {color};">
                    <b style="color: {color}; font-size: 0.9em;">{data['city']}</b><br>
                    <span style="font-size: 1.1em; font-weight: bold;">{data['temp']}°C</span><br>
                    <span style="font-size: 0.7em; color: gray;">Yağış: {data['precip']}mm</span><br>
                    {f'<span style="color: #FF4B4B; font-size: 0.6em; font-weight: bold;">⚠️ RİSKLİ HAVA</span>' if data['risky'] else '<span style="color: #28A745; font-size: 0.6em;">✅ UYGUN</span>'}
                </div>
            """, unsafe_allow_html=True)

# --- ANA EKRAN: PİYASA ANALİZİ ---
if 'geod_p' not in st.session_state:
    st.session_state.geod_p, st.session_state.usd_t, st.session_state.price_df = get_live_prices()

st.divider()
m1, m2, m3 = st.columns(3)
m1.metric("GEOD / USD", f"${st.session_state.geod_p:.4f}")
m2.metric("USD / TRY", f"{st.session_state.usd_t:.2f} ₺")
m3.metric("GEOD / TRY", f"{(st.session_state.geod_p * st.session_state.usd_t):.2f} ₺")

# Grafik ve Stratejik Öngörü (Önceki kodla aynı şekilde devam eder...)
# ... [Burada önceki kodun geri kalanı (Grafik, Sidebar sorgu, Arşiv) yer alacak]
