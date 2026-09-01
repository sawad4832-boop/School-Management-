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
