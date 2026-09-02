"""End-to-End-Tests der Flask-Endpunkte im Demo-Modus."""

import os

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SC_DB_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("SC_REFRESH_MINUTES", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    import importlib

    import app as app_module

    importlib.reload(app_module)
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as test_client:
        yield test_client


def login_demo(client):
    return client.post("/api/login", json={"demo": True})


def test_status_before_login(client):
    data = client.get("/api/status").get_json()
    assert data["logged_in"] is False


def test_login_and_items(client):
    assert login_demo(client).get_json()["mode"] == "demo"

    data = client.get("/api/items").get_json()
    assert data["logged_in"] is True
    assert data["stats"]["open"] >= 5
    # Dringendstes zuerst - Abgelaufenes steht gar nicht mehr in der Liste
    assert data["active"][0]["urgency"]["level"] == "critical"
    assert all(i["urgency"]["level"] != "overdue" for i in data["active"])
    # Bewertete Aufgabe liegt sofort im Archiv
    assert any(i["status"] == "graded" for i in data["archive"])


def test_check_off_moves_item_to_archive(client):
    login_demo(client)
    item_id = client.get("/api/items").get_json()["active"][0]["id"]

    data = client.post(f"/api/items/{item_id}/done", json={"done": True}).get_json()
    assert all(i["id"] != item_id for i in data["active"])
    assert any(i["id"] == item_id and i["done"] for i in data["archive"])

    data = client.post(f"/api/items/{item_id}/done", json={"done": False}).get_json()
    assert any(i["id"] == item_id for i in data["active"])


def test_refresh_keeps_local_done_state(client):
    login_demo(client)
    item_id = client.get("/api/items").get_json()["active"][0]["id"]
    client.post(f"/api/items/{item_id}/done", json={"done": True})

    data = client.post("/api/refresh").get_json()
    assert data["ok"] is True
    assert any(i["id"] == item_id and i["done"] for i in data["archive"])


def test_refresh_requires_login(client):
    assert client.post("/api/refresh").status_code == 401


def test_ingest_requires_token(client):
    assert client.post("/api/ingest", json={"items": []}).status_code == 403


def test_ingest_with_token(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "INGEST_TOKEN", "geheim")
    payload = {"items": [{"id": "abc123def", "title": "Referat Geschichte",
                          "course": "Geschichte 10b", "due": "12.09.2026, 18:00"}]}
    res = client.post("/api/ingest", json=payload, headers={"X-Ingest-Token": "geheim"})
    assert res.get_json()["ok"] is True

    items = client.get("/api/items").get_json()["active"]
    ingested = next(i for i in items if i["title"] == "Referat Geschichte")
    assert ingested["kind"] == "exam"       # "Referat" wird als Leistungsnachweis erkannt
    assert ingested["due"].startswith("2026-09-12T18:00")


def test_index_page_renders(client):
    body = client.get("/").get_data(as_text=True)
    assert "Schul-Cloud Dashboard" in body
    assert "/static/js/app.js" in body


# ----------------------------------------------------------------------
# Handy-Betrieb: PWA-Dateien und PIN-Schutz
# ----------------------------------------------------------------------
def test_manifest_describes_installable_app(client):
    data = client.get("/manifest.webmanifest").get_json()
    assert data["display"] == "standalone"
    assert data["start_url"] == "/"
    assert {icon["sizes"] for icon in data["icons"]} == {"192x192", "512x512"}


def test_service_worker_served_from_root(client):
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert "javascript" in res.headers["Content-Type"]


def test_index_links_manifest_and_icons(client):
    body = client.get("/").get_data(as_text=True)
    assert "/manifest.webmanifest" in body
    assert "apple-mobile-web-app-capable" in body
    assert "viewport-fit=cover" in body


@pytest.fixture()
def pin_client(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "DASHBOARD_PIN", "1234")
    return client


def test_pin_blocks_api_until_entered(pin_client):
    status = pin_client.get("/api/status").get_json()
    assert status["pin_required"] is True

    blocked = pin_client.get("/api/items")
    assert blocked.status_code == 401
    assert blocked.get_json()["pin_required"] is True

    assert pin_client.post("/api/pin", json={"pin": "0000"}).status_code == 401
    assert pin_client.post("/api/pin", json={"pin": "1234"}).get_json()["ok"] is True

    assert pin_client.get("/api/items").status_code == 200
    assert pin_client.get("/api/status").get_json().get("pin_required") is None


def test_pin_off_by_default(client):
    assert client.get("/api/status").get_json().get("pin_required") is None
    assert client.get("/api/items").status_code == 200


def test_warns_when_tailwind_build_is_stale(client):
    """Ein Build, der aelter als die Oberflaeche ist, muss auffallen."""
    import os
    import time

    import app as app_module

    template = os.path.join(app_module.app.template_folder, "index.html")
    script = os.path.join(app_module.app.static_folder, "js", "app.js")
    css = os.path.join(app_module.app.static_folder, "css", "tailwind.css")
    originals = {path: os.stat(path).st_mtime for path in (template, script, css)}
    base = min(originals.values())

    try:
        # Build neuer als beide Quellen -> keine Warnung
        for path in (template, script):
            os.utime(path, (base, base))
        os.utime(css, (base + 60, base + 60))
        assert app_module.warn_if_css_outdated() is None

        # Eine Quelle neuer als der Build -> Warnung mit Bauanleitung
        os.utime(template, (base + 120, base + 120))
        assert "tailwindcss" in app_module.warn_if_css_outdated()
    finally:
        for path, mtime in originals.items():
            os.utime(path, (mtime, mtime))


# ----------------------------------------------------------------------
# Betrieb auf einem Hoster: Sitzung und Haken ueberstehen Neustarts
# ----------------------------------------------------------------------
def test_sealed_token_roundtrip(client):
    import app as app_module

    sealed = app_module._seal("mein-jwt")
    assert sealed != "mein-jwt"
    assert app_module._unseal(sealed) == "mein-jwt"
    assert app_module._unseal("kaputt") is None


def test_demo_session_survives_restart(client, monkeypatch):
    """Nach einem Neustart ist der Arbeitsspeicher leer - das Cookie rettet die Sitzung."""
    import app as app_module

    monkeypatch.setattr(app_module, "PERSIST_SESSION", True)
    login_demo(client)
    assert client.get("/api/items").get_json()["logged_in"] is True

    app_module.SESSIONS.clear()  # entspricht einem Neustart des Servers
    data = client.get("/api/items").get_json()
    assert data["logged_in"] is True
    assert data["stats"]["open"] >= 5


def test_without_persistence_restart_logs_out(client):
    login_demo(client)
    import app as app_module

    app_module.SESSIONS.clear()
    assert client.get("/api/items").get_json()["logged_in"] is False


def test_bulk_restores_checkmarks_after_data_loss(client):
    """Der Browser meldet Haken nach, die der Server nicht mehr kennt."""
    login_demo(client)
    item_id = client.get("/api/items").get_json()["active"][0]["id"]

    data = client.post("/api/items/state/bulk", json={"done": [item_id]}).get_json()
    assert data["restored"] == 1
    assert any(i["id"] == item_id and i["done"] for i in data["archive"])

    # Nochmal dieselbe Meldung aendert nichts mehr
    assert client.post("/api/items/state/bulk", json={"done": [item_id]}).get_json()["restored"] == 0


def test_no_data_without_session(client):
    """Ohne Anmeldung darf nichts herausgehen - die Adresse ist oeffentlich."""
    login_demo(client)
    item_id = client.get("/api/items").get_json()["active"][0]["id"]
    client.post(f"/api/items/{item_id}/done", json={"done": True})
    client.post("/api/logout")

    data = client.get("/api/items").get_json()
    assert data["logged_in"] is False
    assert data["active"] == [] and data["archive"] == []
    assert data["stats"]["open"] == 0

    # Auch Schreibzugriffe sind ohne Anmeldung gesperrt
    assert client.post(f"/api/items/{item_id}/done", json={"done": False}).status_code == 401
    assert client.post("/api/items/state/bulk", json={"done": [item_id]}).status_code == 401
    assert client.post(f"/api/items/{item_id}/note", json={"note": "x"}).status_code == 401


def test_submitted_tasks_leave_the_todo_list(client):
    """In der Schul-Cloud abgegebene Aufgaben stehen nicht mehr unter "offen"."""
    login_demo(client)
    data = client.get("/api/items").get_json()

    assert all(i["status"] == "open" for i in data["active"])
    archived = {i["status"] for i in data["archive"]}
    assert "submitted" in archived and "graded" in archived


def test_asset_version_changes_with_the_interface(client):
    """Skript- und HTML-Adresse tragen eine Kennung, damit beide zusammenpassen."""
    import os
    import time

    import app as app_module

    body = client.get("/").get_data(as_text=True)
    version = app_module.asset_version()
    assert f"app.js?v={version}" in body

    script = os.path.join(app_module.app.static_folder, "js", "app.js")
    original = os.stat(script).st_mtime
    try:
        os.utime(script, (original + 60, original + 60))
        assert app_module.asset_version() != version
    finally:
        os.utime(script, (original, original))


def test_overdue_items_move_to_the_archive(client):
    """Abgelaufene Aufgaben belasten die To-do-Liste nicht mehr."""
    login_demo(client)
    data = client.get("/api/items").get_json()

    assert all(not i.get("expired") for i in data["active"])
    expired = [i for i in data["archive"] if i.get("expired")]
    assert expired, "die Demo enthaelt eine ueberfaellige Aufgabe"
    assert data["stats"]["overdue"] == len([i for i in expired if not i["done"]])

    # Sie sind nur verschoben, nicht verschwunden
    titles = {i["title"] for i in data["archive"]}
    assert "Arbeitsblatt 4: Quadratische Funktionen" in titles


def test_graded_task_is_not_counted_as_expired(client):
    """Eine bewertete Aufgabe mit altem Termin ist erledigt, nicht abgelaufen."""
    login_demo(client)
    data = client.get("/api/items").get_json()

    graded = next(i for i in data["archive"] if i["status"] == "graded")
    assert graded["expired"] is False
    assert data["stats"]["overdue"] == 1        # nur das offene Arbeitsblatt


def test_old_board_cards_do_not_appear(client):
    """Die alte Board-Karte der Demo taucht weder offen noch im Archiv auf."""
    login_demo(client)
    data = client.get("/api/items").get_json()

    titles = {i["title"] for i in data["active"]} | {i["title"] for i in data["archive"]}
    assert "Thema 2: Mittelalter" not in titles
    assert "Thema 7: Nationalsozialismus" in titles      # die aktuelle bleibt


def test_login_does_not_scan_topics(client, monkeypatch):
    """Der Login muss sofort antworten - Kursthemen kommen erst danach.

    Sie kosten bis zu 45 zusaetzliche Abrufe; laufen die beim Anmelden mit,
    steht der Nutzer minutenlang vor einem scheinbar toten Knopf.
    """
    import app as app_module
    from schulcloud.client import FetchResult

    gerufen = []

    class FakeClient:
        strategy = "api"
        jwt = "jwt-test"

        def __init__(self, *args, **kwargs):
            pass

        def login(self, username, password):
            return {"_id": "u1", "firstName": "Mia", "lastName": "Muster"}

        def fetch_all(self, scan_topics=True, **kwargs):
            gerufen.append(scan_topics)
            return FetchResult(courses=[], tasks=[])

        def logout(self):
            pass

    monkeypatch.setattr(app_module, "SchulCloudClient", FakeClient)

    res = client.post("/api/login", json={"username": "mia@example.org", "password": "geheim"})
    assert res.get_json()["ok"] is True
    assert gerufen == [False], "beim Anmelden duerfen die Kursthemen nicht geladen werden"

    client.post("/api/refresh")
    assert gerufen[-1] is app_module.SCAN_TOPICS, "danach gilt wieder die Einstellung"


def test_username_field_accepts_names_without_at_sign(client):
    """Die Schul-Cloud erlaubt Benutzernamen ohne @.

    Mit type="email" verweigert der Browser das Absenden kommentarlos - fuer
    den Nutzer "passiert dann einfach nichts".
    """
    import re

    body = client.get("/").get_data(as_text=True)
    feld = re.search(r'<input[^>]*id="username"[^>]*>', body).group(0)

    assert 'type="text"' in feld
    assert 'type="email"' not in feld
    assert 'inputmode="email"' in feld     # passende Tastatur bleibt erhalten
