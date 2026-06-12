"""
generate_readme.py — Universal Index: Generatore automatico README.md
=====================================================================
Usa l'API Anthropic Claude per generare README.md con frontmatter YAML
in ogni cartella di progetto (prefisso NNN_) che non ne ha già uno valido.

USO:
    python generate_readme.py --root "C:/Users/unoav/OneDrive/Etichetta_Rossa"
    python generate_readme.py --root "C:/..." --aggiorna    # rigenera i cambiati
    python generate_readme.py --root "C:/..." --dry-run     # mostra cosa farebbe
    python generate_readme.py --root "C:/..." --cartella "Area_Istruzione/AA 2009/010_xxx"

PREREQUISITI:
    pip install anthropic pyyaml
    Crea un file .env nella stessa cartella con: ANTHROPIC_API_KEY=sk-ant-...
"""

import os, re, json, yaml, argparse, sys, hashlib
from pathlib import Path

# ─── CONFIG ────────────────────────────────────────────────────────────────────

SCRIPT_DIR    = Path(__file__).parent
STATE_FILE    = SCRIPT_DIR / ".readme_gen_state.json"
RE_UNDERSCORE = re.compile(r'^_')
RE_NUM_PREFIX = re.compile(r'^\d{3}_')

TEXT_EXTENSIONS = {'.txt', '.md', '.csv', '.html', '.htm'}
MAX_TEXT_CHARS  = 400   # per file: mostra solo un'anteprima

# ─── SYSTEM PROMPT ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sei un assistente che analizza cartelle di archivio personale e genera file README.md
con frontmatter YAML strutturato.

Schema frontmatter obbligatorio (rispetta esattamente questi campi e valori):
---
titolo: "Titolo leggibile ricavato dal contenuto"
anno: YYYY
tipo: progetto | relazione | convegno | appunti | portfolio
stato: completo | in-lavorazione | da-revisionare | archivio-morto
tags: [tag1, tag2, tag3]
output: output/file.pdf
disciplina: architettura | storia | urbanistica | ingegneria | altro
contesto: università | scuola | professionale | personale
lingua: it | en | it-en
---

Regole:
- Produci SOLO il contenuto del file README.md, iniziando con ---
- YAML valido, delimitato da ---
- Se non riesci a ricavare l'anno con certezza: anno: null
- Se c'è un file finale in output/ → stato: completo
- Se la cartella è caotica senza output → stato: da-revisionare o archivio-morto
- I tag in minuscolo, senza spazi (usa trattino: es. piani-regolatori)
- Due frasi descrittive in italiano dopo il frontmatter
- NON inventare informazioni: usa null o lascia vuoto"""

# ─── YAML CHECK ────────────────────────────────────────────────────────────────

def has_valid_readme(cartella: Path) -> bool:
    readme = cartella / 'README.md'
    if not readme.exists():
        return False
    try:
        text = readme.read_text(encoding='utf-8', errors='replace')
    except:
        return False
    if not text.startswith('---'):
        return False
    parts = text.split('---', 2)
    if len(parts) < 3:
        return False
    try:
        meta = yaml.safe_load(parts[1])
        return bool(meta and meta.get('titolo'))
    except:
        return False

# ─── HASH CARTELLA ─────────────────────────────────────────────────────────────

def hash_cartella(cartella: Path) -> str:
    """Hash basato su nomi file e date di modifica (esclude README.md)."""
    items = []
    for root, dirs, files in os.walk(cartella):
        dirs[:] = sorted(d for d in dirs if not RE_UNDERSCORE.match(d))
        for f in sorted(files):
            if f == 'README.md':
                continue
            fp = Path(root) / f
            try:
                items.append(f"{fp.relative_to(cartella)}:{fp.stat().st_mtime:.0f}")
            except:
                pass
    return hashlib.md5('\n'.join(items).encode()).hexdigest()

# ─── LEGGI STRUTTURA ───────────────────────────────────────────────────────────

def leggi_struttura(cartella: Path) -> str:
    """Costruisce una descrizione testuale della struttura della cartella."""
    lines = [f"Nome cartella: {cartella.name}", ""]

    for root, dirs, files in os.walk(cartella):
        dirs[:] = sorted(d for d in dirs if not RE_UNDERSCORE.match(d))
        level  = len(Path(root).relative_to(cartella).parts)
        indent = '  ' * level

        if level > 0:
            lines.append(f"{indent}📁 {Path(root).name}/")

        for f in sorted(files):
            if f == 'README.md':
                continue
            fpath = Path(root) / f
            ext   = fpath.suffix.lower()
            try:
                size_kb = fpath.stat().st_size // 1024
            except:
                size_kb = 0
            lines.append(f"{'  ' * (level+1)}📄 {f} ({size_kb}KB)")

            # Anteprima per file testo piccoli
            if ext in TEXT_EXTENSIONS:
                try:
                    snippet = fpath.read_text(encoding='utf-8', errors='replace')[:MAX_TEXT_CHARS].strip()
                    if snippet:
                        lines.append(f"{'  ' * (level+2)}→ {snippet[:200]}")
                except:
                    pass

    return '\n'.join(lines)

# ─── CHIAMA API ────────────────────────────────────────────────────────────────

def genera_readme(cartella: Path, client) -> str:
    struttura = leggi_struttura(cartella)

    user_msg = f"""Analizza questa cartella e genera il README.md con frontmatter YAML.

{struttura}

Produci SOLO il contenuto del README.md, iniziando con ---"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}]
    )

    return message.content[0].text.strip()

# ─── TROVA CARTELLE TARGET ─────────────────────────────────────────────────────

def trova_cartelle_target(root: Path) -> list[Path]:
    """
    Trova tutte le cartelle con prefisso NNN_ a qualsiasi profondità.
    Non scende dentro una cartella NNN_ (il README è lì, non nei figli).
    """
    target = []

    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not RE_UNDERSCORE.match(d))
        cartella = Path(dirpath)

        if RE_NUM_PREFIX.match(cartella.name):
            target.append(cartella)
            dirnames.clear()  # non scendere dentro

    return sorted(target)

# ─── STATO (per --aggiorna) ────────────────────────────────────────────────────

def carica_stato() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except:
            return {}
    return {}

def salva_stato(stato: dict):
    STATE_FILE.write_text(
        json.dumps(stato, ensure_ascii=False, indent=2), encoding='utf-8'
    )

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Genera README.md con Claude API')
    parser.add_argument('--root',     required=True, help='Cartella radice da processare')
    parser.add_argument('--aggiorna', action='store_true',
                        help='Rigenera README nelle cartelle il cui contenuto è cambiato')
    parser.add_argument('--forza',    action='store_true',
                        help='Rigenera TUTTI i README, anche quelli già validi')
    parser.add_argument('--dry-run',  action='store_true',
                        help='Mostra cosa farebbe senza chiamare API')
    parser.add_argument('--cartella', help='Processa solo questo percorso relativo alla root')
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"[ERRORE] Cartella non trovata: {root}")
        sys.exit(1)

    # ── Carica API key ──────────────────────────────────────────────────────────
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        env_file = SCRIPT_DIR / '.env'
        if env_file.exists():
            for enc in ('utf-8-sig', 'utf-16', 'utf-8', 'latin-1'):
                try:
                    text = env_file.read_text(encoding=enc)
                    # rimuove spazi tra caratteri (artefatto UTF-16)
                    if ' A N T H R O P I C' in text:
                        text = text.replace(' ', '')
                    for line in text.splitlines():
                        line = line.strip()
                        if line.startswith('ANTHROPIC_API_KEY='):
                            api_key = line.split('=', 1)[1].strip().strip('"\'')
                            break
                    if api_key:
                        break
                except Exception:
                    continue

    if not api_key and not args.dry_run:
        print("[ERRORE] ANTHROPIC_API_KEY non trovata.")
        print("  Crea un file .env nella cartella dello script con:")
        print("  ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    # ── Import anthropic ────────────────────────────────────────────────────────
    client = None
    if not args.dry_run:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            print("[ERRORE] Installa il pacchetto: pip install anthropic")
            sys.exit(1)

    # ── Trova cartelle ──────────────────────────────────────────────────────────
    stato    = carica_stato()
    cartelle = trova_cartelle_target(root)

    if args.cartella:
        cartelle = [root / args.cartella]

    # ── Filtra cosa processare ──────────────────────────────────────────────────
    da_processare = []
    for c in cartelle:
        chiave = str(c.relative_to(root))
        h      = hash_cartella(c)

        if args.forza:
            da_processare.append((c, h, "forzato"))
        elif not has_valid_readme(c):
            da_processare.append((c, h, "nuovo"))
        elif args.aggiorna and stato.get(chiave) != h:
            da_processare.append((c, h, "cambiato"))

    print(f"\n📁 Cartelle NNN_ trovate:  {len(cartelle)}")
    print(f"📝 Da processare:          {len(da_processare)}")
    print(f"✅ Già con README valido:  {len(cartelle) - len(da_processare)}")

    if args.dry_run:
        if da_processare:
            print("\n[DRY RUN] Cartelle che verrebbero processate:")
            for c, _, motivo in da_processare:
                print(f"  [{motivo}] {c.relative_to(root)}")
        return

    if not da_processare:
        print("\nNiente da fare.")
        return

    # ── Processa ────────────────────────────────────────────────────────────────
    ok = errori = 0
    for i, (cartella, h, motivo) in enumerate(da_processare, 1):
        rel = cartella.relative_to(root)
        print(f"\n[{i}/{len(da_processare)}] {rel}  ({motivo})")

        try:
            contenuto = genera_readme(cartella, client)

            if not contenuto.startswith('---'):
                contenuto = '---\n' + contenuto

            readme_path = cartella / 'README.md'
            readme_path.write_text(contenuto, encoding='utf-8')

            stato[str(rel)] = h
            salva_stato(stato)

            print(f"  ✅ README.md scritto")
            ok += 1

        except Exception as e:
            print(f"  ❌ Errore: {e}")
            errori += 1

    print(f"\n{'─'*50}")
    print(f"Completati: {ok} | Errori: {errori}")
    print(f"\nProssimo passo:")
    print(f"  python scan_local.py")
    print(f"  git add -A && git commit -m 'aggiorna indice' && git push")

if __name__ == '__main__':
    try:
        import yaml
    except ImportError:
        os.system(f"{sys.executable} -m pip install pyyaml")
        import yaml
    main()
