"""
drive_sync.py — Universal Index: Sincronizzazione Google Drive
===============================================================
Modulo condiviso per caricare data.json su Google Drive.
Usato da scan_local.py (upload automatico dopo la scansione)
e da server.py (endpoint POST /api/sync-drive).

Il file su Drive è servito dall'Apps Script alla versione pubblica
su GitHub Pages: https://arstantra.github.io/universal-index

PREREQUISITI:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
    credentials_drive.json e token_drive.json in ../assets/ (fuori dal repo)
"""

from pathlib import Path

SCRIPT_DIR       = Path(__file__).parent
DATA_JSON        = SCRIPT_DIR / "data.json"
ASSETS_DIR       = SCRIPT_DIR.parent / "assets"
CREDENTIALS_FILE = ASSETS_DIR / "credentials_drive.json"
TOKEN_FILE       = ASSETS_DIR / "token_drive.json"
DRIVE_FILE_ID    = "1kwPvWoNAXeEIn1mm8YaFlTtolR1w_ps-"
DRIVE_SCOPES     = ["https://www.googleapis.com/auth/drive"]


def upload_to_drive(quiet: bool = False) -> tuple[bool, str]:
    """
    Carica data.json su Google Drive (aggiorna il file esistente, stesso ID).
    Ritorna (ok, messaggio). Non solleva eccezioni: tutti gli errori
    sono catturati e riportati nel messaggio.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        msg = ("Librerie Google mancanti. Esegui: pip install "
               "google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        if not quiet:
            print(f"  [DRIVE] {msg}")
        return False, msg

    if not DATA_JSON.exists():
        return False, f"data.json non trovato in {SCRIPT_DIR}"

    if not CREDENTIALS_FILE.exists():
        msg = f"credentials_drive.json non trovato in {ASSETS_DIR}"
        if not quiet:
            print(f"  [DRIVE] {msg}")
        return False, msg

    try:
        creds = None
        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), DRIVE_SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), DRIVE_SCOPES)
                creds = flow.run_local_server(port=0)
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

        service = build("drive", "v3", credentials=creds)
        media = MediaFileUpload(str(DATA_JSON), mimetype="application/json",
                                resumable=False)
        service.files().update(fileId=DRIVE_FILE_ID, media_body=media).execute()

        if not quiet:
            print("\n  ☁️  data.json caricato su Google Drive.")
        return True, "data.json caricato su Google Drive"

    except Exception as e:
        msg = f"Upload fallito: {e}"
        if not quiet:
            print(f"  [DRIVE] {msg}")
        return False, msg


if __name__ == "__main__":
    ok, msg = upload_to_drive()
    print(("✅ " if ok else "❌ ") + msg)
