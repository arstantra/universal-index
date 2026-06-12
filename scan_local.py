"""
scan_local.py — Universal Index: Scanner Locale
================================================
Scansiona una o più cartelle locali seguendo il protocollo dell'Archivio Personale.
Legge i README.md (frontmatter YAML), valida la struttura e aggiorna data.json.

USO:
    python scan_local.py --fonte ssd1 --root "D:/Archivio"
    python scan_local.py --fonte msi  --root "C:/Users/unoav/Archivio" --dry-run

REGOLE APPLICATE (Parser Rules):
  - Esplora cartelle-anno (es. 2020/, AA 2021-2022/)
  - Entra nelle sotto-cartelle con prefisso numerico (es. 010_NomeProgetto)
  - Legge README.md nella root della sotto-cartella (non annidati)
  - Ignora file/cartelle il cui nome inizia con _ (es. _archivio/, _bozza.pdf)
"""

import os, re, json, yaml, argparse, sys
from datetime import datetime, date
from pathlib import Path

# ─── CONFIG ────────────────────────────────────────────────────────────────────

DATA_JSON = Path(__file__).parent / "data.json"

STATI_VALIDI      = {"completo", "in-lavorazione", "da-revisionare", "archivio-morto"}
TIPI_VALIDI       = {"progetto", "relazione", "convegno", "appunti", "portfolio"}
DISCIPLINE_VALIDE = {"architettura", "storia", "urbanistica", "ingegneria", "altro"}
CONTESTI_VALIDI   = {"università", "scuola", "professionale", "personale"}
LINGUE_VALIDE     = {"it", "en", "it-en"}

RE_ANNO_FOLDER  = re.compile(r'^(\d{4}|AA \d{4}-\d{4}|AA\d{4}-\d{4})$', re.IGNORECASE)
RE_NUM_PREFIX   = re.compile(r'^\d{3}_')
RE_UNDERSCORE   = re.compile(r'^_')

# ─── YAML PARSING ──────────────────────────────────────────────────────────────

def extract_yaml(readme_path: Path) -> dict | None:
    """Estrae il blocco YAML frontmatter da un README.md."""
    try:
        text = readme_path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
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

    # Verifica file sciolti nella root (solo README.md ammesso)
    loose = [f for f in cartella.iterdir()
             if f.is_file()
             and f.name != 'README.md'
             and not RE_UNDERSCORE.match(f.name)]
    if loose:
        errori.append(f"File sciolti nella root: {[f.name for f in loose]}")

    # Se completo, deve esserci un output dichiarato
    if meta.get('stato') == 'completo' and not meta.get('output'):
        errori.append("Stato 'completo' ma campo 'output' mancante")

    return errori

# ─── SCANNER ───────────────────────────────────────────────────────────────────

def scan_root(root: Path, fonte_id: str) -> tuple[list[dict], list[dict]]:
    """
    Scansiona la root dell'archivio.
    Ritorna (voci, errori_strutturali).
    """
    voci = []
    errori_strutturali = []

    if not root.exists():
        print(f"[ERRORE] Cartella non trovata: {root}")
        return [], []

    # Esplora le cartelle-anno
    for anno_dir in sorted(root.iterdir()):
        if not anno_dir.is_dir(): continue
        if RE_UNDERSCORE.match(anno_dir.name): continue
        if not RE_ANNO_FOLDER.match(anno_dir.name):
            # Non è una cartella-anno riconosciuta, prova comunque ad entrare
            # (strutture ibride: 2020/, 2021/ senza prefisso AA)
            if not re.match(r'^\d{4}$', anno_dir.name):
                continue

        print(f"  📅 Anno: {anno_dir.name}")

        # Esplora le sotto-cartelle numeriche
        for prog_dir in sorted(anno_dir.iterdir()):
            if not prog_dir.is_dir(): continue
            if RE_UNDERSCORE.match(prog_dir.name): continue
            if not RE_NUM_PREFIX.match(prog_dir.name):
                errori_strutturali.append({
                    "percorso": str(prog_dir.relative_to(root)),
                    "errore": "Cartella senza prefisso numerico (es. 010_)"
                })
                continue

            readme = prog_dir / 'README.md'
            rel_path = str(prog_dir.relative_to(root)).replace('\\', '/')

            if not readme.exists():
                errori_strutturali.append({
                    "percorso": rel_path,
                    "errore": "README.md mancante"
                })
                print(f"    ⚠ {prog_dir.name}: README.md mancante")
                continue

            meta = extract_yaml(readme)
            if meta is None:
                errori_strutturali.append({
                    "percorso": rel_path,
                    "errore": "README.md senza frontmatter YAML valido"
                })
                print(f"    ⚠ {prog_dir.name}: YAML non valido")
                continue

            err = valida_voce(meta, prog_dir)
            if err:
                errori_strutturali.append({
                    "percorso": rel_path,
                    "errore": "; ".join(err)
                })

            # Anno: se AA usa l'anno di inizio
            anno_str = str(meta.get('anno', ''))
            if not anno_str:
                m = re.search(r'(\d{4})', anno_dir.name)
                anno_str = m.group(1) if m else ''

            voce = {
                "id":          f"{fonte_id}_{prog_dir.name}",
                "fonte":       fonte_id,
                "percorso":    rel_path,
                "titolo":      meta.get('titolo', prog_dir.name),
                "anno":        anno_str,
                "tipo":        meta.get('tipo', ''),
                "stato":       meta.get('stato', 'da-revisionare'),
                "disciplina":  meta.get('disciplina', ''),
                "contesto":    meta.get('contesto', ''),
                "lingua":      meta.get('lingua', 'it'),
                "output":      meta.get('output', ''),
                "tags":        meta.get('tags', []),
                "note":        meta.get('note', ''),
            }
            voci.append(voce)
            print(f"    ✓ {prog_dir.name} → [{voce['stato']}] {voce['titolo']}")

    return voci, errori_strutturali

# ─── AGGIORNA DATA.JSON ────────────────────────────────────────────────────────

def update_data_json(fonte_id: str, nuove_voci: list[dict], dry_run: bool = False):
    """Rimuove le voci precedenti della fonte e inserisce quelle nuove."""
    if not DATA_JSON.exists():
        print(f"[ERRORE] {DATA_JSON} non trovato. Crea il file prima di scansionare.")
        sys.exit(1)

    db = json.loads(DATA_JSON.read_text(encoding='utf-8'))

    # Controlla che la fonte esista nel registro
    fonte_registrata = any(f['id'] == fonte_id for f in db.get('fonti', []))
    if not fonte_registrata:
        print(f"[ATTENZIONE] Fonte '{fonte_id}' non trovata in data.json. Aggiungila prima dalla dashboard.")

    # Rimuovi voci precedenti della stessa fonte
    db['voci'] = [v for v in db.get('voci', []) if v.get('fonte') != fonte_id]
    db['voci'].extend(nuove_voci)
    db['meta']['lastUpdated'] = date.today().isoformat()

    if dry_run:
        print(f"\n[DRY RUN] Nessuna modifica salvata. Voci che verrebbero aggiunte: {len(nuove_voci)}")
        return

    DATA_JSON.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n✅ data.json aggiornato: {len(nuove_voci)} voci per la fonte '{fonte_id}'.")

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Universal Index — Scanner Locale')
    parser.add_argument('--fonte', required=True, help='ID della fonte (es. ssd1, msi, dell)')
    parser.add_argument('--root',  required=True, help='Percorso radice della cartella da scansionare')
    parser.add_argument('--dry-run', action='store_true', help='Simula senza scrivere su data.json')
    args = parser.parse_args()

    root = Path(args.root)
    print(f"\n🔍 Scansione fonte '{args.fonte}' in: {root}\n")

    voci, errori = scan_root(root, args.fonte)

    if errori:
        print(f"\n⚠  ERRORI STRUTTURALI ({len(errori)}):")
        for e in errori:
            print(f"   [{e['percorso']}] {e['errore']}")

    update_data_json(args.fonte, voci, dry_run=args.dry_run)
    print(f"\n📊 Riepilogo:")
    print(f"   Voci indicizzate: {len(voci)}")
    print(f"   Completi:        {sum(1 for v in voci if v['stato']=='completo')}")
    print(f"   In lavorazione:  {sum(1 for v in voci if v['stato']=='in-lavorazione')}")
    print(f"   Da revisionare:  {sum(1 for v in voci if v['stato']=='da-revisionare')}")
    print(f"   Archivio morto:  {sum(1 for v in voci if v['stato']=='archivio-morto')}")
    print(f"   Errori struttura:{len(errori)}")

if __name__ == '__main__':
    # Installa PyYAML se mancante
    try:
        import yaml
    except ImportError:
        print("Installo PyYAML...")
        os.system(f"{sys.executable} -m pip install pyyaml")
        import yaml
    main()
