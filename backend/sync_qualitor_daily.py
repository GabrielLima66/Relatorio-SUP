"""Sincroniza o Qualitor incrementalmente (a partir do cursor salvo em
qualitor_sync_state.next_offset). Roda sozinho via cron, sem precisar do
servidor Flask (backend/app.py) de pé.

Uso: python sync_qualitor_daily.py

Exemplo de crontab (todo dia às 2h10, alguns minutos depois do Orpen):
  10 2 * * * cd /caminho/para/backend && /usr/bin/python3 sync_qualitor_daily.py >> sync_qualitor_daily.log 2>&1
"""
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import db  # noqa: E402
import qualitor_client  # noqa: E402


def main():
    db.init_db()
    start_offset = db.get_qualitor_offset()
    if start_offset == 0:
        print(f"[{datetime.now().isoformat()}] primeira sincronização — localizando offset inicial...")
        try:
            start_offset = qualitor_client.find_bootstrap_offset()
        except Exception:
            start_offset = 0

    print(f"[{datetime.now().isoformat()}] sincronizando Qualitor a partir do offset {start_offset}...")
    row_count = 0
    try:
        for rows, next_offset in qualitor_client.iter_new_tickets(start_offset):
            db.upsert_qualitor_chamados(rows)
            db.set_qualitor_offset(next_offset)
            row_count += len(rows)
        db.log_sync("qualitor", str(start_offset), str(db.get_qualitor_offset()), row_count, "ok")
        print(f"  ok — {row_count} chamados novos/atualizados")
    except Exception as exc:
        db.log_sync("qualitor", str(start_offset), str(db.get_qualitor_offset()), row_count, "error", str(exc))
        print(f"  erro: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
