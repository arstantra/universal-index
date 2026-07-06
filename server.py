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

def load_db() -> dict | None:
    if not DATA_JSON.exists():
        return None
    return json.loads(DATA_JSON.read_text(encoding="utf-8"))

def save_db(db: dict):
    db.setdefault("meta", {})["lastUpdated"] = date.today().isoformat()
    DATA_JSON.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

def load_sources() -> dict:
    if SOURCES_JSON.exists():
        return json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
    return {"fonti": []}

def save_sources(config: dict):
    SOURCES_JSON.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

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
    """Registra un nuovo supporto in data.json E sources.json."""
    data  = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip()
    tipo  = (data.get("tipo") or "locale").strip()
    note  = (data.get("note") or "").strip()

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

    nuova_fonte = {
        "id": fonte_id, "label": label, "tipo": tipo,
        "stato": "da-collegare", "note": note, "etichette": []
    }
    db.setdefault("fonti", []).append(nuova_fonte)
    save_db(db)

    config = load_sources()
    if not any(f.get("id") == fonte_id for f in config.get("fonti", [])):
        config.setdefault("fonti", []).append(
            {"id": fonte_id, "label": label, "tipo": tipo, "etichette": []})
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

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("╔══════════════════════════════════════════╗")
    print("║   Universal Index — Server Locale        ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║   http://localhost:{PORT}                   ║")
    print("║   Ctrl+C per fermare                     ║")
    print("╚══════════════════════════════════════════╝")
    print()
    app.run(host="127.0.0.1", port=PORT, debug=False)
