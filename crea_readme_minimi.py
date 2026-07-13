"""
crea_readme_minimi.py — Universal Index: README minimi in profondità
=====================================================================
Percorre in profondità il percorso radice di ogni etichetta locale di
sources.json e crea un README.md minimo (stato: da-revisionare) in ogni
cartella-UNITÀ che ne è priva. Filosofia: "si indicizza prima, si ordina poi".

COME DECIDE (protocollo dell'archivio: radice → contenitori → cartelle-anno → unità):

  CONTENITORE (si attraversa, MAI un README qui):
    - le cartelle di primo livello sotto la radice che hanno sottocartelle
      e non hanno prefisso NNN_ (es. Area_Personale, Area_Istruzione, Musica)
    - le cartelle-anno a qualsiasi livello: "2019", "2020-2021", "AA 2020-2021"

  UNITÀ (riceve il README, non si scende oltre):
    - cartelle con prefisso numerico NNN_ (es. 010_Progetto)
    - qualsiasi altra cartella non-contenitore (es. Matrimonio-Rossi, un album foto)
    - cartelle foglia (senza sottocartelle)

  RIPARAZIONE: se un CONTENITORE ha un README creato per errore da una versione
  precedente di questo script (riconosciuto dal marker "README minimo"), il file
  viene RIMOSSO e la discesa continua — altrimenti lo scanner si fermerebbe lì
  nascondendo tutto il contenuto. I README scritti a mano non vengono mai toccati.

  Cartelle con README "vero" → sottoalbero saltato (già indicizzate).
  Prefisso _ e cartelle di sistema → ignorate.

USO:
    python crea_readme_minimi.py --dry-run           # anteprima, non scrive nulla
    python crea_readme_minimi.py                     # tutte le etichette locali collegate
    python crea_readme_minimi.py --etichetta x8-blu  # una sola etichetta

Dopo: 🔄 Scansiona dall'interfaccia (o python scan_local.py).
Disponibile anche dall'interfaccia: 🧰 Manutenzione → 📄 README minimi.
"""

import argparse, json, re, sys
from datetime import date
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
SOURCES_JSON = SCRIPT_DIR / "sources.json"

MARKER_MINIMO = "README minimo"          # riconosce i README creati da questo script
MAX_DEPTH_DEFAULT = 6

CARTELLE_IGNORATE = {
    "$RECYCLE.BIN", "System Volume Information", ".Trash-1000",
    ".Spotlight-V100", ".fseventsd", "found.000", "RECYCLER",
}

DEFAULT_PER_COLORE = {
    "rossa":  {"tipo": "progetto", "contesto": "personale",     "tags": ["da-classificare"]},
    "gialla": {"tipo": "archivio", "contesto": "personale",     "tags": ["fotografie"]},
    "blu":    {"tipo": "progetto", "contesto": "professionale", "tags": ["progetto-chiuso"]},
    "verde":  {"tipo": "archivio", "contesto": "personale",     "tags": ["risorse"]},
}
DEFAULT_GENERICO = {"tipo": "archivio", "contesto": "personale", "tags": ["da-classificare"]}

RE_ANNO        = re.compile(r'(19[5-9]\d|20[0-4]\d)')     # un anno nel nome → campo anno
RE_PREFISSO    = re.compile(r'^\d{2,3}[_\- ]')            # 010_, 27-, 001 ...
# Cartella-anno "pura": 2019 | 2020-2021 | AA 2020-2021 | aa 2020 2021
RE_CONTENITORE_ANNO = re.compile(
    r'^(aa[ _\-]*)?(19|20)\d{2}([ _\-]+(19|20)\d{2})?$', re.IGNORECASE)


def deduci_anno(nome: str) -> str:
    m = RE_ANNO.search(nome)
    return m.group(1) if m else str(date.today().year)


def titolo_da_nome(nome: str) -> str:
    t = RE_PREFISSO.sub('', nome)
    t = re.sub(r'[_\-]+', ' ', t).strip()
    return t or nome


def readme_minimo(nome_cartella: str, colore: str, etichetta_label: str) -> str:
    d = DEFAULT_PER_COLORE.get(colore, DEFAULT_GENERICO)
    titolo = titolo_da_nome(nome_cartella).replace('"', "'")
    tags = ", ".join(d["tags"])
    return f"""---
titolo: "{titolo}"
anno: {deduci_anno(nome_cartella)}
tipo: {d['tipo']}
stato: da-revisionare
tags: [{tags}]
output: null
disciplina: altro
contesto: {d['contesto']}
lingua: it
---

Cartella indicizzata automaticamente da {etichetta_label} (README minimo).
Da aprire e decidere: classificare, lavorare o archiviare.
"""


def _readme_di(cartella: Path) -> Path | None:
    for nome in ("README.md", "readme.md"):
        p = cartella / nome
        if p.exists():
            return p
    return None


def _e_minimo(readme_path: Path) -> bool:
    try:
        return MARKER_MINIMO in readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _sottocartelle_valide(cartella: Path) -> list[Path]:
    try:
        return sorted(
            f for f in cartella.iterdir()
            if f.is_dir() and not f.name.startswith(('_', '.'))
            and f.name not in CARTELLE_IGNORATE
        )
    except OSError:
        return []


def processa_etichetta(et: dict, dry_run: bool,
                       max_depth: int = MAX_DEPTH_DEFAULT) -> dict:
    """
    Ritorna: {"creati": [percorsi], "riparati": [percorsi], "gia_indicizzate": n,
              "nota": str|None}
    """
    ris = {"creati": [], "riparati": [], "gia_indicizzate": 0, "nota": None}
    root = Path(et.get("percorso_radice") or "")
    label, colore = et.get("label", et.get("id", "?")), et.get("colore", "altro")

    if not et.get("percorso_radice"):
        ris["nota"] = "nessun percorso radice"
        return ris
    if not root.exists():
        ris["nota"] = f"{root} non raggiungibile (supporto scollegato?)"
        return ris

    def visita(cartella: Path, depth: int):
        sotto = _sottocartelle_valide(cartella)
        readme = _readme_di(cartella)

        # È un contenitore? (primo livello con sottocartelle e senza prefisso,
        # oppure cartella-anno con sottocartelle) — entro il limite di profondità
        contenitore = bool(sotto) and depth < max_depth and (
            (depth == 1 and not RE_PREFISSO.match(cartella.name))
            if depth == 1 else RE_CONTENITORE_ANNO.match(cartella.name)
        )

        if contenitore:
            if readme:
                if _e_minimo(readme):
                    # README spurio messo su un contenitore: va tolto,
                    # altrimenti lo scanner non scende e nasconde tutto
                    if not dry_run:
                        readme.unlink()
                    ris["riparati"].append(str(readme))
                else:
                    # README scritto a mano: la cartella è indicizzata così,
                    # rispettiamo la scelta e non scendiamo
                    ris["gia_indicizzate"] += 1
                    return
            for figlio in sotto:
                visita(figlio, depth + 1)
            return

        # Unità
        if readme:
            ris["gia_indicizzate"] += 1
            return
        target = cartella / "README.md"
        if not dry_run:
            target.write_text(readme_minimo(cartella.name, colore, label),
                              encoding="utf-8")
        ris["creati"].append(str(target))

    for figlio in _sottocartelle_valide(root):
        visita(figlio, 1)
    return ris


def esegui(config: dict, dry_run: bool, solo_etichetta: str | None = None,
           max_depth: int = MAX_DEPTH_DEFAULT) -> list[dict]:
    """Processa tutte le etichette locali. Ritorna lista di risultati per etichetta."""
    out = []
    for fonte in config.get("fonti", []):
        if fonte.get("tipo") != "locale":
            continue
        for et in fonte.get("etichette", []):
            if solo_etichetta and et.get("id") != solo_etichetta:
                continue
            ris = processa_etichetta(et, dry_run, max_depth)
            ris["fonte"] = fonte.get("label", fonte.get("id"))
            ris["etichetta"] = et.get("label", et.get("id"))
            ris["etichetta_id"] = et.get("id")
            ris["percorso_radice"] = et.get("percorso_radice")
            out.append(ris)
    return out


def main():
    ap = argparse.ArgumentParser(description="README.md minimi in profondità")
    ap.add_argument("--dry-run", action="store_true", help="anteprima senza scrivere")
    ap.add_argument("--etichetta", help="limita a una sola etichetta (id, es. x8-blu)")
    ap.add_argument("--max-depth", type=int, default=MAX_DEPTH_DEFAULT)
    args = ap.parse_args()

    if not SOURCES_JSON.exists():
        sys.exit(f"sources.json non trovato in {SCRIPT_DIR}")
    config = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))

    risultati = esegui(config, args.dry_run, args.etichetta, args.max_depth)
    tot_creati = tot_riparati = 0
    for r in risultati:
        print(f"\n── {r['fonte']} → {r['etichetta']} ({r['percorso_radice']})")
        if r["nota"]:
            print(f"  ⚠  {r['nota']}")
            continue
        for p in r["riparati"]:
            print(f"  🔧 {'rimuoverei' if args.dry_run else 'rimosso'} README spurio da contenitore: {p}")
        for p in r["creati"]:
            print(f"  {'[dry-run] creerei' if args.dry_run else '✓ creato'}: {p}")
        print(f"  Totale: {len(r['creati'])} README, {len(r['riparati'])} riparazioni, "
              f"{r['gia_indicizzate']} già indicizzate")
        tot_creati += len(r["creati"]); tot_riparati += len(r["riparati"])

    print(f"\n{'='*50}\nREADME {'da creare' if args.dry_run else 'creati'}: {tot_creati} — "
          f"riparazioni: {tot_riparati}")
    if not args.dry_run and (tot_creati or tot_riparati):
        print("Ora: 🔄 Scansiona dall'interfaccia (o: python scan_local.py)")


if __name__ == "__main__":
    main()
