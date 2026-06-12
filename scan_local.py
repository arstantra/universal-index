"""
scan_local.py — Universal Index: Scanner Locale
================================================
Scansiona le cartelle definite in sources.json seguendo il protocollo
dell'Archivio Personale. Legge i README.md (frontmatter YAML), valida
la struttura e aggiorna data.json.

USO (modalità config — raccomandata):
    python scan_local.py

USO (modalità manuale — una fonte sola):
    python scan_local.py --fonte ssd1 --root "D:/Archivio/2024"
    python scan_local.py --fonte msi  --root "C:/Archivio" --dry-run

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

SCRIPT_DIR   = Path(__file__).parent
DATA_JSON    = SCRIPT_DIR / "data.json"
SOURCES_JSON = SCRIPT_DIR / "sources.json"

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

def scan_percorso(percorso: Path, fonte_id: str) -> tuple[list[dict], list[dict]]:
    """
    Scansione ricorsiva libera: esplora tutta la cartella in profondità,
    trova ogni README.md con frontmatter YAML valido e lo indicizza.
    Ignora cartelle/file che iniziano con _.
    """
    if not percorso.exists():
        print(f"  [ERRORE] Cartella non trovata: {percorso}")
        return [], []

    print(f"  📂 {percorso}")

    voci = []
    errori = []
    root_str = str(percorso)

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
    """Legge sources.json e scansiona tutte le cartelle configurate."""
    if not SOURCES_JSON.exists():
        print(f"[ERRORE] {SOURCES_JSON} non trovato.")
        sys.exit(1)

    config = json.loads(SOURCES_JSON.read_text(encoding='utf-8'))

    for fonte in config.get('fonti', []):
        fonte_id = fonte['id']
        etichetta = fonte.get('etichetta', fonte_id)
        cartelle  = fonte.get('cartelle', [])

        print(f"\n🔍 Fonte: {etichetta} [{fonte_id}]")

        if not cartelle:
            print("  [SKIP] Nessuna cartella definita per questa fonte.")
            continue

        voci_totali, errori_totali = [], []
        for c in cartelle:
            v, e = scan_percorso(Path(c), fonte_id)
            voci_totali.extend(v)
            errori_totali.extend(e)

        if errori_totali:
            print(f"\n  ⚠  ERRORI STRUTTURALI ({len(errori_totali)}):")
            for e in errori_totali:
                print(f"     [{e['percorso']}] {e['errore']}")

        update_data_json(fonte_id, voci_totali, dry_run)
        stampa_riepilogo(voci_totali, errori_totali)

# ─── AGGIORNA DATA.JSON ────────────────────────────────────────────────────────

def update_data_json(fonte_id: str, nuove_voci: list[dict], dry_run: bool = False):
    """Rimuove le voci precedenti della fonte e inserisce quelle nuove."""
    if not DATA_JSON.exists():
        print(f"[ERRORE] {DATA_JSON} non trovato.")
        sys.exit(1)

    db = json.loads(DATA_JSON.read_text(encoding='utf-8'))

    fonte_registrata = any(f['id'] == fonte_id for f in db.get('fonti', []))
    if not fonte_registrata:
        print(f"  [ATTENZIONE] Fonte '{fonte_id}' non registrata in data.json. Aggiungila dalla dashboard.")

    db['voci'] = [v for v in db.get('voci', []) if v.get('fonte') != fonte_id]
    db['voci'].extend(nuove_voci)
    db['meta']['lastUpdated'] = date.today().isoformat()

    if dry_run:
        print(f"\n  [DRY RUN] Nessuna modifica salvata. Voci trovate: {len(nuove_voci)}")
        return

    DATA_JSON.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n  ✅ data.json aggiornato: {len(nuove_voci)} voci per '{fonte_id}'.")

def stampa_riepilogo(voci: list[dict], errori: list[dict]):
    print(f"  📊 Voci: {len(voci)} | "
          f"Completi: {sum(1 for v in voci if v['stato']=='completo')} | "
          f"In lav.: {sum(1 for v in voci if v['stato']=='in-lavorazione')} | "
          f"Da rev.: {sum(1 for v in voci if v['stato']=='da-revisionare')} | "
          f"Morti: {sum(1 for v in voci if v['stato']=='archivio-morto')} | "
          f"Errori: {len(errori)}")

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Universal Index — Scanner Locale')
    parser.add_argument('--fonte',   help='ID fonte (modalità manuale)')
    parser.add_argument('--root',    help='Cartella radice (modalità manuale)')
    parser.add_argument('--dry-run', action='store_true', help='Simula senza scrivere su data.json')
    args = parser.parse_args()

    if args.fonte and args.root:
        # Modalità manuale: scansiona un singolo percorso
        print(f"\n🔍 Scansione manuale fonte '{args.fonte}' in: {args.root}\n")
        voci, errori = scan_percorso(Path(args.root), args.fonte)
        if errori:
            print(f"\n⚠  ERRORI STRUTTURALI ({len(errori)}):")
            for e in errori:
                print(f"   [{e['percorso']}] {e['errore']}")
        update_data_json(args.fonte, voci, args.dry_run)
        stampa_riepilogo(voci, errori)
    else:
        # Modalità config: legge sources.json
        scan_da_config(dry_run=args.dry_run)

if __name__ == '__main__':
    try:
        import yaml
    except ImportError:
        print("Installo PyYAML...")
        os.system(f"{sys.executable} -m pip install pyyaml")
        import yaml
    main()
