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
import json

warnings.filterwarnings('ignore')

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="GEODNET Finansal Arşiv", layout="wide")

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

def create_pdf(musteri_adi, data_df, g_price, u_try, s_date, e_date):
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

# --- SESSION STATE (ARŞİV İÇİN) ---
if 'arsiv' not in st.session_state:
    st.session_state.arsiv = {}

# --- SIDEBAR MENÜ ---
menu = st.sidebar.selectbox("📂 Menü Seçiniz", ["📊 Yeni Hesaplama", "📚 Geçmiş Kayıtlar"])

if 'geod_p' not in st.session_state:
    st.session_state.geod_p, st.session_state.usd_t = get_live_prices()

# --- YENİ HESAPLAMA SAYFASI ---
if menu == "📊 Yeni Hesaplama":
    st.title("🛰️ GEODNET Hakedis Paneli")
    
    with st.sidebar:
        st.header("⚙️ Ayarlar")
        if st.button("Kurlari Guncelle"):
            st.session_state.geod_p, st.session_state.usd_t = get_live_prices()
            st.rerun()
        st.divider()
        uploaded_file = st.file_uploader("Excel/CSV Yukle", type=['xlsx', 'csv'])
        start_date = st.date_input("Baslangic", datetime.now() - timedelta(days=31))
        end_date = st.date_input("Bitis", datetime.now())
        kayit_adi = st.text_input("Bu Kayit Icin Isim (Örn: Ekim 2025)", "")
        process_btn = st.button("HESAPLA VE KAYDET", type="primary", use_container_width=True)

    # Kur Gösterimi
    c1, c2 = st.columns(2)
    with c1: st.metric(label="GEOD / USD", value=f"${st.session_state.geod_p:.4f}")
    with c2: st.metric(label="USD / TRY", value=f"{st.session_state.usd_t:.2f} TL")

    if process_btn and uploaded_file and kayit_adi:
        try:
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
            
            # Arşive Kaydet
            st.session_state.arsiv[kayit_adi] = {
                "df": final_df,
                "donem": f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}",
                "kur_geod": st.session_state.geod_p,
                "kur_usd": st.session_state.usd_t
            }
            st.success(f"'{kayit_adi}' basariyla kaydedildi! Gecmis Kayitlar menusunden ulasabilirsiniz.")
            st.rerun()

        except Exception as e:
            st.error(f"Hata: {e}")

# --- GEÇMİŞ KAYITLAR SAYFASI ---
elif menu == "📚 Geçmiş Kayıtlar":
    st.title("📚 Finansal Arsiv")
    
    if not st.session_state.arsiv:
        st.info("Henuz kaydedilmis bir donem bulunmuyor.")
    else:
        selected_record = st.selectbox("Incelemek Istediginiz Donemi Secin", list(st.session_state.arsiv.keys()))
        data = st.session_state.arsiv[selected_record]
        df = data["df"]

        # Özet Metrikler
        m1, m2, m3 = st.columns(3)
        m1.metric("Toplam Kazanc (Token)", f"{df['Top_GEOD'].sum():.2f}")
        m2.metric("Odenen Toplam (Token)", f"{df['Musteri_Odenecek_Toplam_Token'].sum():.2f}")
        m3.metric("Bize Kalan Net (Token)", f"{df['Bize_Net_Kalan_Token'].sum():.2f}")

        st.divider()
        st.subheader(f"📋 {selected_record} Detayli Tablo ({data['donem']})")
        st.dataframe(df, use_container_width=True)

        st.divider()
        st.subheader("📄 Musteri Raporlari Indirme Listesi")
        
        # Raporları Düzenli Bir Tabloda Gösterme
        report_data = []
        for m_name in df['Musteri'].unique():
            report_data.append({"Musteri Adi": m_name})
        
        report_table = pd.DataFrame(report_data)
        
        # Raporları butonlarla değil, daha düzenli bir liste ile sunma
        for i, row in report_table.iterrows():
            m_name = row['Musteri Adi']
            m_data = df[df['Musteri'] == m_name]
            
            pdf_bytes = create_pdf(
                m_name, m_data, data["kur_geod"], data["kur_usd"], data["donem"], data["donem"]
            )
            
            col_a, col_b = st.columns([4, 1])
            with col_a:
                st.write(f"📄 {m_name} - {len(m_data)} Cihaz Raporu")
            with col_b:
                st.download_button(
                    label="Indir", 
                    data=pdf_bytes, 
                    file_name=f"{temizle(m_name)}_Rapor.pdf", 
                    mime="application/pdf", 
                    key=f"dl_{selected_record}_{i}"
                )
            st.divider()
