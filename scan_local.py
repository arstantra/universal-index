"""
scan_local.py — Universal Index: Scanner Locale
================================================
Scansiona le cartelle definite in sources.json seguendo il protocollo
dell'Archivio Personale. Legge i README.md (frontmatter YAML), valida
la struttura e aggiorna data.json.

USO (modalità config — raccomandata):
    python scan_local.py

USO (modalità manuale — una sola etichetta):
    python scan_local.py --fonte msi --etichetta msi-rossa --root "C:/Users/.../Etichetta_Rossa"
    python scan_local.py --fonte ssd1 --etichetta ssd1-gialla --root "D:/Etichetta_Gialla" --dry-run

REGOLE APPLICATE (Parser Rules):
  - Ogni cartella in sources.json è trattata come cartella-anno o come
    root contenente cartelle-anno
  - Entra nelle sotto-cartelle con prefisso numerico (es. 010_NomeProgetto)
  - Legge README.md nella root della sotto-cartella (non annidati)
  - Ignora file/cartelle il cui nome inizia con _ (es. _archivio/, _bozza.pdf)
"""

import os, re, json, yaml, argparse, sys
from datetime import datetime, date
from pathlib import Path

# ─── CONFIG ────────────────────────────────────────────────────────────────────

SCRIPT_DIR       = Path(__file__).parent
DATA_JSON        = SCRIPT_DIR / "data.json"
SOURCES_JSON     = SCRIPT_DIR / "sources.json"
ASSETS_DIR       = SCRIPT_DIR.parent / "assets"
CREDENTIALS_FILE = ASSETS_DIR / "credentials_drive.json"
TOKEN_FILE       = ASSETS_DIR / "token_drive.json"
DRIVE_FILE_ID    = "1kwPvWoNAXeEIn1mm8YaFlTtolR1w_ps-"
DRIVE_SCOPES     = ["https://www.googleapis.com/auth/drive"]

STATI_VALIDI      = {"completo", "in-lavorazione", "da-revisionare", "archivio-morto"}
TIPI_VALIDI       = {"progetto", "relazione", "convegno", "appunti", "portfolio"}
DISCIPLINE_VALIDE = {"architettura", "storia", "urbanistica", "ingegneria", "altro"}
CONTESTI_VALIDI   = {"università", "scuola", "professionale", "personale"}
LINGUE_VALIDE     = {"it", "en", "it-en"}

RE_NUM_PREFIX  = re.compile(r'^\d{3}_')
RE_UNDERSCORE  = re.compile(r'^_')

# ─── YAML PARSING ──────────────────────────────────────────────────────────────

def extract_yaml(readme_path: Path) -> dict | None:
    """Estrae il blocco YAML frontmatter da un README.md."""
    try:
        text = readme_path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None

    if not text.startswith('---'):
        return None

    parts = text.split('---', 2)
    if len(parts) < 3:
        return None

    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None

# ─── VALIDAZIONE ───────────────────────────────────────────────────────────────

def valida_voce(meta: dict, cartella: Path) -> list[str]:
    """Ritorna lista di errori di validazione (vuota = OK)."""
    errori = []
    if not meta.get('titolo'):
        errori.append("Campo 'titolo' mancante")
    if not meta.get('anno'):
        errori.append("Campo 'anno' mancante")
    elif not re.match(r'^\d{4}$', str(meta['anno'])):
        errori.append(f"'anno' non valido: {meta['anno']} (atteso YYYY)")
    if meta.get('tipo') and meta['tipo'] not in TIPI_VALIDI:
        errori.append(f"'tipo' non valido: {meta['tipo']}")
    if meta.get('stato') and meta['stato'] not in STATI_VALIDI:
        errori.append(f"'stato' non valido: {meta['stato']}")
    if meta.get('disciplina') and meta['disciplina'] not in DISCIPLINE_VALIDE:
        errori.append(f"'disciplina' non valida: {meta['disciplina']}")
    if meta.get('contesto') and meta['contesto'] not in CONTESTI_VALIDI:
        errori.append(f"'contesto' non valido: {meta['contesto']}")
    if meta.get('lingua') and meta['lingua'] not in LINGUE_VALIDE:
        errori.append(f"'lingua' non valida: {meta['lingua']}")
    if meta.get('stato') == 'completo' and not meta.get('output'):
        errori.append("Stato 'completo' ma campo 'output' mancante")
    return errori

# ─── SCANNER ───────────────────────────────────────────────────────────────────

def scan_percorso(percorso: Path, fonte_id: str, etichetta_id: str | None = None) -> tuple[list[dict], list[dict]]:
    """
    Scansione ricorsiva libera: esplora tutta la cartella in profondità,
    trova ogni README.md con frontmatter YAML valido e lo indicizza.
    Ignora cartelle/file che iniziano con _.
    etichetta_id: id dell'etichetta (es. 'msi-rossa') da assegnare alle voci.
    """
    if not percorso.exists():
        print(f"  [ERRORE] Cartella non trovata: {percorso}")
        return [], []

    print(f"  📂 {percorso}")

    voci = []
    errori = []

    for dirpath, dirnames, filenames in os.walk(percorso):
        # Rimuovi in-place le cartelle da ignorare (prefisso _)
        dirnames[:] = sorted(d for d in dirnames if not RE_UNDERSCORE.match(d))

        if 'README.md' not in filenames:
            continue

        cartella = Path(dirpath)
        readme   = cartella / 'README.md'
        rel_path = str(cartella.relative_to(percorso)).replace('\\', '/')

        meta = extract_yaml(readme)
        if meta is None:
            continue  # README senza frontmatter: non è una voce di archivio

        err = valida_voce(meta, cartella)
        if err:
            errori.append({"percorso": rel_path, "errore": "; ".join(err)})

        anno_str = str(meta.get('anno', ''))
        if not anno_str:
            m = re.search(r'(\d{4})', rel_path)
            anno_str = m.group(1) if m else ''

        # ID univoco: fonte + percorso relativo senza slash
        voce_id = f"{fonte_id}_{rel_path.replace('/', '_')}"

        voce = {
            "id":         voce_id,
            "fonte":      fonte_id,
            "etichetta":  etichetta_id,   # None se non classificato
            "percorso":   rel_path,
            "titolo":     meta.get('titolo', cartella.name),
            "anno":       anno_str,
            "tipo":       meta.get('tipo', ''),
            "stato":      meta.get('stato', 'da-revisionare'),
            "disciplina": meta.get('disciplina', ''),
            "contesto":   meta.get('contesto', ''),
            "lingua":     meta.get('lingua', 'it'),
            "output":     meta.get('output', ''),
            "tags":       meta.get('tags', []),
            "note":       meta.get('note', ''),
        }
        voci.append(voce)
        print(f"    ✓ {rel_path} → [{voce['stato']}] {voce['titolo']}")

    return voci, errori


def scan_da_config(dry_run: bool = False):
    """
    Legge sources.json (nuovo formato gerarchico: supporto > etichette)
    e scansiona tutte le etichette configurate.
    """
    if not SOURCES_JSON.exists():
        print(f"[ERRORE] {SOURCES_JSON} non trovato.")
        sys.exit(1)

    config = json.loads(SOURCES_JSON.read_text(encoding='utf-8'))

    for supporto in config.get('fonti', []):
        fonte_id    = supporto['id']
        fonte_label = supporto.get('label', fonte_id)
        etichette   = supporto.get('etichette', [])

        print(f"\n🖥  Supporto: {fonte_label} [{fonte_id}]")

        if not etichette:
            print("  [SKIP] Nessuna etichetta configurata.")
            continue

        for et in etichette:
            et_id    = et.get('id')
            et_label = et.get('label', et_id)
            percorso = et.get('percorso_radice')

            print(f"\n  🏷  Etichetta: {et_label} [{et_id}]")

            if not percorso:
                print("    [SKIP] Nessun percorso_radice definito.")
                continue

            voci, errori = scan_percorso(Path(percorso), fonte_id, etichetta_id=et_id)

            if errori:
                print(f"\n    ⚠  ERRORI STRUTTURALI ({len(errori)}):")
                for e in errori:
                    print(f"       [{e['percorso']}] {e['errore']}")

            update_data_json(fonte_id, et_id, voci, dry_run)
            stampa_riepilogo(voci, errori)

# ─── AGGIORNA DATA.JSON ────────────────────────────────────────────────────────

def update_data_json(fonte_id: str, etichetta_id: str | None, nuove_voci: list[dict], dry_run: bool = False):
    """
    Rimuove le voci precedenti della (fonte, etichetta) e inserisce quelle nuove.
    Questo permette di ri-scansionare una singola etichetta senza toccare le altre.
    """
    if not DATA_JSON.exists():
        print(f"[ERRORE] {DATA_JSON} non trovato.")
        sys.exit(1)

    db = json.loads(DATA_JSON.read_text(encoding='utf-8'))

    fonte_registrata = any(f['id'] == fonte_id for f in db.get('fonti', []))
    if not fonte_registrata:
        print(f"    [ATTENZIONE] Supporto '{fonte_id}' non registrato in data.json. Aggiungilo dalla dashboard.")

    # Rimuovi solo le voci di questa (fonte + etichetta)
    db['voci'] = [v for v in db.get('voci', [])
                  if not (v.get('fonte') == fonte_id and v.get('etichetta') == etichetta_id)]
    db['voci'].extend(nuove_voci)
    db['meta']['lastUpdated'] = date.today().isoformat()

    if dry_run:
        print(f"\n    [DRY RUN] Nessuna modifica salvata. Voci trovate: {len(nuove_voci)}")
        return

    DATA_JSON.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n    ✅ data.json aggiornato: {len(nuove_voci)} voci per '{fonte_id}/{etichetta_id}'.")

def stampa_riepilogo(voci: list[dict], errori: list[dict]):
    print(f"  📊 Voci: {len(voci)} | "
          f"Completi: {sum(1 for v in voci if v['stato']=='completo')} | "
          f"In lav.: {sum(1 for v in voci if v['stato']=='in-lavorazione')} | "
          f"Da rev.: {sum(1 for v in voci if v['stato']=='da-revisionare')} | "
          f"Morti: {sum(1 for v in voci if v['stato']=='archivio-morto')} | "
          f"Errori: {len(errori)}")

# ─── GOOGLE DRIVE UPLOAD ───────────────────────────────────────────────────────

def upload_to_drive():
    """Carica data.json su Google Drive (aggiorna file esistente, stesso ID)."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("\n  [DRIVE] Librerie mancanti. Esegui:")
        print("  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return

    if not CREDENTIALS_FILE.exists():
        print(f"\n  [DRIVE] credentials_drive.json non trovato in {ASSETS_DIR}")
        return

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), DRIVE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), DRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding='utf-8')

    service = build("drive", "v3", credentials=creds)
    media = MediaFileUpload(str(DATA_JSON), mimetype="application/json", resumable=False)
    service.files().update(fileId=DRIVE_FILE_ID, media_body=media).execute()
    print(f"\n  ☁️  data.json caricato su Google Drive.")

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Universal Index — Scanner Locale')
    parser.add_argument('--fonte',     help='ID supporto (modalità manuale, es. msi)')
    parser.add_argument('--etichetta', help='ID etichetta (modalità manuale, es. msi-rossa)')
    parser.add_argument('--root',      help='Cartella radice (modalità manuale)')
    parser.add_argument('--dry-run',   action='store_true', help='Simula senza scrivere su data.json')
    args = parser.parse_args()

    if args.fonte and args.root:
        # Modalità manuale: scansiona un singolo percorso
        et_id = args.etichetta or None
        print(f"\n🔍 Scansione manuale '{args.fonte}' / etichetta '{et_id}' in: {args.root}\n")
        voci, errori = scan_percorso(Path(args.root), args.fonte, etichetta_id=et_id)
        if errori:
            print(f"\n⚠  ERRORI STRUTTURALI ({len(errori)}):")
            for e in errori:
                print(f"   [{e['percorso']}] {e['errore']}")
        update_data_json(args.fonte, et_id, voci, args.dry_run)
        stampa_riepilogo(voci, errori)
    else:
        # Modalità config: legge sources.json
        scan_da_config(dry_run=args.dry_run)

    if not args.dry_run:
        upload_to_drive()

if __name__ == '__main__':
    try:
        import yaml
    except ImportError:
        print("Installo PyYAML...")
        os.system(f"{sys.executable} -m pip install pyyaml")
        import yaml
    main()
