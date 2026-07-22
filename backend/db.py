import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "dashboard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS orpen_atendimentos (
    protocolo TEXT PRIMARY KEY,
    fim_atendimento_dia TEXT,
    primeira_mensagem_dia TEXT,
    data_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orpen_fim_dia ON orpen_atendimentos(fim_atendimento_dia);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    row_count INTEGER,
    status TEXT NOT NULL,
    error TEXT
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")


def _br_date_to_iso(value):
    """'24/06/2026 14:22' -> '2026-06-24'. Returns None if unparseable."""
    if not value:
        return None
    m = _DATE_RE.search(str(value))
    if not m:
        return None
    d, mth, y = m.groups()
    y = int(y)
    if y < 100:
        y += 2000
    return f"{y:04d}-{int(mth):02d}-{int(d):02d}"


def upsert_atendimentos(rows):
    """rows: list of dict with original Orpen API keys. Dedup by Protocolo."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    try:
        for row in rows:
            protocolo = str(row.get("Protocolo") or "").strip()
            if not protocolo:
                continue
            conn.execute(
                """INSERT INTO orpen_atendimentos
                   (protocolo, fim_atendimento_dia, primeira_mensagem_dia, data_json, synced_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(protocolo) DO UPDATE SET
                     fim_atendimento_dia=excluded.fim_atendimento_dia,
                     primeira_mensagem_dia=excluded.primeira_mensagem_dia,
                     data_json=excluded.data_json,
                     synced_at=excluded.synced_at""",
                (
                    protocolo,
                    _br_date_to_iso(row.get("Fim Atendimento")),
                    _br_date_to_iso(row.get("Primeira mensagem do cliente")),
                    json.dumps(row, ensure_ascii=False),
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def query_atendimentos(start_iso=None, end_iso=None):
    """Returns list of dict (original Orpen keys) filtered by Fim Atendimento date range."""
    conn = get_connection()
    try:
        sql = "SELECT data_json FROM orpen_atendimentos WHERE 1=1"
        params = []
        if start_iso:
            sql += " AND fim_atendimento_dia >= ?"
            params.append(start_iso)
        if end_iso:
            sql += " AND fim_atendimento_dia <= ?"
            params.append(end_iso)
        sql += " ORDER BY fim_atendimento_dia"
        cur = conn.execute(sql, params)
        return [json.loads(r["data_json"]) for r in cur.fetchall()]
    finally:
        conn.close()


def log_sync(source, start_date, end_date, row_count, status, error=None):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO sync_log (source, start_date, end_date, requested_at, row_count, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source, start_date, end_date, datetime.now(timezone.utc).isoformat(), row_count, status, error),
        )
        conn.commit()
    finally:
        conn.close()


def list_sync_log(limit=50):
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
