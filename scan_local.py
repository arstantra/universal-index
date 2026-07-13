"""
scan_local.py — Universal Index: Scanner Locale
================================================
Scansiona le cartelle definite in sources.json seguendo il protocollo
dell'Archivio Personale. Legge i README.md (frontmatter YAML), valida
la struttura e aggiorna data.json.

USO (modalità config — raccomandata):
    python scan_local.py
    python scan_local.py --dry-run
    python scan_local.py --no-upload      # non carica su Google Drive

USO (modalità manuale — una sola etichetta):
    python scan_local.py --fonte msi --etichetta msi-rossa --root "C:/Users/.../Etichetta_Rossa"
    python scan_local.py --fonte ssd1 --etichetta ssd1-gialla --root "D:/Etichetta_Gialla" --dry-run

REGOLE APPLICATE (Parser Rules):
  - Esplora ricorsivamente il percorso radice dell'etichetta
  - Indicizza ogni cartella che contiene un README.md con frontmatter YAML
  - NON scende dentro una cartella già indicizzata (i README annidati
    più in profondità appartengono a repo/materiali interni, non all'indice)
  - Ignora file/cartelle il cui nome inizia con _ (es. _archivio/, _bozza.pdf)

GARANZIE AL RESCAN:
  - I campi gestiti dall'interfaccia (focus, focus_azione) vengono
    preservati: la scansione non azzera il pannello "Progetto attivo/Coda"
  - Vengono sostituite SOLO le voci della coppia (fonte, etichetta)
    scansionata; tutte le altre restano intatte
"""

import os, re, json, argparse, sys
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Installo PyYAML...")
    os.system(f'"{sys.executable}" -m pip install pyyaml --quiet')
    import yaml

from drive_sync import upload_to_drive

# ─── CONFIG ────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
DATA_JSON    = SCRIPT_DIR / "data.json"
SOURCES_JSON = SCRIPT_DIR / "sources.json"

STATI_VALIDI      = {"completo", "in-lavorazione", "da-revisionare", "archivio-morto"}
TIPI_VALIDI       = {"progetto", "relazione", "convegno", "appunti", "portfolio", "archivio"}
DISCIPLINE_VALIDE = {"architettura", "storia", "urbanistica", "ingegneria", "altro"}
CONTESTI_VALIDI   = {"università", "scuola", "professionale", "personale"}
LINGUE_VALIDE     = {"it", "en", "it-en"}

RE_UNDERSCORE = re.compile(r'^_')

# ─── CONTENITORI vs UNITÀ (stesse regole di crea_readme_minimi.py) ─────────────
# Un CONTENITORE (cartella-area di primo livello, cartella-anno o AA-anno) non è
# mai una voce: va sempre attraversato in profondità. Una UNITÀ con README valido
# è una voce e ferma la discesa. Così lo scanner non si blocca più al secondo
# livello quando un contenitore ha (per errore) un README.
RE_PREFISSO         = re.compile(r'^\d{2,3}[_\- ]')          # 010_, 27-, 001 ...
RE_CONTENITORE_ANNO = re.compile(
    r'^(aa[ _\-]*)?(19|20)\d{2}([ _\-]+(19|20)\d{2})?$', re.IGNORECASE)
CARTELLE_IGNORATE   = {
    "$RECYCLE.BIN", "System Volume Information", ".Trash-1000",
    ".Spotlight-V100", ".fseventsd", "found.000", "RECYCLER",
}

def _e_contenitore(nome: str, depth: int, ha_sottocartelle: bool) -> bool:
    """
    True se la cartella è un contenitore da attraversare senza indicizzare:
      - primo livello sotto la radice, con sottocartelle e senza prefisso NNN_
        (es. Area_Personale, Area_Istruzione)
      - cartella-anno / AA-anno a qualsiasi livello, con sottocartelle
        (es. 2019, 2020-2021, AA 2020-2021)
    """
    if not ha_sottocartelle:
        return False
    if depth == 1:
        return not RE_PREFISSO.match(nome)
    return bool(RE_CONTENITORE_ANNO.match(nome))

# ─── I/O ROBUSTO (byte nulli + scrittura atomica) ──────────────────────────────

def read_json_robusto(path: Path) -> dict:
    """Legge un JSON tollerando byte nulli/spazzatura finale (corruzione OneDrive)."""
    raw = path.read_bytes()
    if b"\x00" in raw:
        raw = raw.replace(b"\x00", b"")
    return json.loads(raw.decode("utf-8").strip())

def write_json_atomico(path: Path, data: dict):
    """Scrive su file temporaneo e poi os.replace: niente file a metà, niente corruzione."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

# Campi delle voci gestiti dall'interfaccia (server.py) e NON dal README:
# vanno preservati quando la voce viene rigenerata da una scansione.
CAMPI_PRESERVATI = ("focus", "focus_azione")

# ─── YAML PARSING ──────────────────────────────────────────────────────────────

def extract_yaml(readme_path: Path) -> dict | None:
    """Estrae il blocco YAML frontmatter da un README.md. None se assente/invalido."""
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
        meta = yaml.safe_load(parts[1])
        return meta if isinstance(meta, dict) else None
    except yaml.YAMLError:
        return None

# ─── VALIDAZIONE ───────────────────────────────────────────────────────────────

def valida_voce(meta: dict) -> list[str]:
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

def scan_percorso(percorso: Path, fonte_id: str,
                  etichetta_id: str | None = None) -> tuple[list[dict], list[dict]]:
    """
    Scansione ricorsiva a PIENA PROFONDITÀ.

    Regole di discesa (risolvono il blocco "si ferma al secondo livello"):
      - CONTENITORE (area di primo livello, cartella-anno/AA con sottocartelle):
        non è mai una voce, viene sempre attraversato — anche se contiene un
        README spurio. Prima invece un README su un anno bloccava la discesa
        e nascondeva tutti i progetti dentro.
      - UNITÀ con README.md dotato di frontmatter YAML valido: è una voce e
        ferma la discesa (i README annidati di repo/materiali non contano).
      - Cartella senza README valido (e non contenitore): non è una voce, ma
        si continua a scendere per raggiungere eventuali progetti più in fondo.
      - Cartelle/file con prefisso _ o cartelle di sistema: ignorati.
    """
    if not percorso.exists():
        print(f"  [ERRORE] Cartella non trovata: {percorso}")
        return [], []

    print(f"  📂 {percorso}")

    voci, errori = [], []

    for dirpath, dirnames, filenames in os.walk(percorso):
        # Rimuovi in-place le cartelle da ignorare (prefisso _ e cartelle di sistema)
        dirnames[:] = sorted(d for d in dirnames
                             if not RE_UNDERSCORE.match(d) and d not in CARTELLE_IGNORATE)

        cartella = Path(dirpath)
        rel = cartella.relative_to(percorso)
        depth = len(rel.parts)   # 0 = radice, 1 = primo livello, ...

        # Contenitore → si attraversa sempre, non è mai una voce
        if depth >= 1 and _e_contenitore(cartella.name, depth, bool(dirnames)):
            continue

        if 'README.md' not in filenames:
            continue  # nessuna voce qui, ma os.walk prosegue in profondità

        meta = extract_yaml(cartella / 'README.md')
        if meta is None:
            continue  # README senza frontmatter: non è una voce, si prosegue

        # Unità indicizzata → non scendere oltre (evita duplicati annidati)
        dirnames.clear()

        rel_path = str(cartella.relative_to(percorso)).replace('\\', '/')
        if rel_path == '.':
            rel_path = cartella.name

        err = valida_voce(meta)
        if err:
            errori.append({"percorso": rel_path, "errore": "; ".join(err)})

        anno_str = str(meta.get('anno') or '')
        if not anno_str or anno_str == 'None':
            m = re.search(r'(\d{4})', rel_path)
            anno_str = m.group(1) if m else ''

        # ID univoco: fonte + percorso relativo senza slash
        voce_id = f"{fonte_id}_{rel_path.replace('/', '_')}"

        voce = {
            "id":         voce_id,
            "fonte":      fonte_id,
            "etichetta":  etichetta_id,   # None se non classificato
            "percorso":   rel_path,
            "titolo":     meta.get('titolo') or cartella.name,
            "anno":       anno_str,
            "tipo":       meta.get('tipo', ''),
            "stato":      meta.get('stato', 'da-revisionare'),
            "disciplina": meta.get('disciplina', ''),
            "contesto":   meta.get('contesto', ''),
            "lingua":     meta.get('lingua', 'it'),
            "output":     meta.get('output', ''),
            "tags":       meta.get('tags') or [],
            "note":       meta.get('note', ''),
        }
        voci.append(voce)
        print(f"    ✓ {rel_path} → [{voce['stato']}] {voce['titolo']}")

    return voci, errori

# ─── AGGIORNA DATA.JSON ────────────────────────────────────────────────────────

def update_data_json(fonte_id: str, etichetta_id: str | None,
                     nuove_voci: list[dict], dry_run: bool = False):
    """
    Sostituisce le voci della coppia (fonte, etichetta) con quelle nuove,
    lasciando intatte tutte le altre. Preserva i campi gestiti
    dall'interfaccia (focus, focus_azione) sulle voci che sopravvivono.
    """
    if not DATA_JSON.exists():
        print(f"[ERRORE] {DATA_JSON} non trovato.")
        sys.exit(1)

    db = read_json_robusto(DATA_JSON)

    if not any(f['id'] == fonte_id for f in db.get('fonti', [])):
        print(f"    [ATTENZIONE] Supporto '{fonte_id}' non registrato in data.json. "
              f"Aggiungilo dalla dashboard (server locale) prima di scansionare.")

    # Salva i campi da preservare, indicizzati per id voce
    preservati = {
        v['id']: {k: v[k] for k in CAMPI_PRESERVATI if k in v}
        for v in db.get('voci', [])
        if any(k in v for k in CAMPI_PRESERVATI)
    }

    # Rimuovi solo le voci di questa (fonte + etichetta)
    db['voci'] = [v for v in db.get('voci', [])
                  if not (v.get('fonte') == fonte_id and v.get('etichetta') == etichetta_id)]
    db['voci'].extend(nuove_voci)

    # Riapplica focus/focus_azione alle voci ricreate
    for v in db['voci']:
        if v['id'] in preservati:
            v.update(preservati[v['id']])

    db['meta']['lastUpdated'] = date.today().isoformat()

    if dry_run:
        print(f"\n    [DRY RUN] Nessuna modifica salvata. Voci trovate: {len(nuove_voci)}")
        return

    write_json_atomico(DATA_JSON, db)
    print(f"\n    ✅ data.json aggiornato: {len(nuove_voci)} voci per '{fonte_id}/{etichetta_id}'.")


def stampa_riepilogo(voci: list[dict], errori: list[dict]):
    print(f"  📊 Voci: {len(voci)} | "
          f"Completi: {sum(1 for v in voci if v['stato']=='completo')} | "
          f"In lav.: {sum(1 for v in voci if v['stato']=='in-lavorazione')} | "
          f"Da rev.: {sum(1 for v in voci if v['stato']=='da-revisionare')} | "
          f"Morti: {sum(1 for v in voci if v['stato']=='archivio-morto')} | "
          f"Errori: {len(errori)}")

# ─── SCANSIONE DA CONFIG ───────────────────────────────────────────────────────

def scan_da_config(dry_run: bool = False):
    """
    Legge sources.json (formato gerarchico: supporto > etichette)
    e scansiona tutte le etichette con percorso_radice definito.
    """
    if not SOURCES_JSON.exists():
        print(f"[ERRORE] {SOURCES_JSON} non trovato.")
        sys.exit(1)

    config = json.loads(SOURCES_JSON.read_text(encoding='utf-8'))

    for supporto in config.get('fonti', []):
        if supporto.get('tipo') == 'gdrive':
            continue  # i Google Drive si scansionano con scan_gdrive.py

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
                print("    [SKIP] Nessun percorso_radice definito (supporto non collegato).")
                continue

            voci, errori = scan_percorso(Path(percorso), fonte_id, etichetta_id=et_id)

            if errori:
                print(f"\n    ⚠  ERRORI STRUTTURALI ({len(errori)}):")
                for e in errori:
                    print(f"       [{e['percorso']}] {e['errore']}")

            update_data_json(fonte_id, et_id, voci, dry_run)
            stampa_riepilogo(voci, errori)

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Universal Index — Scanner Locale')
    parser.add_argument('--fonte',     help='ID supporto (modalità manuale, es. msi)')
    parser.add_argument('--etichetta', help='ID etichetta (modalità manuale, es. msi-rossa)')
    parser.add_argument('--root',      help='Cartella radice (modalità manuale)')
    parser.add_argument('--dry-run',   action='store_true', help='Simula senza scrivere su data.json')
    parser.add_argument('--no-upload', action='store_true', help='Non caricare data.json su Google Drive')
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

    if not args.dry_run and not args.no_upload:
        upload_to_drive()

if __name__ == '__main__':
    main()
