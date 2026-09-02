import os
import secrets
import threading
from pathlib import Path

from dotenv import load_dotenv, set_key

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

from flask import Flask, jsonify, request, send_from_directory, session  # noqa: E402

import db  # noqa: E402
import orpen_client  # noqa: E402
import qualitor_client  # noqa: E402

ROOT_DIR = Path(__file__).parent.parent
_CONFIG_LOCK = threading.Lock()

# senha compartilhada (ADMIN_PASSWORD) protege o dashboard inteiro — gate em
# before_request, abaixo. A chave de assinatura do cookie de sessão é gerada
# sozinha no primeiro boot e persistida no .env, sem precisar de passo manual.
if not os.environ.get("APP_SECRET_KEY"):
    generated_key = secrets.token_hex(32)
    set_key(str(ENV_PATH), "APP_SECRET_KEY", generated_key)
    os.environ["APP_SECRET_KEY"] = generated_key

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ["APP_SECRET_KEY"]
db.init_db()


def _br_to_iso(date_br):
    return db._br_date_to_iso(date_br) if date_br else None


@app.before_request
def require_login():
    if request.path in ("/", "/api/login", "/api/session") or request.path.startswith("/assets/"):
        return None
    if not session.get("authed"):
        return jsonify({"error": "não autenticado"}), 401


@app.get("/api/session")
def session_status():
    return jsonify({"authed": bool(session.get("authed"))})


@app.post("/api/login")
def login():
    body = request.get_json(force=True, silent=True) or {}
    password = body.get("password") or ""
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_password or not secrets.compare_digest(password, admin_password):
        return jsonify({"error": "senha incorreta"}), 401
    session.permanent = True
    session["authed"] = True
    return jsonify({"status": "ok"})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"status": "ok"})


@app.get("/")
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.get("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(ROOT_DIR / "assets", filename)


@app.get("/api/entities")
def entities_get():
    return jsonify({"entities": db.get_entities_mapping()})


@app.post("/api/entities")
def entities_post():
    body = request.get_json(force=True, silent=True) or {}
    entities = body.get("entities")
    if not isinstance(entities, list):
        return jsonify({"error": "informe 'entities' como lista"}), 400
    db.set_entities_mapping(entities)
    return jsonify({"status": "ok"})


@app.post("/api/orpen/sync")
def orpen_sync():
    body = request.get_json(force=True, silent=True) or {}
    start_date = body.get("start")
    end_date = body.get("end")
    if not start_date or not end_date:
        return jsonify({"error": "informe 'start' e 'end' no formato dd/mm/aaaa"}), 400

    try:
        rows = orpen_client.fetch_report(start_date, end_date)
        db.upsert_atendimentos(rows)
        db.log_sync("orpen", start_date, end_date, len(rows), "ok")
        return jsonify({"status": "ok", "row_count": len(rows)})
    except Exception as exc:
        db.log_sync("orpen", start_date, end_date, None, "error", str(exc))
        return jsonify({"status": "error", "error": str(exc)}), 502


@app.get("/api/orpen/data")
def orpen_data():
    start_iso = _br_to_iso(request.args.get("start"))
    end_iso = _br_to_iso(request.args.get("end"))
    rows = db.query_atendimentos(start_iso, end_iso)
    return jsonify(rows)


@app.get("/api/orpen/log")
def orpen_log():
    return jsonify(db.list_sync_log())


@app.post("/api/qualitor/sync")
def qualitor_sync():
    start_offset = db.get_qualitor_offset()
    if start_offset == 0:
        # primeira sincronização: pula pro fim da lista (~90 dias atrás) em vez de
        # paginar desde o ticket #1 — a base tem 100k+ tickets, a esmagadora
        # maioria irrelevante pro dashboard.
        try:
            start_offset = qualitor_client.find_bootstrap_offset()
        except Exception:
            start_offset = 0

    row_count = 0
    try:
        for rows, next_offset in qualitor_client.iter_new_tickets(start_offset):
            db.upsert_qualitor_chamados(rows)
            db.set_qualitor_offset(next_offset)
            row_count += len(rows)
        db.log_sync("qualitor", str(start_offset), str(db.get_qualitor_offset()), row_count, "ok")
        return jsonify({"status": "ok", "row_count": row_count})
    except Exception as exc:
        db.log_sync("qualitor", str(start_offset), str(db.get_qualitor_offset()), row_count, "error", str(exc))
        return jsonify({"status": "error", "error": str(exc)}), 502


@app.get("/api/qualitor/data")
def qualitor_data():
    start_iso = _br_to_iso(request.args.get("start"))
    end_iso = _br_to_iso(request.args.get("end"))
    rows = db.query_qualitor_chamados(start_iso, end_iso)
    return jsonify(rows)


@app.post("/api/qualitor/horas")
def qualitor_horas():
    # Sob demanda, restrito aos protocolos que o front-end pede — normalmente só
    # os chamados de agentes já mapeados nas entidades. Buscar isso pra todo o
    # histórico sincronizado seria uma chamada de API extra por chamado
    # (ver qualitor_client.fetch_horas_trabalhadas).
    body = request.get_json(force=True, silent=True) or {}
    protocolos = [str(p) for p in (body.get("protocolos") or [])]
    if not protocolos:
        return jsonify({})
    try:
        hours_by_id = qualitor_client.fetch_horas_trabalhadas_bulk(protocolos)
        db.update_horas_trabalhadas(hours_by_id)
        return jsonify(hours_by_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


CONFIG_FIELDS = [
    "ORPEN_BASE_URL", "ORPEN_AUTH_HEADER", "ORPEN_USER_ID", "ORPEN_REPORT_ID",
    "QUALITOR_BASE_URL", "QUALITOR_USER", "QUALITOR_PASSWORD",
    "QUALITOR_COMPANY", "QUALITOR_CUSTOMER", "QUALITOR_SCOPE",
]


@app.get("/api/config")
def config_get():
    return jsonify({k: os.environ.get(k, "") for k in CONFIG_FIELDS})


@app.post("/api/config")
def config_post():
    body = request.get_json(force=True, silent=True) or {}
    values = {k: body.get(k) for k in CONFIG_FIELDS}
    if not all(isinstance(v, str) and v.strip() for v in values.values()):
        return jsonify({"error": "todos os campos são obrigatórios"}), 400

    with _CONFIG_LOCK:
        qualitor_changed = any(
            k.startswith("QUALITOR_") and os.environ.get(k, "") != v
            for k, v in values.items()
        )
        for key, value in values.items():
            set_key(str(ENV_PATH), key, value)
            os.environ[key] = value
        if qualitor_changed:
            qualitor_client.reset_session()

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
