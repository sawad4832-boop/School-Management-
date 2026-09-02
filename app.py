"""Schul-Cloud Brandenburg - persoenliches Dashboard (Flask-Backend).

Start:  python app.py     ->  http://127.0.0.1:5000
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory, session

from schulcloud import demo as demo_data
from schulcloud import parser
from schulcloud.client import AuthError, SchulCloudClient, SchulCloudError
from schulcloud.store import Store

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dashboard")

BASE_URL = os.getenv("SC_BASE_URL", "https://brandenburg.cloud").rstrip("/")
API_URL = os.getenv("SC_API_URL") or None
REFRESH_MINUTES = int(os.getenv("SC_REFRESH_MINUTES", "15") or 0)
INGEST_TOKEN = os.getenv("SC_INGEST_TOKEN", "").strip()
DEMO_MODE = os.getenv("SC_DEMO", "0") == "1"
# Kursthemen mitlesen (Ankuendigungen ausserhalb des Aufgabenmoduls).
SCAN_TOPICS = os.getenv("SC_SCAN_TOPICS", "1") == "1"
# Optionaler Zugriffsschutz des Dashboards selbst - noetig, sobald der Server
# nicht nur auf 127.0.0.1 lauscht (Handy im gleichen WLAN, eigener Server).
DASHBOARD_PIN = os.getenv("SC_DASHBOARD_PIN", "").strip()
# Haelt die Anmeldung ueber Neustarts hinweg. Das Sitzungs-Token wird dafuer
# verschluesselt im (signierten) Cookie des Browsers abgelegt - nicht auf dem
# Server. Sinnvoll beim Hosting, wo der Prozess jederzeit neu startet.
PERSIST_SESSION = os.getenv("SC_PERSIST_SESSION", "0") == "1"
DB_PATH = os.getenv("SC_DB_PATH", "data/dashboard.sqlite3")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True, JSON_SORT_KEYS=False)

store = Store(DB_PATH)

# Sessions liegen bewusst nur im Arbeitsspeicher: Passwoerter und JWTs werden
# niemals auf die Platte geschrieben. Nach einem Neustart ist ein erneuter
# Login noetig.
SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_LOCK = threading.Lock()


# ----------------------------------------------------------------------
# Session-Verwaltung
# ----------------------------------------------------------------------
def _sid() -> Optional[str]:
    return session.get("sid")


def _box() -> "Fernet":
    """Schluessel fuer das Sitzungs-Token, abgeleitet aus dem SECRET_KEY."""
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(app.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _seal(value: str) -> str:
    return _box().encrypt(value.encode("utf-8")).decode("ascii")


def _unseal(blob: str) -> Optional[str]:
    from cryptography.fernet import InvalidToken

    try:
        return _box().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def _remember(entry: dict[str, Any]) -> None:
    """Legt das Noetige ins Cookie, um die Sitzung spaeter wiederherzustellen."""
    if not PERSIST_SESSION:
        return
    if entry["mode"] == "demo":
        session["demo"] = True
        session.permanent = True
        return
    client = entry.get("client")
    if client is not None and client.jwt:
        session["tok"] = _seal(client.jwt)
        session.permanent = True


def _restore_session() -> Optional[dict[str, Any]]:
    """Baut die Sitzung nach einem Neustart aus dem Cookie wieder auf."""
    if not PERSIST_SESSION:
        return None

    if session.get("demo"):
        entry = _new_session(None, demo_data.demo_user(), "demo")
        sync(entry)
        return entry

    blob = session.get("tok")
    token = _unseal(blob) if blob else None
    if not token:
        return None

    client = SchulCloudClient(BASE_URL, API_URL)
    try:
        user = client.login_with_jwt(token)
    except SchulCloudError:
        session.pop("tok", None)  # Token abgelaufen oder ungueltig
        return None

    entry = _new_session(client, user, "live")
    try:
        sync(entry)
    except SchulCloudError:
        log.warning("Wiederhergestellte Sitzung konnte keine Daten laden.")
    return entry


def current_session(required: bool = True) -> Optional[dict[str, Any]]:
    sid = _sid()
    with SESSION_LOCK:
        entry = SESSIONS.get(sid) if sid else None
    if entry is None:
        entry = _restore_session()
    if entry is None and required:
        raise AuthError("Nicht angemeldet.")
    return entry


def _new_session(client: Optional[SchulCloudClient], user: dict, mode: str) -> dict[str, Any]:
    sid = secrets.token_urlsafe(24)
    entry = {
        "client": client,
        "user": user,
        "mode": mode,  # "live" oder "demo"
        "items": [],
        "last_sync": None,
        "warnings": [],
        "sources": {},
        "created": datetime.now(timezone.utc),
    }
    with SESSION_LOCK:
        SESSIONS[sid] = entry
    session["sid"] = sid
    session.permanent = False
    return entry


def _display_name(user: dict) -> str:
    name = " ".join(x for x in (user.get("firstName"), user.get("lastName")) if x)
    return name or user.get("displayName") or user.get("email") or "Angemeldet"


# ----------------------------------------------------------------------
# Datenabruf
# ----------------------------------------------------------------------
def sync(entry: dict[str, Any]) -> dict[str, Any]:
    """Holt frische Daten, mischt lokalen Status dazu und cached sie."""
    if entry["mode"] == "demo":
        fetch = demo_data.demo_fetch()
        source = "demo"
    else:
        client: SchulCloudClient = entry["client"]
        fetch = client.fetch_all(scan_topics=SCAN_TOPICS)
        source = client.strategy

    items = parser.build_items(fetch, BASE_URL, source=source)
    ingested = entry.get("ingested") or []
    if ingested:
        items = parser.dedupe(items + ingested)

    store.save_items(items)
    store.prune_cache({i["id"] for i in items})

    entry["items"] = items
    entry["warnings"] = list(getattr(fetch, "warnings", []) or [])
    entry["sources"] = dict(getattr(fetch, "sources", {}) or {})
    entry["last_sync"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.set_meta("last_sync", entry["last_sync"])
    return entry


def view_items(entry: dict[str, Any]) -> dict[str, Any]:
    """Baut die Antwortstruktur fuer das Frontend (aktiv + Archiv)."""
    states = store.states()
    items = entry.get("items") or store.cached_items()

    # Abgehakte Eintraege, die nicht mehr geliefert werden, bleiben im Archiv.
    known = {i["id"] for i in items}
    for cached in store.cached_items():
        if cached["id"] not in known and states.get(cached["id"], {}).get("done"):
            items = items + [cached]

    active, archive = [], []
    for item in parser.decorate_and_sort(items):
        state = states.get(item["id"], {})
        item = dict(item)
        item["done"] = bool(state.get("done"))
        item["done_at"] = state.get("done_at")
        item["note"] = state.get("note", "")
        # Was in der Schul-Cloud abgegeben, bewertet oder abgeschlossen ist,
        # gilt als erledigt - genau wie ein selbst gesetzter Haken.
        if item["done"] or item["status"] in ("submitted", "graded") or item.get("finished"):
            archive.append(item)
        else:
            active.append(item)

    return {
        "active": active,
        "archive": sorted(archive, key=lambda i: i.get("done_at") or i.get("due") or "", reverse=True),
        "stats": _stats(active, archive),
        "last_sync": entry.get("last_sync") or store.get_meta("last_sync"),
        "warnings": entry.get("warnings", []),
        "sources": entry.get("sources", {}),
        "mode": entry.get("mode"),
    }


def _stats(active: list[dict], archive: list[dict]) -> dict[str, int]:
    levels = [i["urgency"]["level"] for i in active]
    return {
        "open": len(active),
        "overdue": levels.count("overdue"),
        "next24h": levels.count("critical"),
        "next48h": levels.count("critical") + levels.count("warning"),
        "exams": sum(1 for i in active if i["kind"] == "exam"),
        "done": len(archive),
    }


# ----------------------------------------------------------------------
# Routen
# ----------------------------------------------------------------------
# Routen, die auch ohne PIN erreichbar bleiben muessen.
PIN_FREE_ENDPOINTS = {"index", "static", "manifest", "service_worker", "api_health", "api_pin", "api_status"}


@app.before_request
def require_pin():
    """Sperrt die API, solange die PIN nicht eingegeben wurde."""
    if not DASHBOARD_PIN or request.endpoint in PIN_FREE_ENDPOINTS:
        return None
    if not session.get("pin_ok"):
        return jsonify({"ok": False, "error": "PIN erforderlich.", "pin_required": True}), 401
    return None


@app.post("/api/pin")
def api_pin():
    if not DASHBOARD_PIN:
        return jsonify({"ok": True, "pin_required": False})

    attempts = session.get("pin_attempts", 0)
    if attempts >= 10:
        return jsonify({"ok": False, "error": "Zu viele Fehlversuche. Server neu starten."}), 429

    given = str((request.get_json(silent=True) or {}).get("pin", ""))
    if secrets.compare_digest(given, DASHBOARD_PIN):
        session["pin_ok"] = True
        session["pin_attempts"] = 0
        return jsonify({"ok": True})

    session["pin_attempts"] = attempts + 1
    time.sleep(1)  # bremst Rateversuche aus
    return jsonify({"ok": False, "error": "Falsche PIN."}), 401


@app.get("/manifest.webmanifest")
def manifest():
    """Macht die Seite auf dem Handy zur App (Homebildschirm)."""
    return jsonify(
        {
            "name": "Schul-Cloud Aufgaben",
            "short_name": "Aufgaben",
            "description": "Aufgaben und Termine der Schul-Cloud Brandenburg",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#f8fafc",
            "theme_color": "#0f172a",
            "lang": "de",
            "icons": [
                {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png",
                 "purpose": "any maskable"},
                {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png",
                 "purpose": "any maskable"},
            ],
        }
    )


@app.get("/sw.js")
def service_worker():
    """Der Service Worker muss von der Wurzel kommen, um alles abzudecken."""
    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Cache-Control"] = "no-cache"
    return response


def _local_css_path() -> Path:
    return Path(app.static_folder) / "css" / "tailwind.css"


def asset_version() -> str:
    """Kennung der ausgelieferten Oberflaeche.

    Haengt an den Adressen von Skript und Stylesheet. Dadurch laedt ein Browser
    nach einer neuen Fassung garantiert beide neu - sonst kann altes HTML auf
    neues JavaScript treffen (oder umgekehrt), und die Seite bleibt leer.
    """
    stamps = []
    for path in (
        Path(app.template_folder) / "index.html",
        Path(app.static_folder) / "js" / "app.js",
        _local_css_path(),
    ):
        if path.is_file():
            stamps.append(str(int(path.stat().st_mtime)))
    return hashlib.sha256("|".join(stamps).encode()).hexdigest()[:8]


def warn_if_css_outdated() -> Optional[str]:
    """Meldet einen Tailwind-Build, der aelter als Template oder Skript ist.

    Der lokale Build enthaelt nur die Klassen, die beim Bauen im Quelltext
    standen - ist er veraltet, bricht das Layout ohne sichtbare Fehlermeldung.
    """
    css = _local_css_path()
    if not css.is_file():
        return None
    sources = [Path(app.template_folder) / "index.html", Path(app.static_folder) / "js" / "app.js"]
    newest = max((p.stat().st_mtime for p in sources if p.is_file()), default=0)
    if newest <= css.stat().st_mtime:
        return None
    return (
        "Der lokale Tailwind-Build ist aelter als die Oberflaeche. Neu bauen mit:\n"
        "  npx tailwindcss -i static/css/input.css -o static/css/tailwind.css --minify\n"
        "  (oder static/css/tailwind.css loeschen, dann wird das CDN benutzt)"
    )


@app.get("/")
def index():
    # Liegt ein lokaler Tailwind-Build vor, wird dieser statt des CDN benutzt
    # (funktioniert dann auch ohne Internetverbindung).
    local_css = _local_css_path().is_file()
    return render_template(
        "index.html",
        base_url=BASE_URL,
        demo_mode=DEMO_MODE,
        refresh_minutes=REFRESH_MINUTES,
        local_css=local_css,
        asset_version=asset_version(),
    )


@app.get("/api/status")
def api_status():
    if DASHBOARD_PIN and not session.get("pin_ok"):
        return jsonify({"logged_in": False, "pin_required": True})

    entry = current_session(required=False)
    if entry is None:
        return jsonify({"logged_in": False, "demo_available": True, "base_url": BASE_URL})
    return jsonify(
        {
            "logged_in": True,
            "user": _display_name(entry["user"]),
            "mode": entry["mode"],
            "strategy": getattr(entry.get("client"), "strategy", "demo"),
            "last_sync": entry.get("last_sync"),
            "base_url": BASE_URL,
            "refresh_minutes": REFRESH_MINUTES,
        }
    )


@app.post("/api/login")
def api_login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    jwt = (payload.get("jwt") or "").strip()
    want_demo = bool(payload.get("demo")) or (username == "demo" and password == "demo")

    if want_demo or (DEMO_MODE and not username and not jwt):
        entry = _new_session(None, demo_data.demo_user(), "demo")
        sync(entry)
        _remember(entry)
        return jsonify({"ok": True, "user": _display_name(entry["user"]), "mode": "demo"})

    client = SchulCloudClient(BASE_URL, API_URL)
    try:
        if jwt:
            user = client.login_with_jwt(jwt)
        elif username and password:
            user = client.login(username, password)
        else:
            return jsonify({"ok": False, "error": "Benutzername und Passwort werden benötigt."}), 400
    except AuthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401
    except SchulCloudError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    entry = _new_session(client, user, "live")
    try:
        sync(entry)
    except AuthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401
    _remember(entry)
    return jsonify({"ok": True, "user": _display_name(user), "mode": "live", "strategy": client.strategy})


@app.post("/api/logout")
def api_logout():
    sid = _sid()
    with SESSION_LOCK:
        entry = SESSIONS.pop(sid, None) if sid else None
    if entry and entry.get("client"):
        entry["client"].logout()
    session.clear()  # entfernt auch das gemerkte Sitzungs-Token
    return jsonify({"ok": True})


EMPTY_VIEW = {
    "active": [],
    "archive": [],
    "stats": {"open": 0, "overdue": 0, "next24h": 0, "next48h": 0, "exams": 0, "done": 0},
    "last_sync": None,
    "warnings": [],
    "sources": {},
    "mode": None,
}


@app.get("/api/items")
def api_items():
    """Aufgaben der angemeldeten Sitzung.

    Ohne Sitzung wird bewusst nichts herausgegeben - auch nicht der lokale
    Zwischenspeicher. Sonst waere die Aufgabenliste auf einer oeffentlich
    erreichbaren Adresse ohne Anmeldung lesbar.
    """
    entry = current_session(required=False)
    if entry is None:
        return jsonify({"logged_in": False, **EMPTY_VIEW})
    return jsonify({"logged_in": True, **view_items(entry)})


@app.post("/api/refresh")
def api_refresh():
    entry = current_session()
    try:
        sync(entry)
    except AuthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401
    except SchulCloudError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, **view_items(entry)})


@app.post("/api/items/<path:item_id>/done")
def api_set_done(item_id: str):
    payload = request.get_json(silent=True) or {}
    done = bool(payload.get("done", True))
    entry = current_session()
    store.set_done(item_id, done)
    return jsonify({"ok": True, "item_id": item_id, "done": done, **view_items(entry)})


@app.post("/api/items/state/bulk")
def api_bulk_state():
    """Spiegelt den Abhak-Stand des Browsers zurueck auf den Server.

    Beim Hosting auf kostenlosen Angeboten ist der Speicher des Servers
    fluechtig: nach einem Neustart waeren alle Haken weg. Das Handy hält
    deshalb eine eigene Kopie und meldet sie hier wieder an.
    """
    payload = request.get_json(silent=True) or {}
    done_ids = [str(i) for i in (payload.get("done") or [])][:500]

    entry = current_session()
    states = store.states()
    restored = 0
    for item_id in done_ids:
        if not states.get(item_id, {}).get("done"):
            store.set_done(item_id, True)
            restored += 1

    return jsonify({"ok": True, "restored": restored, **view_items(entry)})


@app.post("/api/items/<path:item_id>/note")
def api_set_note(item_id: str):
    payload = request.get_json(silent=True) or {}
    entry = current_session()
    store.set_note(item_id, (payload.get("note") or "").strip())
    return jsonify({"ok": True, **view_items(entry)})


@app.post("/api/ingest")
def api_ingest():
    """Endpunkt fuer die Browser-Erweiterung.

    Erwartet ``{"jwt": "..."}`` und/oder ``{"items": [...]}`` mit bereits im
    Browser ausgelesenen Aufgaben. Absicherung ueber ``SC_INGEST_TOKEN``.
    """
    if not INGEST_TOKEN:
        return jsonify({"ok": False, "error": "Ingest ist deaktiviert (SC_INGEST_TOKEN fehlt)."}), 403
    if not secrets.compare_digest(request.headers.get("X-Ingest-Token", ""), INGEST_TOKEN):
        return jsonify({"ok": False, "error": "Ungültiges Ingest-Token."}), 401

    payload = request.get_json(silent=True) or {}
    entry = current_session(required=False)

    jwt = (payload.get("jwt") or "").strip()
    if jwt:
        client = SchulCloudClient(BASE_URL, API_URL)
        try:
            user = client.login_with_jwt(jwt)
        except AuthError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 401
        entry = _new_session(client, user, "live")

    raw_items = payload.get("items") or []
    if raw_items:
        entry = entry or _new_session(None, {"displayName": "Browser-Erweiterung"}, "live")
        scraped = _items_from_extension(raw_items)
        entry["ingested"] = scraped
        merged = parser.dedupe((entry.get("items") or []) + scraped)
        entry["items"] = merged
        store.save_items(merged)
        entry["last_sync"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    elif entry is not None and entry.get("client"):
        sync(entry)

    if entry is None:
        return jsonify({"ok": False, "error": "Weder jwt noch items übermittelt."}), 400
    return jsonify({"ok": True, "count": len(entry.get("items") or [])})


def _items_from_extension(raw_items: list[dict]) -> list[dict]:
    """Normalisiert die von der Erweiterung gescrapten Rohdaten."""
    prepared = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        prepared.append(
            {
                "_id": raw.get("id") or raw.get("_id"),
                "name": raw.get("title") or raw.get("name"),
                "courseName": raw.get("course") or "",
                "dueDate": raw.get("due") or raw.get("dueText") or "",
                "description": raw.get("description") or "",
                "statusText": raw.get("status") or "",
                "_source": "extension",
            }
        )
    fake = type("Fetch", (), {"courses": [], "homework": prepared, "submissions": [], "events": [], "lessons": []})()
    return parser.build_items(fake, BASE_URL, source="extension")


@app.get("/api/health")
def api_health():
    return jsonify({"ok": True, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")})


@app.errorhandler(AuthError)
def handle_auth_error(exc: AuthError):
    return jsonify({"ok": False, "error": str(exc), "logged_in": False}), 401


@app.errorhandler(SchulCloudError)
def handle_sc_error(exc: SchulCloudError):
    return jsonify({"ok": False, "error": str(exc)}), 502


# ----------------------------------------------------------------------
# Hintergrund-Aktualisierung
# ----------------------------------------------------------------------
def _refresh_loop() -> None:
    interval = max(REFRESH_MINUTES, 1) * 60
    while True:
        time.sleep(interval)
        with SESSION_LOCK:
            entries = list(SESSIONS.values())
        for entry in entries:
            try:
                sync(entry)
                log.info("Automatische Aktualisierung: %d Einträge", len(entry["items"]))
            except AuthError:
                log.warning("Session abgelaufen – automatische Aktualisierung gestoppt.")
            except Exception:  # pragma: no cover - Hintergrundthread darf nie sterben
                log.exception("Automatische Aktualisierung fehlgeschlagen")


def start_background_refresh() -> None:
    if REFRESH_MINUTES <= 0:
        return
    thread = threading.Thread(target=_refresh_loop, name="sc-refresh", daemon=True)
    thread.start()
    log.info("Automatische Aktualisierung alle %d Minuten aktiv.", REFRESH_MINUTES)


if os.getenv("WERKZEUG_RUN_MAIN") != "true":
    start_background_refresh()


if __name__ == "__main__":
    from schulcloud.netinfo import report

    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "127.0.0.1")
    print(report(port, host))
    stale = warn_if_css_outdated()
    if stale:
        print("\n! " + stale)
    if host == "0.0.0.0" and not DASHBOARD_PIN:
        print(
            "\n! Der Server ist im ganzen Netzwerk erreichbar, aber ohne PIN.\n"
            "  Empfehlung: SC_DASHBOARD_PIN in der .env setzen und neu starten."
        )
    app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG") == "1")
