"""Tests fuer Parsing, Dringlichkeit und Store (python -m pytest -q)."""

from datetime import datetime, timedelta, timezone

import pytest

from schulcloud import demo, parser
from schulcloud.store import Store

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_datetime_formats():
    assert parser.parse_datetime("2026-09-03T14:00:00Z").year == 2026
    assert parser.parse_datetime("03.09.2026, 14:00").hour == 14
    assert parser.parse_datetime("3.9.26").day == 3
    assert parser.parse_datetime(1757000000000) is not None
    assert parser.parse_datetime("") is None


def test_find_deadline_in_text():
    due = parser.find_deadline_in_text("Bitte Abgabe bis 12.09.2026 um 18:00 Uhr.")
    assert (due.day, due.month, due.hour) == (12, 9, 18)
    assert parser.find_deadline_in_text("kein Datum hier") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Klassenarbeit Nr. 2", True),
        ("Vokabeltest Unit 3", True),
        ("Lernkontrolle Zellbiologie", True),
        ("Arbeitsblatt Bruchrechnung", False),
    ],
)
def test_is_exam(text, expected):
    assert parser.is_exam(text) is expected


def test_urgency_levels():
    def item(hours):
        return {"due": (NOW + timedelta(hours=hours)).isoformat()}

    assert parser.urgency(item(-5), NOW)["level"] == "overdue"
    assert parser.urgency(item(6), NOW)["level"] == "critical"
    assert parser.urgency(item(36), NOW)["level"] == "warning"
    assert parser.urgency(item(100), NOW)["level"] == "soon"
    assert parser.urgency(item(500), NOW)["level"] == "later"
    assert parser.urgency({"due": None}, NOW)["level"] == "none"


def test_build_items_from_demo_data():
    items = parser.build_items(demo.demo_fetch(), "https://brandenburg.cloud", source="demo")
    by_id = {i["id"]: i for i in items}

    assert by_id["hw:hw1"]["course"] == "Mathematik 10b"
    assert by_id["hw:hw1"]["url"].endswith("/homework/hw1")
    assert by_id["hw:hw5"]["status"] == "graded"       # Note vorhanden
    assert by_id["hw:hw3"]["status"] == "submitted"    # Abgabe ohne Note
    assert by_id["hw:hw2"]["status"] == "open"
    assert by_id["hw:hw4"]["kind"] == "exam"           # "Klassenarbeit" im Titel

    # Kalender: nur Testankuendigungen, kein Sportfest
    assert "ev:ev1" in by_id and "ev:ev2" in by_id
    assert "ev:ev3" not in by_id

    # Kursthema mit Lernkontrolle wird als Termin erkannt
    assert by_id["ls:ls1"]["kind"] == "exam"


def test_sorting_puts_urgent_first():
    items = parser.build_items(demo.demo_fetch(), "https://brandenburg.cloud")
    ordered = parser.decorate_and_sort(items)
    levels = [i["urgency"]["level"] for i in ordered]
    assert levels == sorted(levels, key=lambda l: parser.SORT_LEVELS[l])
    assert ordered[0]["urgency"]["level"] == "overdue"


def test_strip_html_and_dedupe():
    assert parser.strip_html("<p>Hallo <b>Welt</b></p>") == "Hallo Welt"
    dup = [{"id": "a", "due": None}, {"id": "a", "due": "2026-09-03T10:00:00+02:00"}]
    assert parser.dedupe(dup)[0]["due"] is not None


def test_store_roundtrip(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    store.save_items([{"id": "hw:1", "title": "Test"}])
    assert store.cached_items()[0]["title"] == "Test"

    store.set_done("hw:1", True)
    assert store.states()["hw:1"]["done"] is True

    # Abgehakte Eintraege ueberleben das Aufraeumen des Caches
    assert store.prune_cache(set()) == 0
    store.set_done("hw:1", False)
    assert store.prune_cache(set()) == 1
    assert store.cached_items() == []

    store.set_meta("last_sync", "2026-09-01T10:00:00+00:00")
    assert store.get_meta("last_sync").startswith("2026-09-01")


def test_lan_report_and_qr():
    from schulcloud.netinfo import qr_lines, report

    text = report(5000, "127.0.0.1")
    assert "http://127.0.0.1:5000" in text and "WLAN" not in text

    lan = report(5000, "0.0.0.0")
    assert "Im gleichen WLAN" in lan

    code = qr_lines("http://192.168.0.5:5000")
    assert code and all(len(line) == len(code[0]) for line in code)


# ----------------------------------------------------------------------
# Ankuendigungen in Kursthemen
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,has_date,expected",
    [
        ("Hausaufgabe: Fragen 1-3 beantworten", False, True),   # eindeutig
        ("Am Freitag schreiben wir eine Klassenarbeit", False, True),
        ("Bitte Seite 42 bearbeiten bis 12.09.2026", True, True),  # schwach + Datum
        ("Bitte Seite 42 bearbeiten", False, False),               # schwach ohne Datum
        ("Hier finden Sie die Folien der Stunde", False, False),   # reines Material
        ("", False, False),
    ],
)
def test_is_announcement(text, has_date, expected):
    assert parser.is_announcement(text, has_date) is expected


def test_normalize_topic_reads_homework_from_course_topic():
    topic = {
        "id": "l1",
        "title": "Thema 7: Nationalsozialismus",
        "text": "<p>Hausaufgabe: Quellentext lesen, bis zum 12.09.2026.</p>",
        "course_name": "Geschichte 10b",
        "course_id": "c4",
        "url": "https://brandenburg.cloud/courses/c4/topics/l1",
    }
    item = parser.normalize_topic(topic, "https://brandenburg.cloud", "api")

    assert item["id"] == "tp:l1"
    assert item["kind"] == "homework"
    assert item["origin"] == "topic"          # Kennzeichnung in der Oberflaeche
    assert item["course"] == "Geschichte 10b"
    assert item["due"].startswith("2026-09-12")
    assert item["url"].endswith("/topics/l1")


def test_normalize_topic_marks_tests_as_exam_and_skips_material():
    exam = parser.normalize_topic(
        {"id": "l2", "title": "Stoffwiederholung",
         "text": "Der Vokabeltest findet am 20.09.2026 statt."},
        "https://x", "api")
    assert exam["kind"] == "exam"

    material = parser.normalize_topic(
        {"id": "l3", "title": "Linksammlung", "text": "Weiterführende Links zur Stunde."},
        "https://x", "api")
    assert material is None


def test_topic_does_not_duplicate_an_official_task():
    """Steht dieselbe Sache schon als Aufgabe, wird das Thema nicht doppelt gezeigt."""
    fetch = type("F", (), {
        "courses": [],
        "tasks": [{"id": "t1", "name": "Lesetagebuch Kapitel 1-4", "courseName": "Deutsch",
                   "dueDate": "2026-09-12T12:00:00Z", "status": {}}],
        "topics": [
            {"id": "l9", "title": "Lesetagebuch Kapitel 1-4",
             "text": "Abgabe bis 12.09.2026", "course_name": "Deutsch"},
            {"id": "l8", "title": "Vokabeltest Unit 4",
             "text": "Test am 15.09.2026", "course_name": "Englisch"},
        ],
    })()
    items = parser.build_items(fetch, "https://x")
    ids = [i["id"] for i in items]

    assert "hw:t1" in ids
    assert "tp:l9" not in ids     # Dublette
    assert "tp:l8" in ids         # eigenstaendige Ankuendigung
