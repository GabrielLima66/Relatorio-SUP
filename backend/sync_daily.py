"""Sincroniza o Orpen (R97) para uma data específica (padrão: ontem).
Roda sozinho via cron, sem precisar do servidor Flask (backend/app.py) de pé.

Uso: python sync_daily.py [dd/mm/aaaa]

Exemplo de crontab (todo dia às 2h, sincronizando o dia anterior):
  0 2 * * * cd /caminho/para/backend && /usr/bin/python3 sync_daily.py >> sync_daily.log 2>&1
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import db  # noqa: E402
import orpen_client  # noqa: E402


def main():
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")

    db.init_db()
    print(f"[{datetime.now().isoformat()}] sincronizando Orpen {target_date}...")
    try:
        rows = orpen_client.fetch_report(target_date, target_date)
        db.upsert_atendimentos(rows)
        db.log_sync("orpen", target_date, target_date, len(rows), "ok")
        print(f"  ok — {len(rows)} atendimentos ({target_date})")
    except Exception as exc:
        db.log_sync("orpen", target_date, target_date, None, "error", str(exc))
        print(f"  erro: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
