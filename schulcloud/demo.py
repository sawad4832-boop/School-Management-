"""Demo-Datensatz - erlaubt das Ausprobieren der Oberflaeche ohne Login.

Aktivierung: ``SC_DEMO=1`` in der ``.env`` oder Login mit Benutzer ``demo``
und Passwort ``demo``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .client import FetchResult

BERLIN = timezone(timedelta(hours=2))


def _in(hours: float) -> str:
    return (datetime.now(BERLIN) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def demo_user() -> dict:
    return {"_id": "demo-user", "firstName": "Demo", "lastName": "Schülerin", "roles": ["student"]}


def demo_fetch() -> FetchResult:
    courses = [
        {"_id": "c1", "name": "Mathematik 10b", "color": "#1DE9B6"},
        {"_id": "c2", "name": "Deutsch 10b", "color": "#FFC107"},
        {"_id": "c3", "name": "Biologie 10b", "color": "#4CAF50"},
        {"_id": "c4", "name": "Geschichte 10b", "color": "#F44336"},
        {"_id": "c5", "name": "Englisch 10b", "color": "#03A9F4"},
    ]
    homework = [
        {
            "_id": "hw1",
            "name": "Arbeitsblatt 4: Quadratische Funktionen",
            "courseId": courses[0],
            "dueDate": _in(-6),
            "description": "<p>Aufgaben 1–5 auf dem Arbeitsblatt lösen und hochladen.</p>",
            "teacherId": {"firstName": "Herr", "lastName": "Neumann"},
        },
        {
            "_id": "hw2",
            "name": "Gedichtanalyse 'Der Panther'",
            "courseId": courses[1],
            "dueDate": _in(14),
            "description": "<p>Analyse (mind. 500 Wörter) als PDF abgeben.</p>",
            "teacherId": {"firstName": "Frau", "lastName": "Weber"},
        },
        {
            "_id": "hw3",
            "name": "Protokoll Fotosynthese-Versuch",
            "courseId": courses[2],
            "dueDate": _in(40),
            "description": "<p>Versuchsprotokoll nach Vorlage.</p>",
        },
        {
            "_id": "hw4",
            "name": "Vorbereitung Klassenarbeit Nr. 1",
            "courseId": courses[3],
            "dueDate": _in(96),
            "description": "<p>Die Klassenarbeit umfasst die Themen Kaiserreich und Weimarer Republik.</p>",
        },
        {
            "_id": "hw5",
            "name": "Vocabulary Unit 3 - Wordlist",
            "courseId": courses[4],
            "dueDate": _in(-30),
            "description": "<p>Vokabeln lernen, Test folgt.</p>",
        },
        {
            "_id": "hw6",
            "name": "Lesetagebuch Kapitel 1-4",
            "courseId": courses[1],
            "dueDate": _in(240),
            "description": "<p>Einträge zu den ersten vier Kapiteln.</p>",
        },
    ]
    submissions = [
        {"_id": "s1", "homeworkId": "hw5", "grade": 14, "gradeComment": "<p>Sehr gut!</p>"},
        {"_id": "s2", "homeworkId": "hw3"},
    ]
    events = [
        {
            "_id": "ev1",
            "title": "Klassenarbeit Mathematik (Kapitel 2)",
            "start": _in(120),
            "description": "Taschenrechner und Formelsammlung mitbringen.",
            "courseId": "c1",
        },
        {
            "_id": "ev2",
            "title": "Vokabeltest Englisch Unit 3",
            "start": _in(30),
            "description": "Unit 3, alle Wörter.",
            "courseId": "c5",
        },
        {
            "_id": "ev3",
            "title": "Sportfest",
            "start": _in(200),
            "description": "Kein Unterricht.",
        },
    ]
    lessons = [
        {
            "_id": "ls1",
            "name": "Wiederholung: Zellbiologie",
            "courseId": "c3",
            "contents": [
                {
                    "title": "Hinweis",
                    "content": {
                        "text": "<p>Die Lernkontrolle findet am "
                        + (datetime.now(BERLIN) + timedelta(days=9)).strftime("%d.%m.%Y")
                        + " statt.</p>"
                    },
                }
            ],
        }
    ]
    return FetchResult(
        courses=courses,
        homework=homework,
        submissions=submissions,
        events=events,
        lessons=lessons,
        sources={"courses": "demo", "homework": "demo", "calendar": "demo"},
    )
