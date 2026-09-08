"""
Actualiza la venta real del día en index.html a partir del mail de
it@indutop.uy ("Bit - Venta diaria sucursales").

Pensado para correr una vez por noche desde GitHub Actions.
Requiere las variables de entorno GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET
y GMAIL_REFRESH_TOKEN (ver instrucciones de configuración).
"""

import base64
import datetime
import json
import os
import re
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

HTML_PATH = "index.html"
MONTEVIDEO_OFFSET_HOURS = -3  # Uruguay no usa horario de verano


def get_gmail_service():
    creds = Credentials(
        None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    return build("gmail", "v1", credentials=creds)


def get_plain_text(message):
    def walk(part):
        if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode(
                "utf-8", errors="replace"
            )
        for p in part.get("parts", []):
            found = walk(p)
            if found:
                return found
        return None

    return walk(message["payload"]) or ""


def find_todays_thread(service, target_date_str):
    query = (
        'from:it@indutop.uy subject:"Bit - Venta diaria sucursales" newer_than:2d'
    )
    result = service.users().threads().list(userId="me", q=query).execute()
    for thread_meta in result.get("threads", []):
        thread = (
            service.users()
            .threads()
            .get(userId="me", id=thread_meta["id"], format="full")
            .execute()
        )
        for msg in thread["messages"]:
            body = get_plain_text(msg)
            if f"Fecha: {target_date_str}" in body:
                return thread["messages"]
    return None


def pick_final_message_body(messages, target_date_str):
    """Entre los mensajes del día, elige el de hora más tardía (normalmente 23:35),
    ignorando el mensaje de 00:35 del día siguiente que resetea a 0."""
    candidates = []
    for msg in messages:
        body = get_plain_text(msg)
        match = re.search(r"Fecha: (\d{2}/\d{2}/\d{4}) (\d{1,2}:\d{2})", body)
        if match and match.group(1) == target_date_str:
            candidates.append((match.group(2), body))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def parse_branch_totals(body):
    """Extrae {id_sucursal: TOTAL $} de la tabla 'SUCURSALES URUGUAY'."""
    totals = {}
    for line in body.splitlines():
        m = re.match(
            r"\|\s*(\d+)\s*-\s*[^|]+\|\s*[\d,]+\s*\|\s*[\d,]+\s*\|\s*([\d,]+)\s*\|",
            line,
        )
        if m:
            branch_id = m.group(1)
            total = int(m.group(2).replace(",", ""))
            totals[branch_id] = total
    return totals


def update_branch_data(html_path, month_key, date_key, sales_by_id):
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    idx1 = html.find("<script>")
    idx2 = html.find("</script>")
    script = html[idx1 + 8 : idx2]

    start = script.find("{", script.find("var BRANCH_DATA"))
    depth = 0
    i = start
    while True:
        if script[i] == "{":
            depth += 1
        elif script[i] == "}":
            depth -= 1
        if depth == 0:
            break
        i += 1

    branch_data = json.loads(script[start : i + 1])
    day = branch_data.get(month_key, {}).get(date_key)
    if day is None:
        print(f"No existe {month_key}/{date_key} en BRANCH_DATA, nada para actualizar.")
        return 0

    updated = 0
    for store in day["stores"]:
        branch_id = store["name"].split("-")[0]
        if branch_id == "44":  # WEB VENTA, no es una sucursal
            continue
        if branch_id in sales_by_id:
            store["real"] = sales_by_id[branch_id]
            updated += 1

    new_script = script[:start] + json.dumps(branch_data) + script[i + 1 :]
    new_html = html[: idx1 + 8] + new_script + html[idx2:]

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    return updated


def main():
    tz = datetime.timezone(datetime.timedelta(hours=MONTEVIDEO_OFFSET_HOURS))
    now = datetime.datetime.now(tz)

    target_date_str = now.strftime("%d/%m/%Y")
    month_key = f"{now.year - 1}-{now.month:02d}"
    date_key = now.strftime("%Y-%m-%d")

    service = get_gmail_service()
    messages = find_todays_thread(service, target_date_str)
    if not messages:
        print(f"Todavía no llegó el mail de hoy ({target_date_str}).")
        sys.exit(0)

    body = pick_final_message_body(messages, target_date_str)
    if not body:
        print("No se pudo identificar el mensaje final del día en el hilo.")
        sys.exit(1)

    sales_by_id = parse_branch_totals(body)
    if not sales_by_id:
        print("No se pudo parsear ninguna sucursal del mail.")
        sys.exit(1)

    updated = update_branch_data(HTML_PATH, month_key, date_key, sales_by_id)
    print(f"Actualizado {date_key}: {updated} sucursales cargadas.")


if __name__ == "__main__":
    main()
