"""
scan_gdrive.py — Universal Index: Scanner Google Drive via rclone
=================================================================
Scansiona i remote rclone configurati in sources.json (sezione gdrive),
legge i README.md presenti su Drive (se esistono) e aggiorna data.json.

Se un README.md non è presente, crea una voce base con i dati del percorso.

USO:
    python scan_gdrive.py
    python scan_gdrive.py --dry-run

PREREQUISITI:
    - rclone installato e configurato (rclone config)
    - sources.json aggiornato con la sezione gdrive_fonti
"""

import os, re, json, subprocess, sys, tempfile, yaml, argparse
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_JSON  = SCRIPT_DIR / "data.json"
SOURCES_JSON = SCRIPT_DIR / "sources.json"

STATI_VALIDI = {"completo", "in-lavorazione", "da-revisionare", "archivio-morto"}
TIPI_VALIDI  = {"progetto", "relazione", "convegno", "appunti", "portfolio", "archivio"}
RE_NUM_PREFIX = re.compile(r'^\d{3}_')

# ─── RCLONE HELPERS ────────────────────────────────────────────────────────────

def rclone_lsjson(remote: str, max_depth: int = 6) -> list[dict]:
    """Restituisce il listing JSON di un remote rclone."""
    cmd = ["rclone", "lsjson", "--recursive", "--fast-list",
           f"--max-depth={max_depth}", remote]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"  [ERRORE rclone] {result.stderr.strip()}")
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT] rclone ha impiegato troppo su {remote}")
        return []
    except Exception as e:
        print(f"  [ERRORE] {e}")
        return []

def rclone_cat(remote_path: str) -> str | None:
    """Scarica il contenuto testuale di un file remoto."""
    cmd = ["rclone", "cat", remote_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return None

# ─── YAML PARSING ──────────────────────────────────────────────────────────────

def extract_yaml(text: str) -> dict | None:
    if not text or not text.startswith('---'):
        return None
    parts = text.split('---', 2)
    if len(parts) < 3:
        return None
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None

# ─── SCANNER ───────────────────────────────────────────────────────────────────

def scan_gdrive_remote(remote_name: str, fonte_id: str, max_depth: int = 6) -> list[dict]:
    """
    Scansiona un remote rclone:
    - Cerca cartelle con prefisso numerico (es. 010_Progetto)
    - Se trova README.md, legge il frontmatter YAML
    - Altrimenti crea una voce base dal percorso
    """
    remote = f"{remote_name}:"
    print(f"  🌐 Scansione {remote} ...")

    entries = rclone_lsjson(remote, max_depth)
    if not entries:
        print("  [VUOTO o ERRORE]")
        return []

    # Costruisce un set di percorsi README.md esistenti
    readme_paths = {
        e["Path"] for e in entries
        if not e.get("IsDir") and e["Name"].lower() == "readme.md"
    }

    # Trova le cartelle con prefisso numerico
    cartelle_numeriche = [
        e for e in entries
        if e.get("IsDir") and RE_NUM_PREFIX.match(Path(e["Path"]).name)
        and not Path(e["Path"]).name.startswith("_")
    ]

    voci = []
    for cartella in cartelle_numeriche:
        cart_path = cartella["Path"]
        cart_name = Path(cart_path).name
        readme_rel = f"{cart_path}/README.md"

        meta = None
        if readme_rel in readme_paths:
            content = rclone_cat(f"{remote}{readme_rel}")
            if content:
                meta = extract_yaml(content)

        # Estrai anno dal percorso
        anno_str = ""
        m = re.search(r'(\d{4})', cart_path)
        if m:
            anno_str = m.group(1)

        # Nome pulito (rimuove prefisso numerico)
        titolo_clean = re.sub(r'^\d{3}_', '', cart_name).replace('_', ' ')

        voce_id = f"{fonte_id}_{cart_path.replace('/', '_')}"

        if meta:
            voce = {
                "id":         voce_id,
                "fonte":      fonte_id,
                "etichetta":  None,
                "percorso":   cart_path,
                "titolo":     meta.get("titolo", titolo_clean),
                "anno":       str(meta.get("anno", anno_str)),
                "tipo":       meta.get("tipo", ""),
                "stato":      meta.get("stato", "da-revisionare"),
                "disciplina": meta.get("disciplina", ""),
                "contesto":   meta.get("contesto", ""),
                "lingua":     meta.get("lingua", "it"),
                "output":     meta.get("output", ""),
                "tags":       meta.get("tags", []),
                "note":       meta.get("note", ""),
            }
            print(f"    ✓ {cart_path} → [README] {voce['titolo']}")
        else:
            voce = {
                "id":         voce_id,
                "fonte":      fonte_id,
                "etichetta":  None,
                "percorso":   cart_path,
                "titolo":     titolo_clean,
                "anno":       anno_str,
                "tipo":       "",
                "stato":      "da-revisionare",
                "disciplina": "",
                "contesto":   "",
                "lingua":     "it",
                "output":     "",
                "tags":       [],
                "note":       "",
            }
            print(f"    · {cart_path} → [base] {voce['titolo']}")

        voci.append(voce)

    print(f"  📊 Trovate {len(voci)} cartelle su {remote}")
    return voci

def update_data_json(fonte_id: str, nuove_voci: list[dict], dry_run: bool = False):
    db = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    db["voci"] = [v for v in db.get("voci", []) if v.get("fonte") != fonte_id]
    db["voci"].extend(nuove_voci)
    db["meta"]["lastUpdated"] = date.today().isoformat()

    if dry_run:
        print(f"  [DRY RUN] Voci che verrebbero scritte: {len(nuove_voci)}")
        return

    DATA_JSON.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ data.json aggiornato: {len(nuove_voci)} voci per '{fonte_id}'")

# ─── MAIN ──────────────────────────────────────────────────────────────────────

# Mapping: fonte_id → nome remote rclone
GDRIVE_REMOTES = {
    "gdrive-gmail":   "drive-personale",
    # Aggiungi altri account quando li configuri con rclone:
    # "gdrive-andipol": "drive-andipol",
    # "gdrive-unoav":   "drive-unoav",
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fonte",   help="Scansiona solo questa fonte (es. gdrive-gmail)")
    args = parser.parse_args()

    print("\n=== Universal Index — Scanner Google Drive ===\n")

    for fonte_id, remote_name in GDRIVE_REMOTES.items():
        if args.fonte and args.fonte != fonte_id:
            continue
        print(f"📂 {fonte_id} ({remote_name})")
        voci = scan_gdrive_remote(remote_name, fonte_id)
        update_data_json(fonte_id, voci, args.dry_run)

    print("\n=== Completato ===")

if __name__ == "__main__":
    try:
        import yaml
    except ImportError:
        os.system(f"{sys.executable} -m pip install pyyaml --break-system-packages --quiet")
        import yaml
    main()
