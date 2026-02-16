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

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="GEODNET Finansal Portal", layout="wide")

# --- GEODNET API VE SECRETS ---
try:
    CLIENT_ID = st.secrets["CLIENT_ID"]
    TOKEN = st.secrets["TOKEN"]
except:
    st.error("Secrets bulunamadi! Lutfen Settings > Secrets kismini kontrol edin.")
    st.stop()

BASE_URL = "https://consoleresapi.geodnet.com"

# --- YARDIMCI FONKSIYONLAR ---
def temizle(text):
    mapping = {"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ı": "i", "İ": "I", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"}
    for key, val in mapping.items():
        text = str(text).replace(key, val)
    return text

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
        params = {"clientId": CLIENT_ID, "timeStamp": encrypt_param(ts, TOKEN), "sn": encrypt_param(sn, TOKEN), "minTime": encrypt_param(curr_start.strftime('%Y-%m-%d'), TOKEN), "maxTime": encrypt_param(curr_end.strftime('%Y-%m-%d'), TOKEN)}
        try:
            r = requests.get(f"{BASE_URL}/getRewardsTimeLine", params=params, verify=False, timeout=15)
            res = r.json()
            if res.get('statusCode') == 200:
                all_data.extend(res.get('data', []))
        except: pass
        curr_start = curr_end + timedelta(days=1)
    return all_data

def create_pdf(musteri_adi, data_df, g_price, u_try, s_date):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 14)
    pdf.cell(190, 10, "GEODNET HAKEDIS RAPORU", ln=True, align='C')
    pdf.set_font("helvetica", '', 10)
    pdf.ln(5)
    pdf.cell(95, 8, f"Musteri: {temizle(musteri_adi)}")
    pdf.cell(95, 8, f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y')}", ln=True, align='R')
    pdf.cell(190, 8, f"Donem: {s_date}", ln=True)
    pdf.cell(190, 8, f"GEOD Fiyat: ${g_price:.4f} | USD Kuru: {u_try:.2f} TL", ln=True)
    pdf.ln(5)
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font("helvetica", 'B', 8)
    pdf.cell(45, 10, "Cihaz SN", 1, 0, 'C', True)
    pdf.cell(25, 10, "Kazanc(25%)", 1, 0, 'C', True)
    pdf.cell(30, 10, "Eklenen(Fix)", 1, 0, 'C', True)
    pdf.cell(30, 10, "Top. Token", 1, 0, 'C', True)
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

# --- ARŞİV SİSTEMİ ---
if 'arsiv' not in st.session_state:
    st.session_state.arsiv = {}
if 'last_results' not in st.session_state:
    st.session_state.last_results = None

# --- SIDEBAR MENÜ ---
menu = st.sidebar.selectbox("📂 İşlem Seçiniz", ["📊 Yeni Hesaplama", "📚 Geçmiş Kayıtlar"])

if 'geod_p' not in st.session_state:
    st.session_state.geod_p, st.session_state.usd_t = get_live_prices()

# --- YENİ HESAPLAMA ---
if menu == "📊 Yeni Hesaplama":
    st.title("🛰️ GEODNET Hakediş Paneli")
    
    with st.sidebar:
        st.header("⚙️ Kontrol Paneli")
        if st.button("Kurları Güncelle"):
            st.session_state.geod_p, st.session_state.usd_t = get_live_prices()
            st.rerun()
        st.divider()
        uploaded_file = st.file_uploader("Cihaz Listesini Yükleyin", type=['xlsx', 'csv'])
        start_date = st.date_input("Başlangıç", datetime.now() - timedelta(days=31))
        end_date = st.date_input("Bitiş", datetime.now())
        kayit_adi = st.text_input("Kayıt İsmi (Örn: Kasım 2025)", "")
        process_btn = st.button("HESAPLA", type="primary", use_container_width=True)

    c1, c2 = st.columns(2)
    c1.metric("GEOD / USD", f"${st.session_state.geod_p:.4f}")
    c2.metric("USD / TRY", f"{st.session_state.usd_t:.2f} TL")

    if process_btn and uploaded_file:
        input_df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('.xlsx') else pd.read_csv(uploaded_file)
        input_df.columns = ['Musteri', 'SN'] + list(input_df.columns[2:])
        results = []
        geod_tl_rate = st.session_state.geod_p * st.session_state.usd_t
        p_bar = st.progress(0)
        
        for index, row in input_df.iterrows():
            m_name, sn_no = str(row['Musteri']).strip(), str(row['SN']).strip()
            raw_data = get_all_rewards(sn_no, start_date, end_date)
            total_token = sum([pd.to_numeric(d['reward'], errors='coerce') or 0 for d in raw_data])
            token_25 = total_token * 0.25
            tl_25 = token_25 * geod_tl_rate
            fix_tl = max(0, 500 - tl_25)
            fix_token = fix_tl / geod_tl_rate if geod_tl_rate > 0 else 0
            
            results.append({
                "Musteri": m_name, "SN": sn_no, "Top_GEOD": total_token,
                "Musteri_Pay_Token": token_25, "Eklenen_Token_Fix": fix_token,
                "Musteri_Toplam_TL": tl_25 + fix_tl,
                "Musteri_Odenecek_Toplam_Token": token_25 + fix_token,
                "Bize_Net_Kalan_Token": total_token - (token_25 + fix_token)
            })
            p_bar.progress((index + 1) / len(input_df))

        final_df = pd.DataFrame(results)
        
        # Sonuçları state'e kaydet (ekranda kalması için)
        st.session_state.last_results = {
            "df": final_df,
            "donem": f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}",
            "kur_geod": st.session_state.geod_p,
            "kur_usd": st.session_state.usd_t
        }
        
        # Eğer bir kayıt ismi girildiyse arşive de ekle
        if kayit_adi:
            st.session_state.arsiv[kayit_adi] = st.session_state.last_results
            st.sidebar.success(f"'{kayit_adi}' arşive kaydedildi!")

    # Hesaplama bittiğinde veya daha önceden hesaplanmış veri varsa ekranda göster
    if st.session_state.last_results is not None:
        data = st.session_state.last_results
        df = data["df"]
        
        st.divider()
        st.header("📊 Güncel Hesaplama Özeti")
        m1, m2, m3 = st.columns(3)
        m1.metric("Toplam Kazanç (Token)", f"{df['Top_GEOD'].sum():.2f}")
        m2.metric("Ödenen Toplam (Token)", f"{df['Musteri_Odenecek_Toplam_Token'].sum():.2f}")
        m3.metric("Bize Kalan Net (Token)", f"{df['Bize_Net_Kalan_Token'].sum():.2f}")
        
        st.subheader("📋 Detaylı Analiz")
        st.dataframe(df, use_container_width=True)
        
        st.subheader("📄 Müşteri Raporları")
        for i, m_name in enumerate(df['Musteri'].unique()):
            m_data = df[df['Musteri'] == m_name]
            pdf_bytes = create_pdf(m_name, m_data, data["kur_geod"], data["kur_usd"], data["donem"])
            col_a, col_b = st.columns([5, 1])
            col_a.write(f"📄 **{m_name}** ({len(m_data)} Cihaz)")
            col_b.download_button("İndir", data=pdf_bytes, file_name=f"{temizle(m_name)}_Rapor.pdf", key=f"current_{i}")

# --- GEÇMİŞ KAYITLAR ---
elif menu == "📚 Geçmiş Kayıtlar":
    st.title("📚 Finansal Arşiv")
    if not st.session_state.arsiv:
        st.info("Henüz bir kayıt bulunmamaktadır. Lütfen 'Yeni Hesaplama' sayfasından bir kayıt ismiyle hesaplama yapın.")
    else:
        selected = st.selectbox("Görüntülenecek Dönem Seçin", list(st.session_state.arsiv.keys()))
        data = st.session_state.arsiv[selected]
        df = data["df"]

        m1, m2, m3 = st.columns(3)
        m1.metric("Toplam Kazanç", f"{df['Top_GEOD'].sum():.2f}")
        m2.metric("Ödenen Toplam", f"{df['Musteri_Odenecek_Toplam_Token'].sum():.2f}")
        m3.metric("Bize Kalan Net", f"{df['Bize_Net_Kalan_Token'].sum():.2f}")

        st.divider()
        st.subheader("📋 Müşteri Raporları Listesi")
        for i, m_name in enumerate(df['Musteri'].unique()):
            m_data = df[df['Musteri'] == m_name]
            pdf_bytes = create_pdf(m_name, m_data, data["kur_geod"], data["kur_usd"], data["donem"])
            col_a, col_b = st.columns([5, 1])
            col_a.write(f"📄 **{m_name}** ({len(m_data)} Cihaz)")
            col_b.download_button("İndir", data=pdf_bytes, file_name=f"{temizle(m_name)}_Rapor.pdf", key=f"hist_{i}")
