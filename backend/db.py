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

CREATE TABLE IF NOT EXISTS qualitor_chamados (
    id TEXT PRIMARY KEY,
    abertura_dia TEXT,
    data_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qualitor_abertura ON qualitor_chamados(abertura_dia);

CREATE TABLE IF NOT EXISTS qualitor_sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_offset INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS entities_mapping (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
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


def upsert_qualitor_chamados(rows):
    """rows: list of dict já no formato de colunas do export manual do Qualitor
    (ver qualitor_client._map_ticket). Dedup por Protocolo (id do ticket)."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    try:
        for row in rows:
            ticket_id = str(row.get("Protocolo") or "").strip()
            if not ticket_id:
                continue
            conn.execute(
                """INSERT INTO qualitor_chamados (id, abertura_dia, data_json, synced_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     abertura_dia=excluded.abertura_dia,
                     data_json=excluded.data_json,
                     synced_at=excluded.synced_at""",
                (
                    ticket_id,
                    _br_date_to_iso(row.get("Abertura")),
                    json.dumps(row, ensure_ascii=False),
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def query_qualitor_chamados(start_iso=None, end_iso=None):
    """Returns list of dict filtrado por Abertura (data de abertura do chamado)."""
    conn = get_connection()
    try:
        sql = "SELECT data_json FROM qualitor_chamados WHERE 1=1"
        params = []
        if start_iso:
            sql += " AND abertura_dia >= ?"
            params.append(start_iso)
        if end_iso:
            sql += " AND abertura_dia <= ?"
            params.append(end_iso)
        sql += " ORDER BY abertura_dia"
        cur = conn.execute(sql, params)
        return [json.loads(r["data_json"]) for r in cur.fetchall()]
    finally:
        conn.close()


def update_horas_trabalhadas(hours_by_id):
    """hours_by_id: {protocolo: 'HH:MM'}. Atualiza só o campo 'Horas trabalhadas'
    dos chamados já armazenados (busca à parte, feita sob demanda — ver
    qualitor_client.fetch_horas_trabalhadas_bulk), sem mexer no resto do registro."""
    if not hours_by_id:
        return
    conn = get_connection()
    try:
        for ticket_id, hhmm in hours_by_id.items():
            row = conn.execute(
                "SELECT data_json FROM qualitor_chamados WHERE id = ?", (ticket_id,)
            ).fetchone()
            if not row:
                continue
            data = json.loads(row["data_json"])
            data["Horas trabalhadas"] = hhmm
            conn.execute(
                "UPDATE qualitor_chamados SET data_json = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), ticket_id),
            )
        conn.commit()
    finally:
        conn.close()


def update_dentro_do_sla(sla_by_id):
    """sla_by_id: {protocolo: 'No Prazo'|'Fora do Prazo'}. Mesmo padrão de
    update_horas_trabalhadas — usado pra backfill dos chamados sincronizados
    antes do campo 'Dentro do SLA?' (baseado em is_overdue) existir."""
    if not sla_by_id:
        return
    conn = get_connection()
    try:
        for ticket_id, valor in sla_by_id.items():
            row = conn.execute(
                "SELECT data_json FROM qualitor_chamados WHERE id = ?", (ticket_id,)
            ).fetchone()
            if not row:
                continue
            data = json.loads(row["data_json"])
            data["Dentro do SLA?"] = valor
            conn.execute(
                "UPDATE qualitor_chamados SET data_json = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), ticket_id),
            )
        conn.commit()
    finally:
        conn.close()


def update_protocolo_vinculado(vinculo_by_id):
    """vinculo_by_id: {protocolo_qualitor: protocolo_orpen_ou_''}. Mesmo padrão de
    update_dentro_do_sla — usado pra backfill dos chamados sincronizados antes do
    campo 'Protocolo Orpen Vinculado' existir."""
    if not vinculo_by_id:
        return
    conn = get_connection()
    try:
        for ticket_id, valor in vinculo_by_id.items():
            row = conn.execute(
                "SELECT data_json FROM qualitor_chamados WHERE id = ?", (ticket_id,)
            ).fetchone()
            if not row:
                continue
            data = json.loads(row["data_json"])
            data["Protocolo Orpen Vinculado"] = valor
            conn.execute(
                "UPDATE qualitor_chamados SET data_json = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), ticket_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_qualitor_offset():
    conn = get_connection()
    try:
        row = conn.execute("SELECT next_offset FROM qualitor_sync_state WHERE id = 1").fetchone()
        return row["next_offset"] if row else 0
    finally:
        conn.close()


def set_qualitor_offset(offset):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO qualitor_sync_state (id, next_offset) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET next_offset=excluded.next_offset""",
            (offset,),
        )
        conn.commit()
    finally:
        conn.close()


def get_entities_mapping():
    """Retorna a lista de entidades (ver formato em index.html: ENTITIES),
    ou [] se nunca foi salva."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT data_json FROM entities_mapping WHERE id = 1").fetchone()
        return json.loads(row["data_json"]) if row else []
    finally:
        conn.close()


def set_entities_mapping(entities):
    """entities: lista de {id, nome, orpenLinks, qualLinks} — salva o grafo
    inteiro de uma vez (o front-end já trata ENTITIES como um bloco atômico,
    sem necessidade de consulta por entidade individual)."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """INSERT INTO entities_mapping (id, data_json, updated_at) VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at""",
            (json.dumps(entities, ensure_ascii=False), now),
        )
        conn.commit()
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
