"""
server.py — Universal Index: Server Locale
===========================================
Avvia un server HTTP sulla porta 5000 che:
  - serve index.html, data.json e tutti i file statici
  - espone POST /api/update-stato per cambiare lo stato di una voce
    e aggiornare in automatico sia data.json che il README.md corrispondente

USO:
    python server.py

Poi apri nel browser: http://localhost:5000

NOTA: questo server è pensato per uso LOCALE sul proprio computer.
      Non va esposto su internet.

REQUISITI:
    pip install flask flask-cors
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
    from flask_cors import CORS
except ImportError:
    print("\n📦 Installazione dipendenze...")
    os.system(f'"{sys.executable}" -m pip install flask flask-cors --quiet')
    from flask import Flask, jsonify, request, send_from_directory
    from flask_cors import CORS

# ─── CONFIG ────────────────────────────────────────────────────────────────────

BASE         = Path(__file__).parent
DATA_JSON    = BASE / "data.json"
SOURCES_JSON = BASE / "sources.json"
PORT         = 5000

STATI_VALIDI = {"completo", "in-lavorazione", "da-revisionare", "archivio-morto"}

app = Flask(__name__, static_folder=str(BASE))
CORS(app, resources={r"/api/*": {"origins": ["http://localhost:*", "http://127.0.0.1:*"]}})

# ─── FILE STATICI ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(BASE), "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(str(BASE), filename)

# ─── API: AGGIORNA STATO ───────────────────────────────────────────────────────

@app.route("/api/update-stato", methods=["POST"])
def update_stato():
    data      = request.get_json(silent=True) or {}
    voce_id   = data.get("voce_id", "").strip()
    new_stato  = data.get("stato", "").strip()

    # Validazione input
    if not voce_id:
        return jsonify({"ok": False, "error": "voce_id mancante"}), 400
    if new_stato not in STATI_VALIDI:
        return jsonify({"ok": False, "error": f"Stato non valido: '{new_stato}'"}), 400

    # Carica data.json
    if not DATA_JSON.exists():
        return jsonify({"ok": False, "error": "data.json non trovato"}), 500
    db = json.loads(DATA_JSON.read_text(encoding="utf-8"))

    # Trova la voce
    voce = next((v for v in db.get("voci", []) if v.get("id") == voce_id), None)
    if voce is None:
        return jsonify({"ok": False, "error": f"Voce '{voce_id}' non trovata"}), 404

    # Controlla che la fonte sia collegata
    fonte_id = voce.get("fonte", "")
    fonte    = next((f for f in db.get("fonti", []) if f.get("id") == fonte_id), None)
    if fonte and fonte.get("stato") in ("da-collegare", "da-svuotare"):
        return jsonify({"ok": False, "error": f"Fonte '{fonte_id}' non collegata — impossibile modificare"}), 403

    vecchio_stato     = voce.get("stato")
    voce["stato"]     = new_stato
    db["meta"]["lastUpdated"] = date.today().isoformat()

    # Salva data.json
    DATA_JSON.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

    # Prova ad aggiornare anche il README.md
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
        readme_warn = "README.md non trovato sul disco — solo data.json aggiornato"

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

MAX_CODA = 5

@app.route("/api/update-focus", methods=["POST"])
def update_focus():
    data         = request.get_json(silent=True) or {}
    voce_id      = data.get("voce_id", "").strip()
    new_focus    = data.get("focus")          # "attivo" | "coda" | None
    focus_azione = data.get("focus_azione")   # testo libero, solo per attivo

    if not voce_id:
        return jsonify({"ok": False, "error": "voce_id mancante"}), 400
    if new_focus not in (None, "attivo", "coda"):
        return jsonify({"ok": False, "error": f"Focus non valido: '{new_focus}'"}), 400

    if not DATA_JSON.exists():
        return jsonify({"ok": False, "error": "data.json non trovato"}), 500
    db = json.loads(DATA_JSON.read_text(encoding="utf-8"))

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
        # Se l'old_attivo deve scendere in coda, verifica che ci sia spazio
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

    db["meta"]["lastUpdated"] = date.today().isoformat()
    DATA_JSON.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

    return jsonify({"ok": True, "vecchio_focus": vecchio_focus, "nuovo_focus": new_focus})

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _find_readme(voce: dict) -> Path | None:
    """Ricostruisce il percorso assoluto del README.md a partire da sources.json."""
    if not SOURCES_JSON.exists():
        return None

    config   = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
    fonte_id = voce.get("fonte", "")
    percorso = voce.get("percorso", "")

    for fonte in config.get("fonti", []):
        if fonte.get("id") != fonte_id:
            continue
        for cartella in fonte.get("cartelle", []):
            candidate = Path(cartella) / percorso / "README.md"
            if candidate.exists():
                return candidate

    return None


def _update_readme_stato(readme_path: Path, new_stato: str) -> None:
    """
    Aggiorna il campo `stato:` nel frontmatter YAML del README.md.
    Sovrascrive SOLO quella riga, preservando tutto il resto.
    """
    text = readme_path.read_text(encoding="utf-8", errors="replace")

    # Sostituisce la prima occorrenza di "stato: <valore>" nel blocco frontmatter
    updated = re.sub(
        r"^(stato:\s*)(.+)$",
        lambda m: m.group(1) + new_stato,
        text,
        count=1,
        flags=re.MULTILINE,
    )

    if updated == text:
        raise ValueError("Campo 'stato:' non trovato nel frontmatter")

    readme_path.write_text(updated, encoding="utf-8")


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
