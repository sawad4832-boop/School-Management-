"""Tests fuer Login-Strategien und die Aufbereitung der v3-Antworten.

Es werden keine echten Netzwerkaufrufe gemacht: die requests-Session wird durch
ein Fake-Objekt ersetzt, das vorbereitete Antworten liefert.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from schulcloud import parser
from schulcloud.client import AuthError, SchulCloudClient, _extract_csrf, _v3_list

LOGIN_HTML = '''
<form method="post" action="/login" class="login-form">
  <input type="hidden" name="redirect" value="" />
  <input type="text" name="username" required>
  <input type="password" name="password" maxlength="72" required>
  <input type="hidden" data-force-value="true" name="_csrf" value="aenTTdt2-OT8cpMph">
</form>
'''


class FakeResponse:
    def __init__(self, status=200, json_data=None, text="", url="https://x/", cookies=None):
        self.status_code = status
        self._json = json_data
        self.text = text
        self.url = url
        self.headers = {"Content-Type": "application/json" if json_data is not None else "text/html"}

    def json(self):
        if self._json is None:
            raise ValueError("keine JSON-Antwort")
        return self._json


class FakeSession:
    """Minimaler Ersatz fuer requests.Session mit programmierbaren Antworten."""

    def __init__(self, responses: dict):
        self.responses = responses     # (methode, url-fragment) -> FakeResponse
        self.headers: dict = {}
        self.cookies = FakeCookies()
        self.verify = True
        self.calls: list[tuple[str, str]] = []

    def _lookup(self, method, url):
        self.calls.append((method, url))
        for (m, fragment), response in self.responses.items():
            if m == method and fragment in url:
                return response
        return FakeResponse(status=404, text="not found", url=url)

    def get(self, url, params=None, timeout=None, **kw):
        return self._lookup("GET", url)

    def post(self, url, json=None, data=None, timeout=None, **kw):
        return self._lookup("POST", url)


class FakeCookies(dict):
    def get(self, name, default=None):
        return dict.get(self, name, default)

    def set(self, name, value, **kw):
        self[name] = value

    def clear(self):
        dict.clear(self)


def make_client(responses) -> SchulCloudClient:
    client = SchulCloudClient("https://brandenburg.cloud")
    client.session = FakeSession(responses)
    return client


ME_RESPONSE = FakeResponse(json_data={
    "user": {"id": "u1", "firstName": "Mia", "lastName": "Muster"},
    "school": {"id": "s1", "name": "Beispielschule"},
    "roles": [{"id": "r1", "name": "student"}],
})


def test_extract_csrf_from_login_form():
    assert _extract_csrf(LOGIN_HTML) == "aenTTdt2-OT8cpMph"
    assert _extract_csrf("<form></form>") is None


def test_v3_list_unwraps_data_envelope():
    assert _v3_list({"data": [{"id": "a"}], "total": 1}) == [{"id": "a"}]
    assert _v3_list([{"id": "b"}]) == [{"id": "b"}]
    assert _v3_list({"foo": 1}) == []


def test_api_login_uses_v3_endpoint_and_sets_bearer():
    client = make_client({
        ("POST", "/api/v3/authentication/local"): FakeResponse(json_data={"accessToken": "jwt-123"}),
        ("GET", "/api/v3/me"): ME_RESPONSE,
    })
    user = client.login("mia@example.org", "geheim")

    assert client.strategy == "api"
    assert client.session.headers["Authorization"] == "Bearer jwt-123"
    assert user["firstName"] == "Mia"
    assert user["school"] == "Beispielschule"


def test_wrong_password_stops_immediately():
    """Bei 401 darf nicht auch noch der Formular-Login versucht werden."""
    client = make_client({
        ("POST", "/api/v3/authentication/local"): FakeResponse(status=401, json_data={"code": 401}),
    })
    with pytest.raises(AuthError, match="nicht akzeptiert"):
        client.login("mia@example.org", "falsch")

    assert not any(url.endswith("/login") for _, url in client.session.calls)


def test_form_login_sends_csrf_token_and_takes_cookie():
    session_responses = {
        ("POST", "/api/v3/authentication/local"): FakeResponse(status=500),
        ("GET", "/login"): FakeResponse(text=LOGIN_HTML, url="https://brandenburg.cloud/login"),
        ("POST", "/login"): FakeResponse(text="ok", url="https://brandenburg.cloud/dashboard"),
        ("GET", "/api/v3/me"): ME_RESPONSE,
    }
    client = make_client(session_responses)
    client.session.cookies.set("jwt", "cookie-jwt")

    client.login("mia@example.org", "geheim")
    assert client.strategy == "form"
    assert client.jwt == "cookie-jwt"


def test_fetch_all_reads_v3_tasks():
    tasks_open = {"data": [{
        "id": "t1", "name": "Arbeitsblatt 4", "courseName": "Mathematik 10b",
        "dueDate": "2026-09-03T12:00:00.000Z",
        "status": {"submitted": 0, "graded": 0, "isDraft": False, "isFinished": False},
    }], "total": 1}
    tasks_done = {"data": [{
        "id": "t2", "name": "Lesetagebuch", "courseName": "Deutsch 10b",
        "dueDate": "2026-08-20T12:00:00.000Z",
        "status": {"submitted": 1, "graded": 1, "isDraft": False, "isFinished": True},
    }], "total": 1}
    courses = {"data": [{"id": "c1", "name": "Mathematik 10b", "displayColor": "#1DE9B6"}]}

    client = make_client({
        ("GET", "/api/v3/courses"): FakeResponse(json_data=courses),
        ("GET", "/api/v3/tasks/finished"): FakeResponse(json_data=tasks_done),
        ("GET", "/api/v3/tasks"): FakeResponse(json_data=tasks_open),
        ("GET", "/api/v3/news"): FakeResponse(json_data={"data": []}),
    })
    result = client.fetch_all()

    assert result.sources["tasks"] == "api/v3"
    assert result.sources["courses"] == "api/v3"
    assert [t["id"] for t in result.tasks] == ["t1", "t2"]
    assert result.tasks[1]["_finished"] is True
    assert result.courses[0] == {"_id": "c1", "name": "Mathematik 10b", "color": "#1DE9B6"}

    items = {i["id"]: i for i in parser.build_items(result, "https://brandenburg.cloud")}
    assert items["hw:t1"]["status"] == "open"
    assert items["hw:t2"]["status"] == "graded"
    assert items["hw:t2"]["finished"] is True


def test_expired_session_raises_auth_error():
    client = make_client({("GET", "/api/v3/tasks"): FakeResponse(status=401)})
    with pytest.raises(AuthError, match="abgelaufen"):
        client.fetch_all()


def test_normalize_task_skips_drafts():
    draft = {"id": "d1", "name": "Entwurf", "status": {"isDraft": True}}
    assert parser.normalize_task(draft, {}, "https://x", "api") is None


def test_normalize_news_only_keeps_exams_with_date():
    exam = {"id": "n1", "title": "Klassenarbeit Mathematik",
            "content": "<p>Die Arbeit wird am 15.09.2026 geschrieben.</p>",
            "target": {"id": "c1", "name": "Mathematik 10b"}}
    item = parser.normalize_news(exam, {}, "https://x", "api")
    assert item["kind"] == "exam" and item["due"].startswith("2026-09-15")

    assert parser.normalize_news(
        {"id": "n2", "title": "Mensa geschlossen", "content": "<p>Am 15.09.2026.</p>"},
        {}, "https://x", "api") is None
    assert parser.normalize_news(
        {"id": "n3", "title": "Klassenarbeit", "content": "<p>Termin folgt.</p>"},
        {}, "https://x", "api") is None


# ----------------------------------------------------------------------
# Kursthemen einsammeln (Board + klassische Themen)
# ----------------------------------------------------------------------
COURSE = {"_id": "c1", "name": "Geschichte 10b", "color": "#F44336"}

BOARD = {
    "elements": [
        {"type": "lesson", "content": {"id": "l1", "name": "Thema 7: Weimarer Republik"}},
        {"type": "column-board", "content": {"id": "b1", "title": "Kursboard"}},
    ]
}
COLUMN_BOARD = {"id": "b1", "columns": [{"id": "col1", "cards": [{"cardId": "card1"}]}]}
_BALD = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%d.%m.%Y")
CARDS = {"data": [{
    "id": "card1",
    "title": "Ankündigung",
    "elements": [{"type": "richText",
                  "content": {"text": f"<p>Klassenarbeit am {_BALD}</p>"}}],
}]}
TOPIC_HTML = ("<html><body><main>Hausaufgabe: Quellentext lesen bis "
              f"{_BALD}</main></body></html>")


def test_fetch_course_topics_reads_lessons_and_boards():
    client = make_client({
        ("GET", "/course-rooms/c1/board"): FakeResponse(json_data=BOARD),
        ("GET", "/api/v3/boards/b1"): FakeResponse(json_data=COLUMN_BOARD),
        ("GET", "/api/v3/cards"): FakeResponse(json_data=CARDS),
        ("GET", "/courses/c1/topics/l1"): FakeResponse(text=TOPIC_HTML, url="https://x/courses/c1/topics/l1"),
    })
    topics = client.fetch_course_topics([COURSE])
    by_id = {t["id"]: t for t in topics}

    assert "Hausaufgabe" in by_id["l1"]["text"]
    assert by_id["l1"]["course_name"] == "Geschichte 10b"
    assert by_id["l1"]["url"].endswith("/courses/c1/topics/l1")
    assert "Klassenarbeit" in by_id["card1"]["text"]

    items = {i["id"]: i for i in parser.build_items(
        type("F", (), {"topics": topics})(), "https://brandenburg.cloud")}
    assert items["tp:l1"]["kind"] == "homework"
    assert items["tp:card1"]["kind"] == "exam"


def test_fetch_course_topics_respects_request_budget():
    """Viele Kurse duerfen die Aktualisierung nicht ins Endlose ziehen."""
    client = make_client({("GET", "/board"): FakeResponse(json_data={"elements": []})})
    courses = [{"_id": f"c{n}", "name": f"Kurs {n}"} for n in range(30)]

    client.fetch_course_topics(courses, max_courses=12, max_requests=5)
    assert len(client.session.calls) <= 5


def test_fetch_all_survives_broken_topics():
    """Ein Fehler beim Lesen der Themen darf die Aufgabenliste nicht kippen."""
    client = make_client({
        # Reihenfolge zaehlt: der laengere Pfad muss zuerst geprueft werden.
        ("GET", "/api/v3/tasks/finished"): FakeResponse(json_data={"data": []}),
        ("GET", "/api/v3/tasks"): FakeResponse(json_data={"data": [
            {"id": "t1", "name": "Arbeitsblatt", "courseName": "Mathe", "status": {}}]}),
        ("GET", "/api/v3/courses"): FakeResponse(json_data={"data": [{"id": "c1", "name": "Mathe"}]}),
    })
    result = client.fetch_all()          # Board-Abrufe laufen ins Leere (404)

    assert [t["id"] for t in result.tasks] == ["t1"]
    assert result.topics == []


def test_topics_are_cached_between_refreshes():
    """Der zweite Abruf kurz danach darf die Kurse nicht erneut durchgehen."""
    client = make_client({
        ("GET", "/api/v3/courses"): FakeResponse(json_data={"data": [{"id": "c1", "name": "Mathe"}]}),
        ("GET", "/course-rooms/c1/board"): FakeResponse(json_data=BOARD),
        ("GET", "/api/v3/boards/b1"): FakeResponse(json_data=COLUMN_BOARD),
        ("GET", "/api/v3/cards"): FakeResponse(json_data=CARDS),
        ("GET", "/courses/c1/topics/l1"): FakeResponse(text=TOPIC_HTML, url="https://x/t"),
    })
    first = client.fetch_all()
    assert first.sources["topics"] == "api/v3+html"
    calls_after_first = len(client.session.calls)

    second = client.fetch_all()
    assert second.sources["topics"] == "zwischenspeicher"
    assert [t["id"] for t in second.topics] == [t["id"] for t in first.topics]

    # Nach Ablauf der Frist wird wieder frisch gelesen
    third = client.fetch_all(topics_ttl=0)
    assert third.sources["topics"] == "api/v3+html"
    assert len(client.session.calls) > calls_after_first


def test_topic_scan_stops_when_time_runs_out(monkeypatch):
    """Antwortet die Schul-Cloud langsam, bricht der Durchlauf ab."""
    import schulcloud.client as client_module

    uhr = {"jetzt": 1000.0}
    monkeypatch.setattr(client_module.time, "monotonic", lambda: uhr["jetzt"])

    client = make_client({("GET", "/board"): FakeResponse(json_data={"elements": []})})
    original = client.session.get

    def langsam(*args, **kwargs):
        uhr["jetzt"] += 5          # jeder Abruf kostet 5 Sekunden
        return original(*args, **kwargs)

    client.session.get = langsam
    courses = [{"_id": f"c{n}", "name": f"Kurs {n}"} for n in range(12)]

    client.fetch_course_topics(courses, max_requests=45, max_seconds=20)
    assert len(client.session.calls) <= 5, "nach 20 Sekunden ist Schluss"


def test_topic_requests_use_a_short_timeout():
    """Ein haengender Abruf darf nicht 25 Sekunden blockieren."""
    from schulcloud.client import TOPIC_TIMEOUT

    assert TOPIC_TIMEOUT <= 10

    gesehen = []

    class Mitschrift(FakeSession):
        def get(self, url, params=None, timeout=None, **kw):
            gesehen.append(timeout)
            return self._lookup("GET", url)

    client = SchulCloudClient("https://brandenburg.cloud")
    client.session = Mitschrift({("GET", "/board"): FakeResponse(json_data={"elements": []})})
    client.fetch_course_topics([{"_id": "c1", "name": "Kurs"}])

    assert gesehen and all(t == TOPIC_TIMEOUT for t in gesehen)
