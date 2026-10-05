import streamlit as st
import pandas as pd
from datetime import datetime, date
import os
import io
import json
import base64

# Google API Bibliotheken importieren
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import gspread
import plotly.express as px

# 1. APP-ERSTELLUNG & GRUNDEINSTELLUNG
st.set_page_config(page_title="Cloud Budgetplaner", page_icon="☁️", layout="wide")
st.title("☁️ Google-Driven Multiplayer Budgetplaner")

CREDENTIALS_FILE = 'credentials.json'
SPREADSHEET_NAME = "Budgetplaner_DB"

# VOLLSTÄNDIGE GOOGLE SCOPES
SCOPES = [
    'https://googleapis.com',
    'https://googleapis.com'
]

# 2. VERBINDUNG ZU GOOGLE AUFBAUEN (BASE64 VERFAHREN)
@st.cache_resource
def get_google_clients():
    try:
        if "gcp_service_account" in st.secrets:
            encoded_key = st.secrets["gcp_service_account"]["encoded_key"]
            decoded_bytes = base64.b64decode(encoded_key)
            json_text = decoded_bytes.decode("utf-8")
            creds_dict = json.loads(json_text)
            creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        elif os.path.exists(CREDENTIALS_FILE):
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        else:
            return None, None
            
        drive_service = build('drive', 'v3', credentials=creds)
        gc = gspread.authorize(creds)
        return drive_service, gc
    except Exception as e:
        st.error(f"Fehler bei der Google-Verbindung: {e}")
        return None, None

# Initialisierung der Google-Clients
drive_service, gc = get_google_clients()

if not gc:
    st.error("Fehler: Verbindung zu Google Sheets fehlgeschlagen. Überprüfe die Secrets!")
    st.stop()

# Verbindung zu den Tabellenblättern herstellen
sh = gc.open(SPREADSHEET_NAME)
ws_budgets = sh.worksheet("Budgets")
ws_fixkosten = sh.worksheet("Fixkosten")
ws_variabel = sh.worksheet("Variabel")

# 3. HILFSFUNKTIONEN FÜR DATEN-TRANSFER & AUTOMATIK
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

def kategorisiere_fixkosten(name):
    name_lower = str(name).lower()
    if any(k in name_lower for k in ["versicherung", "vvg", "huk", "allianz", "krankenkasse", "kfz"]):
        return "Versicherungen"
    elif any(k in name_lower for k in ["strom", "energie", "vbw", "stadtwerke"]):
        return "Strom"
    elif any(k in name_lower for k in ["müll", "abfall", "muell"]):
        return "Müll"
    elif "abwasser" in name_lower:
        return "Abwasser"
    elif any(k in name_lower for k in ["wasser", "trinkwasser"]):
        return "Trinkwasser"
    elif any(k in name_lower for k in ["kredit", "darlehen", "bank", "rate", "finanzierung"]):
        return "Kredit"
    elif any(k in name_lower for k in ["abo", "netflix", "spotify", "disney", "prime", "gym", "rundfunk", "gez"]):
        return "Abos"
    else:
        return "Abos"

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

# Fixkosten filtern
aktive_fk = []
for _, fk in df_fixkosten_all.iterrows():
    if ist_fixkosten_aktiv(fk["Startmonat"], fk["Intervall"], ziel_str):
        aktive_fk.append(fk)
df_aktive_fk = pd.DataFrame(aktive_fk) if aktive_fk else pd.DataFrame(columns=["Name", "Betrag", "Intervall"])

# Variable Kosten filtern
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
    
    st.divider()
    
    # --- KUCHENDIAGRAMME ERSTELLEN ---
    st.subheader("📊 Ausgaben-Analyse nach Kategorien")
    
    # Monatliche Daten aufbereiten
    df_fk_chart = df_aktive_fk.copy()
    if not df_fk_chart.empty:
        df_fk_chart["Kategorie"] = df_fk_chart["Name"].apply(kategorisiere_fixkosten)
    else:
        df_fk_chart = pd.DataFrame(columns=["Kategorie", "Betrag"])
        
    df_var_chart = df_var_monat[["Kategorie", "Betrag"]].copy()
    df_monat_gesamt = pd.concat([df_fk_chart[["Kategorie", "Betrag"]], df_var_chart], ignore_index=True)
    if not df_monat_gesamt.empty:
        df_monat_gesamt = df_monat_gesamt.groupby("Kategorie", as_index=False)["Betrag"].sum()
    
    # Jährliche Daten aufbereiten
    aktuelle_jahr_str = aktueller_monat.strftime("%Y")
    if not df_variabel_all.empty:
        df_variabel_all["Datum"] = pd.to_datetime(df_variabel_all["Datum"])
        df_jahr_var = df_variabel_all[df_variabel_all["Datum"].dt.strftime("%Y") == aktuelle_jahr_str].copy()
    else:
        df_jahr_var = pd.DataFrame(columns=["Kategorie", "Betrag"])
        
    df_jahr_fk = df_fixkosten_all.copy()
    if not df_jahr_fk.empty:
        df_jahr_fk["Kategorie"] = df_jahr_fk["Name"].apply(kategorisiere_fixkosten)
        def auf_jahr_rechnen(row):
            intervall = row["Intervall"]
            if "monatlich" in intervall and "2-monatlich" not in intervall: return row["Betrag"] * 12
            elif "2-monatlich" in intervall: return row["Betrag"] * 6
            elif "3-monatlich" in intervall: return row["Betrag"] * 4
            elif "halbjährlich" in intervall: return row["Betrag"] * 2
            else: return row["Betrag"]
        df_jahr_fk["Betrag"] = df_jahr_fk.apply(auf_jahr_rechnen, axis=1)
    else:
        df_jahr_fk = pd.DataFrame(columns=["Kategorie", "Betrag"])
        
