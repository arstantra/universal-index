"""
server.py — Universal Index: Server Locale
===========================================
Avvia un server HTTP sulla porta 5000 che:
  - serve index.html, data.json e tutti i file statici
  - POST /api/update-stato  → cambia lo stato di una voce e aggiorna
                              sia data.json che il README.md corrispondente
  - POST /api/update-focus  → gestisce il pannello Progetto attivo / Coda
  - POST /api/add-fonte     → registra un nuovo supporto (data.json + sources.json)
  - POST /api/add-etichetta → aggiunge un'etichetta a un supporto esistente
  - POST /api/sync-drive    → carica data.json su Google Drive (versione pubblica)

USO:
    python server.py

Poi apri nel browser: http://localhost:5000

NOTA: questo server è pensato per uso LOCALE sul proprio computer.
      Non va esposto su internet.

REQUISITI:
    pip install flask
"""

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

# ─── DIPENDENZE ────────────────────────────────────────────────────────────────

try:
    from flask import Flask, jsonify, request, send_from_directory
except ImportError:
    print("\n📦 Installazione dipendenze...")
    os.system(f'"{sys.executable}" -m pip install flask --quiet')
    from flask import Flask, jsonify, request, send_from_directory

# ─── CONFIG ────────────────────────────────────────────────────────────────────

BASE         = Path(__file__).parent
DATA_JSON    = BASE / "data.json"
SOURCES_JSON = BASE / "sources.json"
PORT         = 5000

STATI_VALIDI  = {"completo", "in-lavorazione", "da-revisionare", "archivio-morto"}
TIPI_SUPPORTO = {"locale", "gdrive", "dropbox", "altro"}
MAX_CODA      = 5

app = Flask(__name__, static_folder=str(BASE))

# ─── HELPERS I/O ───────────────────────────────────────────────────────────────

def _read_json_robusto(path: Path) -> dict:
    """
    Legge un JSON tollerando i byte nulli (\\x00) e lo spazio bianco finale
    lasciati da scritture interrotte o dalla sincronizzazione di OneDrive.
    Senza questa pulizia, un solo byte spurio in fondo al file mandava in
    errore l'intera dashboard e tutte le API.
    """
    raw = path.read_bytes()
    if b"\x00" in raw:
        raw = raw.replace(b"\x00", b"")
    return json.loads(raw.decode("utf-8").strip())

def _write_json_atomico(path: Path, data: dict):
    """
    Scrive su un file temporaneo e poi lo rinomina sul file finale (os.replace,
    operazione atomica): il file di destinazione non resta mai a metà, quindi
    non si ripresenta la corruzione con i byte nulli.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def load_db() -> dict | None:
    if not DATA_JSON.exists():
        return None
    return _read_json_robusto(DATA_JSON)

def save_db(db: dict):
    db.setdefault("meta", {})["lastUpdated"] = date.today().isoformat()
    _write_json_atomico(DATA_JSON, db)

def load_sources() -> dict:
    if SOURCES_JSON.exists():
        return _read_json_robusto(SOURCES_JSON)
    return {"fonti": []}

def save_sources(config: dict):
    _write_json_atomico(SOURCES_JSON, config)

# ─── FILE STATICI ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(BASE), "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    # data.json servito sempre fresco (niente cache del browser)
    response = send_from_directory(str(BASE), filename)
    if filename == "data.json":
        response.headers["Cache-Control"] = "no-store"
    return response

# ─── API: AGGIORNA STATO ───────────────────────────────────────────────────────

@app.route("/api/update-stato", methods=["POST"])
def update_stato():
    data      = request.get_json(silent=True) or {}
    voce_id   = (data.get("voce_id") or "").strip()
    new_stato = (data.get("stato") or "").strip()

    if not voce_id:
        return jsonify({"ok": False, "error": "voce_id mancante"}), 400
    if new_stato not in STATI_VALIDI:
        return jsonify({"ok": False, "error": f"Stato non valido: '{new_stato}'"}), 400

    db = load_db()
    if db is None:
        return jsonify({"ok": False, "error": "data.json non trovato"}), 500

    voce = next((v for v in db.get("voci", []) if v.get("id") == voce_id), None)
    if voce is None:
        return jsonify({"ok": False, "error": f"Voce '{voce_id}' non trovata"}), 404

    # La fonte deve essere collegata (regola UX: scollegata = read-only)
    fonte = next((f for f in db.get("fonti", []) if f.get("id") == voce.get("fonte")), None)
    if fonte and fonte.get("stato") in ("da-collegare", "da-svuotare"):
        return jsonify({"ok": False,
                        "error": f"Fonte '{voce.get('fonte')}' non collegata — impossibile modificare"}), 403

    vecchio_stato = voce.get("stato")
    voce["stato"] = new_stato
    save_db(db)

    # Aggiorna anche il README.md sul disco (fonte di verità del prossimo scan)
    readme_path    = _find_readme(voce)
    readme_updated = False
    readme_warn    = None

    if readme_path and readme_path.exists():
        try:
            _update_readme_stato(readme_path, new_stato)
            readme_updated = True
        except Exception as e:
            readme_warn = f"README trovato ma non aggiornato: {e}"
    else:
        readme_warn = ("README.md non trovato sul disco — solo data.json aggiornato. "
                       "ATTENZIONE: il prossimo scan ripristinerà il vecchio stato.")

    response = {
        "ok":             True,
        "readme_updated": readme_updated,
        "vecchio_stato":  vecchio_stato,
        "nuovo_stato":    new_stato,
    }
    if readme_warn:
        response["warn"] = readme_warn
    if readme_path:
        response["readme_path"] = str(readme_path)

    return jsonify(response)

# ─── API: AGGIORNA FOCUS ──────────────────────────────────────────────────────

@app.route("/api/update-focus", methods=["POST"])
def update_focus():
    data         = request.get_json(silent=True) or {}
    voce_id      = (data.get("voce_id") or "").strip()
    new_focus    = data.get("focus")          # "attivo" | "coda" | None
    focus_azione = data.get("focus_azione")   # testo libero, solo per attivo

    if not voce_id:
        return jsonify({"ok": False, "error": "voce_id mancante"}), 400
    if new_focus not in (None, "attivo", "coda"):
        return jsonify({"ok": False, "error": f"Focus non valido: '{new_focus}'"}), 400

    db = load_db()
    if db is None:
        return jsonify({"ok": False, "error": "data.json non trovato"}), 500

    voce = next((v for v in db.get("voci", []) if v.get("id") == voce_id), None)
    if voce is None:
        return jsonify({"ok": False, "error": f"Voce '{voce_id}' non trovata"}), 404

    vecchio_focus = voce.get("focus")

    if new_focus == "attivo":
        old_attivo = next(
            (v for v in db.get("voci", []) if v.get("focus") == "attivo" and v.get("id") != voce_id),
            None
        )
        coda_count = sum(1 for v in db.get("voci", [])
                         if v.get("focus") == "coda" and v.get("id") != voce_id)
        # Se l'attivo precedente deve scendere in coda, verifica che ci sia spazio
        if old_attivo and vecchio_focus != "coda" and coda_count >= MAX_CODA:
            return jsonify({
                "ok": False,
                "error": f"Coda piena ({MAX_CODA}/{MAX_CODA}) — rimuovi un progetto prima"
            }), 400
        if old_attivo:
            old_attivo["focus"] = "coda"
            old_attivo.pop("focus_azione", None)
        voce["focus"] = "attivo"
        if focus_azione is not None:
            voce["focus_azione"] = focus_azione

    elif new_focus == "coda":
        coda_count = sum(1 for v in db.get("voci", [])
                         if v.get("focus") == "coda" and v.get("id") != voce_id)
        if coda_count >= MAX_CODA:
            return jsonify({
                "ok": False,
                "error": f"Coda piena ({MAX_CODA}/{MAX_CODA}) — rimuovi un progetto prima"
            }), 400
        voce["focus"] = "coda"
        voce.pop("focus_azione", None)

    else:  # None — rimuovi dal focus
        voce["focus"] = None
        voce.pop("focus_azione", None)

    save_db(db)
    return jsonify({"ok": True, "vecchio_focus": vecchio_focus, "nuovo_focus": new_focus})

# ─── API: AGGIUNGI FONTE ──────────────────────────────────────────────────────

@app.route("/api/add-fonte", methods=["POST"])
def add_fonte():
    """Registra un nuovo supporto in data.json E sources.json.

    Accetta opzionalmente 'etichetta': {label, colore, descrizione, percorso_radice}
    così il supporto nasce già completo di etichetta iniziale (un solo passaggio dall'UI).
    """
    data  = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip()
    tipo  = (data.get("tipo") or "locale").strip()
    note  = (data.get("note") or "").strip()
    et_in = data.get("etichetta") or None

    if not label:
        return jsonify({"ok": False, "error": "Nome del supporto obbligatorio"}), 400
    if tipo not in TIPI_SUPPORTO:
        return jsonify({"ok": False, "error": f"Tipo non valido: '{tipo}'"}), 400

    fonte_id = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')
    if not fonte_id:
        return jsonify({"ok": False, "error": "Nome non valido"}), 400

    db = load_db()
    if db is None:
        return jsonify({"ok": False, "error": "data.json non trovato"}), 500

    if any(f.get("id") == fonte_id for f in db.get("fonti", [])):
        return jsonify({"ok": False, "error": f"Esiste già un supporto con ID '{fonte_id}'"}), 409

    # Etichetta iniziale (opzionale)
    etichette_db, etichette_cfg, stato_fonte = [], [], "da-collegare"
    if isinstance(et_in, dict) and (et_in.get("label") or "").strip():
        et_label  = et_in["label"].strip()
        colore    = (et_in.get("colore") or "altro").strip()
        percorso  = (et_in.get("percorso_radice") or "").strip()
        desc      = (et_in.get("descrizione") or "").strip()
        et_id     = f"{fonte_id}-{re.sub(r'[^a-z0-9]+', '-', et_label.lower()).strip('-') or colore}"
        etichette_db.append({
            "id": et_id, "label": et_label, "colore": colore,
            "descrizione": desc,
            "percorso_radice": percorso or None,
            "stato": "attivo" if percorso else "da-collegare",
        })
        etichette_cfg.append({
            "id": et_id, "label": et_label, "colore": colore,
            "percorso_radice": percorso or None,
        })
        if percorso:
            stato_fonte = "parziale"

    nuova_fonte = {
        "id": fonte_id, "label": label, "tipo": tipo,
        "stato": stato_fonte, "note": note, "etichette": etichette_db
    }
    db.setdefault("fonti", []).append(nuova_fonte)
    save_db(db)

    config = load_sources()
    if not any(f.get("id") == fonte_id for f in config.get("fonti", [])):
        config.setdefault("fonti", []).append(
            {"id": fonte_id, "label": label, "tipo": tipo, "etichette": etichette_cfg})
        save_sources(config)

    return jsonify({"ok": True, "fonte": nuova_fonte})

# ─── API: AGGIUNGI ETICHETTA ──────────────────────────────────────────────────

@app.route("/api/add-etichetta", methods=["POST"])
def add_etichetta():
    """Aggiunge un'etichetta a un supporto esistente (data.json + sources.json)."""
    data     = request.get_json(silent=True) or {}
    fonte_id = (data.get("fonte_id") or "").strip()
    label    = (data.get("label") or "").strip()
    colore   = (data.get("colore") or "altro").strip()
    percorso = (data.get("percorso_radice") or "").strip()
    desc     = (data.get("descrizione") or "").strip()

    if not fonte_id or not label:
        return jsonify({"ok": False, "error": "fonte_id e label obbligatori"}), 400

    db = load_db()
    if db is None:
        return jsonify({"ok": False, "error": "data.json non trovato"}), 500

    fonte = next((f for f in db.get("fonti", []) if f.get("id") == fonte_id), None)
    if fonte is None:
        return jsonify({"ok": False, "error": f"Supporto '{fonte_id}' non trovato"}), 404

    base_id = f"{fonte_id}-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-') or colore}"
    et_id   = base_id
    esistenti = {e.get("id") for e in fonte.get("etichette", [])}
    n = 2
    while et_id in esistenti:
        et_id = f"{base_id}-{n}"
        n += 1

    nuova_et = {
        "id": et_id, "label": label, "colore": colore,
        "descrizione": desc,
        "percorso_radice": percorso or None,
        "stato": "attivo" if percorso else "da-collegare",
    }
    fonte.setdefault("etichette", []).append(nuova_et)
    if percorso and fonte.get("stato") == "da-collegare":
        fonte["stato"] = "parziale"
    save_db(db)

    config = load_sources()
    cfg_fonte = next((f for f in config.get("fonti", []) if f.get("id") == fonte_id), None)
    if cfg_fonte is None:
        cfg_fonte = {"id": fonte_id, "label": fonte.get("label", fonte_id),
                     "tipo": fonte.get("tipo", "locale"), "etichette": []}
        config.setdefault("fonti", []).append(cfg_fonte)
    cfg_fonte.setdefault("etichette", []).append({
        "id": et_id, "label": label, "colore": colore,
        "percorso_radice": percorso or None,
    })
    save_sources(config)

    return jsonify({"ok": True, "etichetta": nuova_et})

# ─── API: SCANSIONA ───────────────────────────────────────────────────────────

@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Rilancia la scansione locale (tutte le etichette configurate in sources.json)."""
    try:
        from scan_local import scan_percorso, update_data_json as scan_update
    except ImportError as e:
        return jsonify({"ok": False, "error": f"scan_local.py non importabile: {e}"}), 500

    config = load_sources()
    risultati, errori_tot = [], 0

    for fonte in config.get("fonti", []):
        if fonte.get("tipo") == "gdrive":
            continue  # i Drive si scansionano con scan_gdrive.py da terminale
        for et in fonte.get("etichette", []):
            radice = et.get("percorso_radice")
            if not radice or not Path(radice).exists():
                continue
            voci, errori = scan_percorso(Path(radice), fonte["id"], etichetta_id=et.get("id"))
            scan_update(fonte["id"], et.get("id"), voci, dry_run=False)
            errori_tot += len(errori)
            risultati.append({"fonte": fonte["id"], "etichetta": et.get("id"),
                              "voci": len(voci), "errori": errori})

    if not risultati:
        return jsonify({"ok": False, "error": "Nessuna etichetta locale con percorso raggiungibile"}), 400

    return jsonify({"ok": True, "risultati": risultati,
                    "voci_totali": sum(r["voci"] for r in risultati),
                    "errori_totali": errori_tot})

# ─── API: REPORT STRUTTURA (checklist conformità) ────────────────────────────

CARTELLE_STANDARD = ("output", "assets", "_archivio")

@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Chiude il server dall'interfaccia (bottone ⏻): risponde e poi termina il processo."""
    import threading, os
    threading.Timer(0.6, lambda: os._exit(0)).start()
    return jsonify({"ok": True})

@app.route("/api/crea-readme-minimi", methods=["POST"])
def api_crea_readme_minimi():
    """
    Crea README.md minimi in profondità su tutte le etichette locali collegate
    (vedi crea_readme_minimi.py). Con {"dry_run": true} ritorna solo l'anteprima.
    Opzionale: {"etichetta": "x8-blu"} per limitare a una sola etichetta.
    """
    try:
        from crea_readme_minimi import esegui
    except ImportError as e:
        return jsonify({"ok": False, "error": f"crea_readme_minimi.py non importabile: {e}"}), 500

    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run"))
    etichetta = (data.get("etichetta") or "").strip() or None

    risultati = esegui(load_sources(), dry_run, etichetta)
    tot_creati   = sum(len(r["creati"]) for r in risultati)
    tot_riparati = sum(len(r["riparati"]) for r in risultati)
    return jsonify({
        "ok": True, "dry_run": dry_run,
        "creati": tot_creati, "riparati": tot_riparati,
        "dettaglio": [{
            "etichetta": r["etichetta"], "etichetta_id": r["etichetta_id"],
            "creati": len(r["creati"]), "riparati": len(r["riparati"]),
            "gia_indicizzate": r["gia_indicizzate"], "nota": r["nota"],
            "esempi": r["creati"][:8],
        } for r in risultati],
    })

@app.route("/api/struttura-report", methods=["GET"])
def struttura_report():
    """
    Analizza le cartelle NNN_ di ogni etichetta locale collegata e segnala:
      - README mancante o senza frontmatter valido
      - file sciolti nella root (fuori da output/assets/_archivio)
      - sottocartelle standard mancanti
    """
    try:
        from generate_readme import trova_cartelle_target, has_valid_readme
    except ImportError as e:
        return jsonify({"ok": False, "error": f"generate_readme.py non importabile: {e}"}), 500

    config = load_sources()
    report = []

    for fonte in config.get("fonti", []):
        if fonte.get("tipo") == "gdrive":
            continue
        for et in fonte.get("etichette", []):
            # Le etichette "raccolta" (foto, risorse) non seguono la struttura
            # standard output/assets/_archivio: niente controlli di conformità.
            if et.get("natura") == "raccolta":
                continue
            radice = et.get("percorso_radice")
            if not radice or not Path(radice).exists():
                continue
            root = Path(radice)

            for cartella in trova_cartelle_target(root):
                rel = str(cartella.relative_to(root)).replace("\\", "/")

                if not (cartella / "README.md").exists():
                    readme = "mancante"
                elif not has_valid_readme(cartella):
                    readme = "invalido"
                else:
                    readme = "ok"

                try:
                    contenuto = list(cartella.iterdir())
                except OSError:
                    continue
                file_sciolti = sorted(
                    f.name for f in contenuto
                    if f.is_file() and f.name != "README.md" and not f.name.startswith("_")
                )
                dir_presenti = {f.name for f in contenuto if f.is_dir()}
                dir_mancanti = [d for d in CARTELLE_STANDARD if d not in dir_presenti]

                conforme = readme == "ok" and not file_sciolti
                report.append({
                    "fonte": fonte["id"],
                    "etichetta": et.get("id"),
                    "percorso": rel,
                    "nome": cartella.name,
                    "readme": readme,
                    "file_sciolti": file_sciolti,
                    "dir_mancanti": dir_mancanti,
                    "conforme": conforme,
                })

    report.sort(key=lambda r: (r["conforme"], r["percorso"]))
    n_ok = sum(1 for r in report if r["conforme"])
    return jsonify({"ok": True, "totale": len(report), "conformi": n_ok,
                    "da_sistemare": len(report) - n_ok, "cartelle": report})

# ─── API: GENERA README ───────────────────────────────────────────────────────

def _resolve_cartella(etichetta_id: str, percorso: str) -> Path | None:
    """Percorso assoluto di una cartella-progetto a partire da (etichetta, percorso relativo)."""
    if not percorso or ".." in percorso.replace("\\", "/").split("/"):
        return None
    config = load_sources()
    for fonte in config.get("fonti", []):
        for et in fonte.get("etichette", []):
            if et.get("id") == etichetta_id and et.get("percorso_radice"):
                candidate = Path(et["percorso_radice"]) / percorso
                return candidate if candidate.exists() else None
    return None


@app.route("/api/genera-readme", methods=["POST"])
def api_genera_readme():
    """
    Genera (o rigenera) il README.md di una cartella con la Claude API,
    poi ri-scansiona l'etichetta così la voce appare subito nell'indice.
    """
    data      = request.get_json(silent=True) or {}
    etichetta = (data.get("etichetta_id") or "").strip()
    percorso  = (data.get("percorso") or "").strip()

    cartella = _resolve_cartella(etichetta, percorso)
    if cartella is None:
        return jsonify({"ok": False, "error": f"Cartella non trovata: {percorso}"}), 404

    try:
        from generate_readme import genera_per_cartella
    except ImportError as e:
        return jsonify({"ok": False, "error": f"generate_readme.py non importabile: {e}"}), 500

    ok, msg = genera_per_cartella(cartella)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 502

    # Ri-scansiona l'etichetta per aggiornare l'indice
    try:
        from scan_local import scan_percorso, update_data_json as scan_update
        config = load_sources()
        for fonte in config.get("fonti", []):
            for et in fonte.get("etichette", []):
                if et.get("id") == etichetta and et.get("percorso_radice"):
                    voci, _err = scan_percorso(Path(et["percorso_radice"]),
                                               fonte["id"], etichetta_id=etichetta)
                    scan_update(fonte["id"], etichetta, voci, dry_run=False)
    except Exception:
        pass  # il README è comunque scritto; lo scan si può rifare a mano

    return jsonify({"ok": True, "message": msg, "percorso": percorso})

# ─── API: PREPARA STRUTTURA ───────────────────────────────────────────────────

@app.route("/api/prepara-struttura", methods=["POST"])
def prepara_struttura():
    """
    Crea le sottocartelle standard (output/, assets/, _archivio/) in una
    cartella-progetto. NON sposta né tocca alcun file esistente.
    """
    data      = request.get_json(silent=True) or {}
    etichetta = (data.get("etichetta_id") or "").strip()
    percorso  = (data.get("percorso") or "").strip()

    cartella = _resolve_cartella(etichetta, percorso)
    if cartella is None:
        return jsonify({"ok": False, "error": f"Cartella non trovata: {percorso}"}), 404

    create = []
    for nome in CARTELLE_STANDARD:
        sub = cartella / nome
        if not sub.exists():
            sub.mkdir()
            create.append(nome)

    return jsonify({"ok": True, "create": create,
                    "message": ("Create: " + ", ".join(create)) if create
                               else "Struttura già completa"})

# ─── API: SYNC GOOGLE DRIVE ───────────────────────────────────────────────────

@app.route("/api/sync-drive", methods=["POST"])
def sync_drive():
    """Carica data.json su Google Drive → aggiorna la versione pubblica."""
    try:
        from drive_sync import upload_to_drive
    except ImportError as e:
        return jsonify({"ok": False, "error": f"drive_sync.py non trovato: {e}"}), 500

    ok, msg = upload_to_drive(quiet=True)
    return jsonify({"ok": ok, "message" if ok else "error": msg}), (200 if ok else 502)

# ─── HELPERS README ───────────────────────────────────────────────────────────

def _find_readme(voce: dict) -> Path | None:
    """
    Ricostruisce il percorso assoluto del README.md a partire da sources.json.
    Formato sources.json v2: fonti[].etichette[].percorso_radice.
    Prova prima l'etichetta della voce, poi tutte le etichette della fonte
    (copre le voci non ancora classificate).
    """
    config    = load_sources()
    fonte_id  = voce.get("fonte", "")
    et_id     = voce.get("etichetta")
    percorso  = voce.get("percorso", "")
    if not percorso:
        return None

    fonte = next((f for f in config.get("fonti", []) if f.get("id") == fonte_id), None)
    if fonte is None:
        return None

    etichette = fonte.get("etichette", [])
    # Prima l'etichetta della voce, poi le altre
    ordinate = sorted(etichette, key=lambda e: 0 if e.get("id") == et_id else 1)

    for et in ordinate:
        radice = et.get("percorso_radice")
        if not radice:
            continue
        candidate = Path(radice) / percorso / "README.md"
        if candidate.exists():
            return candidate

    return None


def _update_readme_stato(readme_path: Path, new_stato: str) -> None:
    """
    Aggiorna il campo `stato:` nel frontmatter YAML del README.md.
    Sovrascrive SOLO quella riga, preservando tutto il resto.
    """
    text = readme_path.read_text(encoding="utf-8", errors="replace")

    # Limita la sostituzione al blocco frontmatter (fra i primi due ---)
    parts = text.split('---', 2)
    if len(parts) < 3:
        raise ValueError("Frontmatter YAML non trovato")

    updated_fm, n = re.subn(
        r"^(stato:\s*).+$",
        lambda m: m.group(1) + new_stato,
        parts[1],
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        raise ValueError("Campo 'stato:' non trovato nel frontmatter")

    readme_path.write_text(parts[0] + '---' + updated_fm + '---' + parts[2],
                           encoding="utf-8")

# ─── AVVIO: LIBERA LA PORTA DA SERVER PRECEDENTI ─────────────────────────────
# "L'ultimo vince": se un server precedente (anche nascosto, avviato dal .vbs)
# occupa già la porta, viene chiuso — prima gentilmente via /api/shutdown,
# poi, se non risponde (versioni vecchie), terminando il processo.

def _libera_porta(port: int):
    import socket, subprocess, time, urllib.request

    def occupata() -> bool:
        with socket.socket() as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0

    if not occupata():
        return
    print(f"⚠  Porta {port} occupata da un server precedente — lo chiudo...")

    # 1) Via gentile (server recenti hanno /api/shutdown)
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown",
                                   method="POST"), timeout=2)
        time.sleep(1.5)
    except Exception:
        pass
    if not occupata():
        print("✓  Server precedente chiuso.")
        return

    # 2) Versione vecchia senza /api/shutdown: termina il processo sulla porta
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"], text=True, errors="replace")
        pids = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{port}"):
                pid = parts[-1]
                if pid.isdigit() and pid != "0":
                    pids.add(pid)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        time.sleep(1)
    except Exception:
        pass
    print("✓  Server precedente chiuso." if not occupata()
          else "✗  Non sono riuscito a liberare la porta: chiudi il processo Python da Gestione attività.")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, threading, webbrowser

    parser = argparse.ArgumentParser(description="Universal Index — Server Locale")
    parser.add_argument("--no-browser", action="store_true",
                        help="Non aprire il browser all'avvio")
    args = parser.parse_args()

    _libera_porta(PORT)

    print()
    print("╔══════════════════════════════════════════╗")
    print("║   Universal Index — Server Locale        ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║   http://localhost:{PORT}                   ║")
    print("║   Ctrl+C per fermare                     ║")
    print("╚══════════════════════════════════════════╝")
    print()

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    app.run(host="127.0.0.1", port=PORT, debug=False)
