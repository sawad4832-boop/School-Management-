"""Diagnose: prueft Erreichbarkeit, Login und Datenabruf auf diesem Rechner.

Aufruf::

    python -m schulcloud.check              # mit Login-Abfrage
    python -m schulcloud.check --no-login   # nur Erreichbarkeit pruefen

Das Passwort wird verdeckt eingegeben, nirgends gespeichert und ausschliesslich
an die eigene Schul-Cloud-Instanz geschickt.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import requests
from dotenv import load_dotenv

from .client import AuthError, SchulCloudClient, SchulCloudError
from .parser import build_items, decorate_and_sort

OK, FAIL, INFO = "  ✅", "  ❌", "  •"


def _probe(base_url: str) -> bool:
    """Erreichbarkeit und Vorhandensein der wichtigen Endpunkte."""
    print(f"\n1) Erreichbarkeit von {base_url}")
    try:
        resp = requests.get(f"{base_url}/login", timeout=20)
        print(f"{OK} Login-Seite erreichbar (HTTP {resp.status_code})")
    except requests.RequestException as exc:
        print(f"{FAIL} Keine Verbindung: {exc}")
        print(f"{INFO} Internetverbindung, VPN oder Proxy prüfen.")
        return False

    print("\n2) Erwartete Endpunkte (401 = vorhanden, aber Login nötig)")
    for path in ("api/v3/tasks", "api/v3/courses", "api/v3/me", "api/v3/news"):
        try:
            code = requests.get(f"{base_url}/{path}", timeout=15).status_code
        except requests.RequestException as exc:
            print(f"{FAIL} /{path}: {exc}")
            continue
        mark = OK if code in (200, 401) else FAIL
        print(f"{mark} /{path}: HTTP {code}")
    return True


def _login(base_url: str, api_url: str | None) -> SchulCloudClient | None:
    print("\n3) Anmeldung")
    username = input("   E-Mail / Nutzername: ").strip()
    if not username:
        print(f"{INFO} Abgebrochen.")
        return None
    password = getpass.getpass("   Passwort (Eingabe bleibt unsichtbar): ")

    client = SchulCloudClient(base_url, api_url)
    try:
        user = client.login(username, password)
    except AuthError as exc:
        print(f"{FAIL} {exc}")
        print(f"{INFO} Bei Zwei-Faktor-Anmeldung oder SSO: Session-Token (JWT) "
              "im Dashboard verwenden – siehe README, Abschnitt 2.")
        return None
    except SchulCloudError as exc:
        print(f"{FAIL} {exc}")
        return None
    finally:
        del password

    name = " ".join(x for x in (user.get("firstName"), user.get("lastName")) if x)
    print(f"{OK} Angemeldet als {name or 'unbekannt'}"
          + (f" ({user['school']})" if user.get("school") else ""))
    print(f"{INFO} verwendete Strategie: {client.strategy}")
    return client


def _fetch(client: SchulCloudClient, base_url: str) -> int:
    print("\n4) Datenabruf")
    try:
        result = client.fetch_all()
    except SchulCloudError as exc:
        print(f"{FAIL} {exc}")
        return 1

    print(f"{INFO} Quellen: " + ", ".join(f"{k}={v}" for k, v in result.sources.items()))
    print(f"{OK} {len(result.courses)} Kurse, {len(result.tasks) + len(result.homework)} Aufgaben, "
          f"{len(result.events)} Termine, {len(result.news)} Ankündigungen")
    for warning in result.warnings:
        print(f"{FAIL} {warning}")

    items = decorate_and_sort(build_items(result, base_url, source=client.strategy))
    if not items:
        print(f"{INFO} Keine Aufgaben gefunden – das kann auch schlicht bedeuten, "
              "dass gerade nichts offen ist.")
        return 0

    print("\n5) Die nächsten Termine")
    for item in items[:5]:
        due = (item.get("due") or "ohne Termin")[:16].replace("T", " ")
        print(f"   [{item['urgency']['level']:8}] {due:16} {item['course'][:18]:18} "
              f"{item['title'][:40]:40} {item['status']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    argparser = argparse.ArgumentParser(description="Verbindungstest für das Schul-Cloud-Dashboard")
    argparser.add_argument("--no-login", action="store_true", help="nur die Erreichbarkeit prüfen")
    argparser.add_argument("--base-url", default=os.getenv("SC_BASE_URL", "https://brandenburg.cloud"))
    args = argparser.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    api_url = os.getenv("SC_API_URL") or None

    print("Schul-Cloud Dashboard – Verbindungstest")
    if not _probe(base_url):
        return 1
    if args.no_login:
        print("\nFertig (ohne Login-Test).")
        return 0

    client = _login(base_url, api_url)
    if client is None:
        return 1
    try:
        return _fetch(client, base_url)
    finally:
        client.logout()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
