import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
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
# atendimentos transferidos da Orpen abrem chamado automático no Qualitor cuja descrição
# termina com um bloco "#TransferenciaSuporte# - Nome do cliente: ... - Numero do
# cliente: ...  Protocolo: 546068" — validado contra tickets reais nesta sessão.
_ORPEN_PROTOCOLO_RE = re.compile(r"protocolo:\s*(\d+)", re.IGNORECASE)


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


def reset_session():
    """Chamada quando QUALITOR_USER/PASSWORD mudam em runtime (tela de
    Configurações) — sem isso, _ensure_access_token() continuaria renovando a
    sessão antiga via refresh_token em vez de logar com a credencial nova."""
    global _access_token
    with _lock:
        _access_token = None
        SESSION_PATH.unlink(missing_ok=True)


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


def _extract_orpen_protocolo(description):
    """Chamados abertos automaticamente pela transferência Orpen→Qualitor têm a
    descrição terminando em '...Protocolo: 546068'. Retorna o número como string,
    ou '' se o chamado não veio desse fluxo (alerta de monitoramento, pedido
    manual etc. não têm esse padrão)."""
    m = _ORPEN_PROTOCOLO_RE.search(str(description or ""))
    return m.group(1) if m else ""


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
        # is_overdue já vem pronto no /ticket/list (calculado ao vivo pelo próprio
        # Qualitor, inclusive pra chamados ainda abertos comparando com "agora") —
        # bem mais confiável que comparar Encerramento x Previsão de resposta na
        # unha, que só funciona pra chamados já fechados (valida 83,7% vs 37,1% de
        # "Dentro do SLA" no mesmo período real, testado nesta sessão).
        "Dentro do SLA?": "Fora do Prazo" if ticket.get("is_overdue") else "No Prazo",
        # protocolo do atendimento Orpen que abriu este chamado automaticamente
        # (ver _extract_orpen_protocolo) — vazio se o chamado não veio desse fluxo.
        "Protocolo Orpen Vinculado": _extract_orpen_protocolo(ticket.get("description")),
        # preenchido sob demanda via fetch_horas_trabalhadas_bulk — /ticket/list não
        # traz isso, é um endpoint à parte (/ticket/{id}/followup) e caro demais pra
        # chamar em todo chamado sincronizado, então fica vazio até ser pedido.
        "Horas trabalhadas": "",
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


def iter_new_tickets(start_offset, limit=500, max_pages=2000):
    """Pagina a partir de start_offset (cursor da última sincronização) até uma
    página mais curta que `limit` (fim da lista). A listagem é ascendente por id
    e estável, então offset == quantidade já sincronizada funciona como cursor
    incremental. Gera (rows_mapeadas_da_página, next_offset) por página, pra
    quem consome poder persistir o progresso aos poucos em vez de perder tudo
    se uma página no meio do caminho falhar (a base tem 100k+ tickets — uma
    sincronização do zero passa por centenas de páginas)."""
    offset = start_offset
    for _ in range(max_pages):
        page = fetch_tickets_page(offset, limit=limit)
        offset += len(page)
        yield [_map_ticket(t) for t in page], offset
        if len(page) < limit:
            break


def _ticket_date(ticket):
    m = _API_DATE_RE.search(str(ticket.get("creation_date") or ""))
    return datetime(*(int(g) for g in m.groups())) if m else None


def find_bootstrap_offset(days_back=90, buffer=2000, max_probes=40):
    """Localiza por busca binária um offset próximo de `days_back` dias atrás,
    usado só na primeira sincronização (cursor ainda em 0). Evita ter que
    paginar desde o ticket #1 — a base já tem 100k+ tickets, a maioria
    irrelevante pro dashboard, que olha períodos recentes. Se a busca falhar
    por qualquer motivo, quem chama deve cair de volta pro offset 0 (mais
    lento, mas sempre correto)."""
    target = datetime.now() - timedelta(days=days_back)
    probes = 0

    low, high = 0, 1000
    while probes < max_probes:
        probes += 1
        page = fetch_tickets_page(high, limit=1)
        if not page:
            break
        date = _ticket_date(page[0])
        if date and date >= target:
            break
        low = high
        high *= 2

    while probes < max_probes and high - low > 1:
        probes += 1
        mid = (low + high) // 2
        page = fetch_tickets_page(mid, limit=1)
        if not page:
            high = mid
            continue
        date = _ticket_date(page[0])
        if date and date >= target:
            high = mid
        else:
            low = mid

    return max(0, low - buffer)


_DURATION_RE = re.compile(r"^(\d+):(\d{2})$")


def _duration_to_minutes(duration):
    m = _DURATION_RE.match(str(duration or "").strip())
    if not m:
        return 0
    h, mi = m.groups()
    return int(h) * 60 + int(mi)


def _minutes_to_hhmm(total_minutes):
    h, m = divmod(int(round(total_minutes)), 60)
    return f"{h}:{m:02d}"


def fetch_horas_trabalhadas(ticket_id):
    """Soma a duração de todos os followups do tipo 'HORAS TRABALHADAS' do
    chamado — não vem no /ticket/list, é preciso um GET /ticket/{id}/followup
    por chamado (achado testando o ticket 130053, que tinha um followup
    type='HORAS TRABALHADAS', duration='00:20')."""
    followups = _request("GET", f"/ticket/{ticket_id}/followup") or []
    total = sum(
        _duration_to_minutes(f.get("duration"))
        for f in followups
        if isinstance(f, dict) and str(f.get("type") or "").strip().upper() == "HORAS TRABALHADAS"
    )
    return _minutes_to_hhmm(total)


def fetch_horas_trabalhadas_bulk(ticket_ids, max_workers=15):
    """Busca horas trabalhadas de vários chamados em paralelo (sequencial dá
    ~0,14s/chamado; em paralelo com 15 workers cai pra ~0,024s/chamado
    efetivo — inviável fazer isso pra todo o histórico, só vale a pena
    restrito aos chamados de agentes que interessam). Retorna {ticket_id: 'HH:MM'};
    chamados que falharem ficam de fora do dict (quem chama tenta de novo depois)."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_horas_trabalhadas, tid): tid for tid in ticket_ids}
        for fut in as_completed(futures):
            tid = futures[fut]
            try:
                results[tid] = fut.result()
            except Exception:
                pass
    return results
