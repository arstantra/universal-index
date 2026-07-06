"""
scan_gdrive.py — Universal Index: Scanner Google Drive via rclone
=================================================================
Scansiona i remote rclone dei supporti con tipo "gdrive" definiti in
sources.json, legge i README.md presenti su Drive (se esistono) e
aggiorna data.json.

Cosa viene indicizzato (per contenere il rumore):
  - cartelle che contengono un README.md con frontmatter YAML valido
  - cartelle con prefisso numerico NNN_ (protocollo archivio) anche senza README
  Le altre cartelle sono solo attraversate, non indicizzate.

USO:
    python scan_gdrive.py
    python scan_gdrive.py --dry-run
    python scan_gdrive.py --fonte gdrive-gmail
    python scan_gdrive.py --max-depth 8 --timeout 300

PREREQUISITI:
    - rclone installato e configurato (rclone config)
    - sources.json: fonte con "tipo": "gdrive" e "remote": "<nome-remote-rclone>"

GARANZIE AL RESCAN:
  - focus / focus_azione preservati (gestiti dall'interfaccia)
  - etichette assegnate a mano preservate ("si indicizza prima, si ordina poi")
"""

import os, re, json, subprocess, sys, argparse
from datetime import date
from pathlib import PurePosixPath, Path

try:
    import yaml
except ImportError:
    os.system(f'"{sys.executable}" -m pip install pyyaml --quiet')
    import yaml

SCRIPT_DIR   = Path(__file__).parent
DATA_JSON    = SCRIPT_DIR / "data.json"
SOURCES_JSON = SCRIPT_DIR / "sources.json"

RE_NUM_PREFIX = re.compile(r'^\d{3}_')

# Campi gestiti dall'interfaccia o assegnati a mano: preservati al rescan
CAMPI_PRESERVATI = ("focus", "focus_azione", "etichetta")

# ─── RCLONE HELPERS ────────────────────────────────────────────────────────────

def rclone_lsjson(remote: str, max_depth: int, timeout: int) -> list[dict]:
    """Restituisce il listing JSON ricorsivo di un remote rclone."""
    cmd = ["rclone", "lsjson", "--recursive", "--fast-list",
           f"--max-depth={max_depth}", remote]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', timeout=timeout)
        if result.returncode != 0:
            print(f"  [ERRORE rclone] {result.stderr.strip()}")
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except FileNotFoundError:
        print("  [ERRORE] rclone non trovato. Installalo e configura il remote.")
        return []
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT] rclone ha impiegato più di {timeout}s su {remote}. "
              f"Riprova con --timeout più alto o --max-depth più basso.")
        return []
    except Exception as e:
        print(f"  [ERRORE] {e}")
        return []


def rclone_cat(remote_path: str) -> str | None:
    """Scarica il contenuto testuale di un file remoto."""
    try:
        result = subprocess.run(["rclone", "cat", remote_path],
                                capture_output=True, text=True,
                                encoding='utf-8', timeout=30)
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
        meta = yaml.safe_load(parts[1])
        return meta if isinstance(meta, dict) else None
    except yaml.YAMLError:
        return None

# ─── SCANNER ───────────────────────────────────────────────────────────────────

def _is_ignored(path: str) -> bool:
    """True se il percorso contiene un segmento con prefisso _ (protocollo)."""
    return any(part.startswith('_') for part in PurePosixPath(path).parts)


def scan_gdrive_remote(remote_name: str, fonte_id: str,
                       max_depth: int, timeout: int) -> list[dict]:
    """
    Scansiona un remote rclone e indicizza:
      - cartelle con README.md dotato di frontmatter YAML valido
      - cartelle con prefisso NNN_ anche senza README (voce base, da-revisionare)
    Se una cartella indicizzata ne contiene altre indicizzabili, vengono
    saltate (nessun duplicato annidato).
    """
    remote = f"{remote_name}:"
    print(f"  🌐 Scansione {remote} (max-depth {max_depth}) ...")

    entries = rclone_lsjson(remote, max_depth, timeout)
    if not entries:
        print("  [VUOTO o ERRORE]")
        return []

    readme_paths = {
        e["Path"] for e in entries
        if not e.get("IsDir") and e["Name"].lower() == "readme.md"
        and not _is_ignored(e["Path"])
    }

    cartelle = sorted(
        (e["Path"] for e in entries
         if e.get("IsDir") and not _is_ignored(e["Path"])),
        key=lambda p: p.count('/')
    )

    voci = []
    indicizzate: list[str] = []   # percorsi già indicizzati (per pruning annidati)

    for cart_path in cartelle:
        # Salta le cartelle dentro una cartella già indicizzata
        if any(cart_path.startswith(p + '/') for p in indicizzate):
            continue

        cart_name  = PurePosixPath(cart_path).name
        has_readme = f"{cart_path}/README.md" in readme_paths
        has_prefix = bool(RE_NUM_PREFIX.match(cart_name))

        if not has_readme and not has_prefix:
            continue

        meta = None
        if has_readme:
            content = rclone_cat(f"{remote}{cart_path}/README.md")
            if content:
                meta = extract_yaml(content)

        if meta is None and not has_prefix:
            continue  # README presente ma senza frontmatter valido

        anno_str = ""
        m = re.search(r'(\d{4})', cart_path)
        if m:
            anno_str = m.group(1)

        titolo_clean = RE_NUM_PREFIX.sub('', cart_name).replace('_', ' ')
        voce_id = f"{fonte_id}_{cart_path.replace('/', '_')}"

        if meta:
            voce = {
                "id":         voce_id,
                "fonte":      fonte_id,
                "etichetta":  None,
                "percorso":   cart_path,
                "titolo":     meta.get("titolo") or titolo_clean,
                "anno":       str(meta.get("anno") or anno_str),
                "tipo":       meta.get("tipo", ""),
                "stato":      meta.get("stato", "da-revisionare"),
                "disciplina": meta.get("disciplina", ""),
                "contesto":   meta.get("contesto", ""),
                "lingua":     meta.get("lingua", "it"),
                "output":     meta.get("output", ""),
                "tags":       meta.get("tags") or [],
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
        indicizzate.append(cart_path)

    print(f"  📊 Indicizzate {len(voci)} cartelle su {remote} "
          f"(su {len(entries)} elementi totali)")
    return voci

# ─── AGGIORNA DATA.JSON ────────────────────────────────────────────────────────

def update_data_json(fonte_id: str, nuove_voci: list[dict], dry_run: bool = False):
    """
    Sostituisce le voci della fonte preservando focus, focus_azione e
    le etichette assegnate a mano dall'interfaccia.
    """
    if not DATA_JSON.exists():
        print(f"[ERRORE] {DATA_JSON} non trovato.")
        sys.exit(1)

    db = json.loads(DATA_JSON.read_text(encoding="utf-8"))

    preservati = {}
    for v in db.get("voci", []):
        if v.get("fonte") != fonte_id:
            continue
        campi = {k: v[k] for k in CAMPI_PRESERVATI if v.get(k) is not None}
        if campi:
            preservati[v["id"]] = campi

    db["voci"] = [v for v in db.get("voci", []) if v.get("fonte") != fonte_id]
    db["voci"].extend(nuove_voci)

    for v in db["voci"]:
        if v.get("fonte") == fonte_id and v["id"] in preservati:
            for k, val in preservati[v["id"]].items():
                if v.get(k) is None:
                    v[k] = val

    db["meta"]["lastUpdated"] = date.today().isoformat()

    if dry_run:
        print(f"  [DRY RUN] Voci che verrebbero scritte: {len(nuove_voci)}")
        return

    DATA_JSON.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ data.json aggiornato: {len(nuove_voci)} voci per '{fonte_id}'")

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Universal Index — Scanner Google Drive')
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--fonte",     help="Scansiona solo questa fonte (es. gdrive-gmail)")
    parser.add_argument("--max-depth", type=int, default=6, help="Profondità massima (default 6)")
    parser.add_argument("--timeout",   type=int, default=300, help="Timeout rclone in secondi (default 300)")
    args = parser.parse_args()

    if not SOURCES_JSON.exists():
        print(f"[ERRORE] {SOURCES_JSON} non trovato.")
        sys.exit(1)

    config = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
    gdrive_fonti = [f for f in config.get("fonti", [])
                    if f.get("tipo") == "gdrive" and f.get("remote")]

    if not gdrive_fonti:
        print("[INFO] Nessuna fonte gdrive con campo 'remote' in sources.json.")
        print('       Esempio: { "id": "gdrive-gmail", "tipo": "gdrive", "remote": "drive-personale" }')
        return

    print("\n=== Universal Index — Scanner Google Drive ===\n")

    for fonte in gdrive_fonti:
        fonte_id = fonte["id"]
        if args.fonte and args.fonte != fonte_id:
            continue
        print(f"📂 {fonte_id} ({fonte['remote']})")
        voci = scan_gdrive_remote(fonte["remote"], fonte_id,
                                  args.max_depth, args.timeout)
        update_data_json(fonte_id, voci, args.dry_run)

    print("\n=== Completato ===")

if __name__ == "__main__":
    main()
