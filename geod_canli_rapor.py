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
st.set_page_config(page_title="MonsPro | GEODNET Portal", layout="wide")

# --- SOL ÜST MARKA (NAVİGASYON) ---
st.sidebar.markdown(
    """
    <div style="cursor: pointer;" onclick="window.location.reload();">
        <h1 style='color: #FF4B4B; font-family: sans-serif; margin-bottom: 0;'>🛰️ MonsPro</h1>
        <p style='font-size: 0.8em; color: gray;'>Finansal Analiz Merkezi</p>
    </div>
    """, unsafe_allow_html=True
)

# --- GEODNET API VE SECRETS ---
try:
    CLIENT_ID = st.secrets["CLIENT_ID"]
    TOKEN = st.secrets["TOKEN"]
except:
    st.error("Secrets bulunamadı! Lütfen Settings > Secrets kısmını kontrol edin.")
    st.stop()

BASE_URL = "https://consoleresapi.geodnet.com"

# --- YARDIMCI FONKSİYONLAR ---
def temizle(text):
    mapping = {"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ı": "i", "İ": "I", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"}
    for key, val in mapping.items():
        text = str(text).replace(key, val)
    return text

def get_live_prices():
    try:
        res = requests.get("https://api.coingecko.com/api/v3/coins/geodnet/market_chart?vs_currency=usd&days=30", timeout=10).json()
        df_p = pd.DataFrame(res['prices'], columns=['time', 'price'])
        df_p['time'] = pd.to_datetime(df_p['time'], unit='ms')
        geod_p = df_p['price'].iloc[-1]
        usd_t = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()['rates']['TRY']
        return geod_p, usd_t, df_p
    except:
        return 0.1500, 33.00, pd.DataFrame()

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
        params = {"clientId": CLIENT_ID, "timeStamp": encrypt_param(ts, TOKEN), "sn": encrypt_param(sn, TOKEN), "minTime": encrypt_param(curr_start.strftime('%Y-%m-%d'), TOKEN), "maxTime": encrypt_param(curr_end.strftime('%Y-%m-%d'), TOKEN)}
        try:
            r = requests.get(f"{BASE_URL}/getRewardsTimeLine", params=params, verify=False, timeout=15)
            res = r.json()
            if res.get('statusCode') == 200:
                all_data.extend(res.get('data', []))
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
    pdf.cell(95, 8, f"Musteri: {temizle(m_name)}")
    pdf.cell(95, 8, f"Donem: {s_date}", ln=True, align='R')
    pdf.cell(190, 8, f"GEOD Fiyat: ${g_price:.4f} | USD Kuru: {u_try:.2f} TL", ln=True)
    pdf.ln(5)
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font("helvetica", 'B', 8)
    pdf.cell(45, 10, "Cihaz SN", 1, 0, 'C', True)
    pdf.cell(25, 10, "Pay (25%)", 1, 0, 'C', True)
    pdf.cell(30, 10, "Eklenen (Fix)", 1, 0, 'C', True)
    pdf.cell(30, 10, "Toplam Token", 1, 0, 'C', True)
    pdf.cell(60, 10, "Toplam Tutar (TL)", 1, 1, 'C', True)
    pdf.set_font("helvetica", '', 8)
    total_val = 0
    for _, row in data_df.iterrows():
        pdf.cell(45, 10, str(row['SN']), 1)
        pdf.cell(25, 10, f"{row['Musteri_Pay_Token']:.2f}", 1, 0, 'C')
        pdf.cell(30, 10, f"{row['Eklenen_Token_Fix']:.2f}", 1, 0, 'C')
        pdf.cell(30, 10, f"{row['Musteri_Odenecek_Toplam_Token']:.2f}", 1, 0, 'C')
        pdf.cell(60, 10, f"{row['Musteri_Toplam_TL']:.2f} TL", 1, 1, 'C')
        total_val += row['Musteri_Toplam_TL']
    pdf.ln(5)
    pdf.set_font("helvetica", 'B', 11)
    pdf.cell(190, 10, f"Genel Toplam: {total_val:.2f} TL", ln=True, align='R')
    return bytes(pdf.output())

# --- SESSION STATE ---
if 'arsiv' not in st.session_state: st.session_state.arsiv = {}
if 'last_results' not in st.session_state: st.session_state.last_results = None
if 'geod_p' not in st.session_state:
    st.session_state.geod_p, st.session_state.usd_t, st.session_state.price_df = get_live_prices()

# --- SIDEBAR (SORGULAMA PANELİ) ---
with st.sidebar:
    st.divider()
    menu = st.radio("İşlem Modu", ["📊 Yeni Sorgu", "📚 Arşiv"])
    
    if menu == "📊 Yeni Sorgu":
        st.subheader("Veri Girişi")
        input_type = st.radio("Yöntem", ["Excel/CSV", "Manuel SN"])
        
        if input_type == "Excel/CSV":
            uploaded_file = st.file_uploader("Dosya Seç", type=['xlsx', 'csv'])
        else:
            sn_manual = st.text_input("SN Yazın")
            m_manual = st.text_input("Müşteri Adı", "Tekil Sorgu")
            
        start_date = st.date_input("Başlangıç", datetime.now() - timedelta(days=31))
        end_date = st.date_input("Bitiş", datetime.now())
        kayit_adi = st.text_input("Arşiv İsmi", "")
        
        if st.button("HESAPLA", type="primary", use_container_width=True):
            source_df = None
            if input_type == "Excel/CSV" and uploaded_file:
                source_df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('.xlsx') else pd.read_csv(uploaded_file)
                source_df.columns = ['Musteri', 'SN'] + list(source_df.columns[2:])
            elif input_type == "Manuel SN" and sn_manual:
                source_df = pd.DataFrame([{'Musteri': m_manual, 'SN': sn_manual}])
            
            if source_df is not None:
                results = []
                geod_tl = st.session_state.geod_p * st.session_state.usd_t
                p_bar = st.progress(0)
                for index, row in source_df.iterrows():
                    m_name, sn_no = str(row['Musteri']).strip(), str(row['SN']).strip()
                    raw_data = get_all_rewards(sn_no, start_date, end_date)
                    total_token = sum([pd.to_numeric(d['reward'], errors='coerce') or 0 for d in raw_data])
                    token_25 = total_token * 0.25
                    tl_25 = token_25 * geod_tl
                    fix_tl = max(0, 500 - tl_25)
                    fix_token = fix_tl / geod_tl if geod_tl > 0 else 0
                    results.append({"Musteri": m_name, "SN": sn_no, "Top_GEOD": total_token, "Musteri_Pay_Token": token_25, "Eklenen_Token_Fix": fix_token, "Musteri_Toplam_TL": tl_25 + fix_tl, "Musteri_Odenecek_Toplam_Token": token_25 + fix_token, "Bize_Net_Kalan_Token": total_token - (token_25 + fix_token)})
                    p_bar.progress((index + 1) / len(source_df))
                
                st.session_state.last_results = {"df": pd.DataFrame(results), "donem": f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}", "kur_geod": st.session_state.geod_p, "kur_usd": st.session_state.usd_t}
                if kayit_adi: st.session_state.arsiv[kayit_adi] = st.session_state.last_results

    elif menu == "📚 Arşiv":
        if st.session_state.arsiv:
            selected_h = st.selectbox("Geçmiş Kayıtlar", list(st.session_state.arsiv.keys()))
            if st.button("Kaydı Sil", type="secondary"):
                del st.session_state.arsiv[selected_h]
                st.rerun()
            if st.button("Görüntüle"):
                st.session_state.last_results = st.session_state.arsiv[selected_h]
        else:
            st.info("Arşiv boş.")

# --- ANA EKRAN (DASHBOARD) ---
st.header("📊 MonsPro Canlı Piyasa Analizi")

# Canlı Metrikler
m1, m2, m3 = st.columns(3)
m1.metric("GEOD / USD", f"${st.session_state.geod_p:.4f}")
m2.metric("USD / TRY", f"{st.session_state.usd_t:.2f} ₺")
m3.metric("GEOD / TRY", f"{(st.session_state.geod_p * st.session_state.usd_t):.2f} ₺")

# Grafik ve Stratejik Öngörü (Açılışta Her Zaman Görünür)
if not st.session_state.price_df.empty:
    st.divider()
    col_chart, col_strat = st.columns([2, 1])
    
    with col_chart:
        fig = px.line(st.session_state.price_df, x='time', y='price', title="30 Günlük GEOD Trendi")
        fig.add_hline(y=0.12, line_dash="dash", line_color="red", annotation_text="Kritik Eşik (0.12$)")
        st.plotly_chart(fig, use_container_width=True)
        

    with col_strat:
        st.subheader("🎯 Stratejik Analiz")
        if st.session_state.geod_p >= 0.12:
            st.success("**GÜVENLİ BÖLGE**\nFiyat 0.12$ üzerinde. Operasyonel karlılık korunuyor.")
        else:
            st.error("**KRİTİK BÖLGE**\nFiyat 0.12$ altında! Tamamlama maliyetleri yüksek seviyede.")
        st.info("Optimum ödeme için fiyatın pivot seviyelerini takip edin.")

# Dinamik Hesaplama Ekranı (Sadece Hesaplama Varsa Görünür)
if st.session_state.last_results is not None:
    st.divider()
    st.header("📋 Hesaplama ve Hakediş Detayları")
    df = st.session_state.last_results["df"]
    
    # Yönetim Özeti
    sm1, sm2, sm3 = st.columns(3)
    sm1.metric("Toplam Üretim", f"{df['Top_GEOD'].sum():.2f}")
    sm2.metric("Ödenen Toplam", f"{df['Musteri_Odenecek_Toplam_Token'].sum():.2f}")
    sm3.metric("Bize Kalan Net", f"{df['Bize_Net_Kalan_Token'].sum():.2f}")
    
    st.dataframe(df, use_container_width=True)
    
    # Raporlar
    st.subheader("📄 Hakediş Raporları")
    for i, m_name in enumerate(df['Musteri'].unique()):
        m_data = df[df['Musteri'] == m_name]
        pdf_bytes = create_pdf(m_name, m_data, st.session_state.last_results["kur_geod"], st.session_state.last_results["kur_usd"], st.session_state.last_results["donem"])
        col_a, col_b = st.columns([5, 1])
        col_a.write(f"Müşteri: **{m_name}**")
        col_b.download_button("PDF İndir", data=pdf_bytes, file_name=f"{temizle(m_name)}_Rapor.pdf", key=f"f_{i}")
