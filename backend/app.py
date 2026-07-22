from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

import db  # noqa: E402
import orpen_client  # noqa: E402

ROOT_DIR = Path(__file__).parent.parent

app = Flask(__name__, static_folder=None)
db.init_db()


def _br_to_iso(date_br):
    return db._br_date_to_iso(date_br) if date_br else None


@app.get("/")
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.get("/styles.css")
def styles():
    return send_from_directory(ROOT_DIR, "styles.css")


@app.get("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(ROOT_DIR / "assets", filename)


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
