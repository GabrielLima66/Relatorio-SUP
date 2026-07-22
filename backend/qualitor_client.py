import json
import os
import re
import threading
from pathlib import Path

import requests
import urllib3

# O host só é alcançável pelo IP interno (172.31.1.81), cujo certificado é emitido
# para *.rcxit.com.br -> SAN não bate com o IP. Rede é só VPN interna, então
# desabilitamos a verificação de TLS especificamente para essas chamadas.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SESSION_PATH = Path(__file__).parent / "qualitor_session.json"

# access_token só existe em memória do processo (nunca em disco); refresh_token
# é o único persistido, para não pedir login de novo a cada restart do backend.
_access_token = None
_lock = threading.Lock()

_TOKEN_EXPIRED_RE = re.compile(r"token\s*expired", re.IGNORECASE)
_API_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})")


class QualitorApiError(Exception):
    pass


def _base_url():
    return os.environ["QUALITOR_BASE_URL"].rstrip("/")


def _load_refresh_token():
    if not SESSION_PATH.exists():
        return None
    try:
        return json.loads(SESSION_PATH.read_text()).get("refresh_token")
    except (json.JSONDecodeError, OSError):
        return None


def _save_refresh_token(token):
    if token:
        SESSION_PATH.write_text(json.dumps({"refresh_token": token}))


def _is_token_expired(status, body):
    if status == 401:
        return True
    message = body.get("message") if isinstance(body, dict) else None
    return bool(message and _TOKEN_EXPIRED_RE.search(str(message)))


def _login():
    global _access_token
    resp = requests.post(
        f"{_base_url()}/auth/login",
        json={
            "user": os.environ["QUALITOR_USER"],
            "password": os.environ["QUALITOR_PASSWORD"],
            "company": int(os.environ.get("QUALITOR_COMPANY", 1)),
            "customer": int(os.environ.get("QUALITOR_CUSTOMER", 0)),
            "scope": os.environ.get("QUALITOR_SCOPE", "USER"),
        },
        verify=False,
        timeout=30,
    )
    resp.raise_for_status()
    tokens = resp.json()["token"]["payload"]
    _access_token = tokens["access_token"]
    _save_refresh_token(tokens.get("refresh_token"))


def _refresh():
    """True se conseguiu renovar; False se o refresh_token também já morreu."""
    global _access_token
    refresh_token = _load_refresh_token()
    if not refresh_token:
        return False
    resp = requests.patch(
        f"{_base_url()}/auth/token",
        headers={"Authorization": f"Bearer {refresh_token}"},
        verify=False,
        timeout=30,
    )
    if resp.status_code != 200:
        return False
    tokens = resp.json()["token"]["payload"]
    _access_token = tokens["access_token"]
    _save_refresh_token(tokens.get("refresh_token") or refresh_token)
    return True


def _ensure_access_token():
    if _access_token:
        return
    with _lock:
        if _access_token:
            return
        if not _refresh():
            _login()


def _parse_body(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def _do_request(method, path, json_body, params):
    return requests.request(
        method,
        f"{_base_url()}{path}",
        headers={"Authorization": f"Bearer {_access_token}"},
        json=json_body,
        params=params,
        verify=False,
        timeout=60,
    )


def _request(method, path, json_body=None, params=None):
    _ensure_access_token()
    resp = _do_request(method, path, json_body, params)
    body = _parse_body(resp)

    if _is_token_expired(resp.status_code, body):
        with _lock:
            if not _refresh():
                _login()
        resp = _do_request(method, path, json_body, params)
        body = _parse_body(resp)

    resp.raise_for_status()
    return body


def _clean_filters(filters):
    """Remove chaves com valor 0/''/None — a API não trata bem filtro vazio explícito."""
    return {k: v for k, v in (filters or {}).items() if v}


def _api_date_to_br(value):
    """'2021-02-22 09:41:33.617' -> '22/02/2021 09:41'. Ausente/inválido -> ''."""
    if not value:
        return ""
    m = _API_DATE_RE.search(str(value))
    if not m:
        return ""
    y, mth, d, h, mi = m.groups()
    return f"{d}/{mth}/{y} {h}:{mi}"


def _map_ticket(ticket):
    """Achata o ticket da API pro mesmo formato de colunas do export manual do
    Qualitor, pra reaproveitar o parser existente no front-end (processQual)."""
    status = ticket.get("status") or {}
    assignee = ticket.get("assignee") or {}
    return {
        "Protocolo": str(ticket.get("id") or ""),
        "Responsável": assignee.get("name") or "",
        "Situação": status.get("name") or "",
        "Abertura": _api_date_to_br(ticket.get("creation_date")),
        "Encerramento": _api_date_to_br(ticket.get("end_date")),
        "Previsão de resposta": _api_date_to_br(ticket.get("due_date")),
    }


def fetch_tickets_page(offset, limit=500, filters=None):
    body = _request(
        "POST",
        "/ticket/list",
        json_body=_clean_filters(filters),
        params={"limit": limit, "offset": offset},
    )
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "tickets", "data"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


def fetch_new_tickets(start_offset, limit=500, max_pages=1000):
    """Pagina a partir de start_offset (cursor da última sincronização) até uma
    página mais curta que `limit` (fim da lista). A listagem é ascendente por id
    e estável, então offset == quantidade já sincronizada funciona como cursor
    incremental. Retorna (rows_mapeadas, next_offset)."""
    rows = []
    offset = start_offset
    for _ in range(max_pages):
        page = fetch_tickets_page(offset, limit=limit)
        rows.extend(_map_ticket(t) for t in page)
        offset += len(page)
        if len(page) < limit:
            break
    return rows, offset
