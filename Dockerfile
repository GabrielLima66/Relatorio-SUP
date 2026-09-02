FROM python:3.12-slim

WORKDIR /app

# só as dependências entram na imagem — o código (backend/, index.html, assets/)
# vem do bind mount do repo em runtime (ver docker-compose.yml), então
# atualizar o app é git pull + restart, sem rebuild.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

WORKDIR /app/backend
# --timeout alto: sync de período grande (ex.: 6 meses do Orpen, ~20k+ linhas) pode passar
# dos 30s padrão do gunicorn — o worker some no meio do upsert e o front-end recebe HTML de
# erro em vez de JSON. upsert_atendimentos só faz commit() no final, então um timeout não
# corrompe nada (fica tudo ou nada), mas mata a sincronização inteira sem gravar.
CMD ["gunicorn", "--workers", "2", "--timeout", "300", "--bind", "0.0.0.0:80", "app:app"]
