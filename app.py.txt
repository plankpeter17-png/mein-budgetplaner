import streamlit as st
import pandas as pd
from datetime import datetime, date
import os
import io

# Google API Bibliotheken importieren
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import gspread

# 1. APP-ERSTELLUNG & GRUNDEINSTELLUNG
st.set_page_config(page_title="Cloud Budgetplaner", page_icon="☁️", layout="wide")
st.title("☁️ Google-Driven Multiplayer Budgetplaner")

CREDENTIALS_FILE = 'credentials.json'
SPREADSHEET_NAME = "Budgetplaner_DB"
SCOPES = [
    'https://googleapis.com',
    'https://googleapis.com'
]

# 2. VERBINDUNG ZU GOOGLE AUFBAUEN
@st.cache_resource
def get_google_clients():
    if os.path.exists(CREDENTIALS_FILE):
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        drive_service = build('drive', 'v3', credentials=creds)
        gc = gspread.authorize(creds)
        return drive_service, gc
    return None, None

drive_service, gc = get_google_clients()

if not gc:
    st.error("Fehler: Die Datei 'credentials.json' wurde nicht im App-Ordner gefunden!")
    st.stop()

# Verbindung zu den Tabellenblättern herstellen
sh = gc.open(SPREADSHEET_NAME)
ws_budgets = sh.worksheet("Budgets")
ws_fixkosten = sh.worksheet("Fixkosten")
ws_variabel = sh.worksheet("Variabel")

# 3. HILFSFUNKTIONEN FÜR DATEN TRANSFER & AUTOMATIK
def load_budgets():
    data = ws_budgets.get_all_records()
    return {row["Monat"]: float(row["Betrag"]) for row in data} if data else {}

def save_budget(monat_str, betrag):
    budgets = load_budgets()
    budgets[monat_str] = betrag
    ws_budgets.clear()
    ws_budgets.append_row(["Monat", "Betrag"])
    for m, b in budgets.items():
        ws_budgets.append_row([m, b])

def load_fixkosten():
    data = ws_fixkosten.get_all_records()
    return pd.DataFrame(data) if data else pd.DataFrame(columns=["Name", "Betrag", "Intervall", "Startmonat"])

def add_fixkosten(name, betrag, intervall, startmonat):
    ws_fixkosten.append_row([name, betrag, intervall, startmonat])

def load_variabel():
    data = ws_variabel.get_all_records()
    if data:
        df = pd.DataFrame(data)
        df["Datum"] = pd.to_datetime(df["Datum"]).dt.date
        return df
    return pd.DataFrame(columns=["Datum", "Kategorie", "Betrag", "Beschreibung", "Nutzer", "Drive-Link"])

def add_variabel(datum_str, kategorie, betrag, beschreibung, nutzer, drive_link):
    ws_variabel.append_row([datum_str, kategorie, betrag, beschreibung, nutzer, drive_link])

def find_or_create_folder(name, parent_id=None):
    q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id: q += f" and '{parent_id}' in parents"
    res = drive_service.files().list(q=q, spaces='drive', fields='files(id)').execute().get('files', [])
    if res: return res['id']
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent_id: meta['parents'] = [parent_id]
    return drive_service.files().create(body=meta, fields='id').execute().get('id')

def upload_to_google_drive(file_bytes, filename, mime_type, jahr, monat):
    try:
        id_ausgaben = find_or_create_folder("Ausgaben")
        id_jahr = find_or_create_folder(str(jahr), id_ausgaben)
        id_monat = find_or_create_folder(str(monat).zfill(2), id_jahr)
        id_rechnungen = find_or_create_folder("Rechnungen", id_monat)
        
        meta = {'name': filename, 'parents': [id_rechnungen]}
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
        f = drive_service.files().create(body=meta, media_body=media, fields='webViewLink').execute()
        return f.get('webViewLink')
    except Exception as e:
        st.error(f"Fehler beim Drive-Upload: {e}")
        return ""

def ist_fixkosten_aktiv(start_str, intervall, ziel_str):
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m").date()
        ziel_dt = datetime.strptime(ziel_str, "%Y-%m").date()
        if ziel_dt < start_dt: return False
        diff = (ziel_dt.year - start_dt.year) * 12 + (ziel_dt.month - start_dt.month)
        mapping = {"monatlich": 1, "2-monatlich (z.B. Strom)": 2, "3-monatlich (Quartal)": 3, "halbjährlich": 6, "jährlich": 12}
        return (diff % mapping.get(intervall, 1)) == 0
    except: return False

# 4. DIE SEITENLEISTE (SIDEBAR) FÜR STEUERUNG
with st.sidebar:
    st.header("Nutzerprofil")
    aktueller_nutzer = st.text_input("Dein Name", value="Nutzer1")
    st.divider()
    st.header("Zeitraum")
    aktueller_monat = st.date_input("Monat wählen", date.today())
    ziel_str = aktueller_monat.strftime("%Y-%m")
    
    st.divider()
    st.header("Budget")
    all_budgets = load_budgets()
    bestehendes_budget = all_budgets.get(ziel_str, 1000.0)
    neues_budget = st.number_input("Budget für diesen Monat (€)", min_value=0.0, value=float(bestehendes_budget), step=50.0)
    if neues_budget != bestehendes_budget:
        save_budget(ziel_str, neues_budget)
        st.success("In Google Sheets aktualisiert!")
        st.rerun()

# 5. LIVE-BERECHNUNGEN AUS DEN GOOGLE-DATEN
df_fixkosten_all = load_fixkosten()
df_variabel_all = load_variabel()
aktuelles_budget = load_budgets().get(ziel_str, 1000.0)

aktive_fk = [fk for _, fk in df_fixkosten_all.iterrows() if ist_fixkosten_aktiv(fk["Startmonat"], fk["Intervall"], ziel_str)]
df_aktive_fk = pd.DataFrame(aktive_fk) if aktive_fk else pd.DataFrame(columns=["Name", "Betrag", "Intervall"])

if not df_variabel_all.empty:
    df_var_monat = df_variabel_all[df_variabel_all["Datum"].apply(lambda x: x.strftime("%Y-%m") == ziel_str)]
else:
    df_var_monat = pd.DataFrame(columns=["Datum", "Kategorie", "Betrag", "Beschreibung", "Nutzer", "Drive-Link"])

summe_fix = df_aktive_fk["Betrag"].sum() if not df_aktive_fk.empty else 0.0
summe_var = df_var_monat["Betrag"].sum() if not df_var_monat.empty else 0.0
gesamtausgaben = summe_fix + summe_var
verbleibend = aktuelles_budget - gesamtausgaben

# 6. DIE OBERFLÄCHE (REITER/TABS)
tab1, tab2, tab3 = st.tabs(["Live-Dashboard", "Fixkosten & Abos", "Variable Ausgaben & Upload"])

with tab1:
    st.header(f"Status für {aktueller_monat.strftime('%B %Y')}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Budget", f"{aktuelles_budget:.2f} €")
    c2.metric("Fixkosten", f"{summe_fix:.2f} €")
    c3.metric("Variable Ausgaben", f"{summe_var:.2f} €")
    c4.metric("Verbleibend", f"{verbleibend:.2f} €", delta=f"{verbleibend:.2f} €")
    st.subheader("Aktuelle Buchungsliste")
    st.dataframe(df_var_monat, use_container_width=True)

with tab2:
    st.header("Neue wiederkehrende Ausgabe")
    with st.form("fix_form"):
        fk_name = st.text_input("Name der Belastung")
        fk_betrag = st.number_input("Betrag (€)", min_value=0.01)
        fk_intervall = st.selectbox("Turnus", ["monatlich", "2-monatlich (z.B. Strom)", "3-monatlich (Quartal)", "halbjährlich", "jährlich"])
        fk_start = st.date_input("Startet ab", date.today())
        if st.form_submit_button("In Google Tabelle eintragen"):
            add_fixkosten(fk_name, fk_betrag, fk_intervall, fk_start.strftime("%Y-%m"))
            st.success("Gespeichert!")
            st.rerun()
    st.dataframe(df_fixkosten_all, use_container_width=True)

with tab3:
    st.header("Einzelbeleg hochladen")
    uploaded_file = st.file_uploader("Datei wählen (PDF/Bild)", type=["pdf", "png", "jpg", "jpeg"])
    with st.form("var_form"):
        v_datum = st.date_input("Datum", date.today())
        v_kat = st.selectbox("Kategorie", ["Lebensmittel", "Freizeit", "Auto", "Haushalt"])
        v_betrag = st.number_input("Betrag (€)", min_value=0.00, step=0.01)
        v_desc = st.text_input("Notiz", value=uploaded_file.name if uploaded_file else "")
        if st.form_submit_button("Buchen & in Drive archivieren"):
            drive_link = ""
            if uploaded_file:
                drive_link = upload_to_google_drive(
                    uploaded_file.read(), uploaded_file.name, uploaded_file.type, v_datum.year, v_datum.month
                )
            add_variabel(v_datum.strftime("%Y-%m-%d"), v_kat, v_betrag, v_desc, aktueller_nutzer, drive_link)
            st.success("Hochgeladen und gebucht!")
            st.rerun()
