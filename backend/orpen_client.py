import html
import os

import requests


class OrpenApiError(Exception):
    pass


def _decode_row(row):
    """The API HTML-encodes some fields (e.g. 'Agentes': 'A &gt; B'); decode
    so downstream parsing (which splits on the literal '>') works like it
    does for the manually-uploaded R97 export."""
    return {k: (html.unescape(v) if isinstance(v, str) else v) for k, v in row.items()}


def fetch_report(start_date_br, end_date_br, report_id=None):
    """start_date_br/end_date_br: 'dd/mm/aaaa'. Returns list of row dicts
    with the same keys as the R97 export."""
    resp = requests.post(
        os.environ["ORPEN_BASE_URL"],
        headers={"Authorization": os.environ["ORPEN_AUTH_HEADER"]},
        data={
            "action": "report_json",
            "userId": os.environ["ORPEN_USER_ID"],
            "reportId": str(report_id or os.environ["ORPEN_REPORT_ID"]),
            "startDate": start_date_br,
            "endDate": end_date_br,
        },
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise OrpenApiError(f"Orpen API retornou success=false: {payload}")

    rows = []
    for chunk in payload.get("data") or []:
        if isinstance(chunk, list):
            rows.extend(chunk)
    return [_decode_row(r) for r in rows]
