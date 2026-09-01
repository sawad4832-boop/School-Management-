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
    assert data["active"][0]["urgency"]["level"] == "overdue"
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
