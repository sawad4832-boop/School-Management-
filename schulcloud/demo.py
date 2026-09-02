"""Demo-Datensatz – erlaubt das Ausprobieren der Oberflaeche ohne Login.

Die Struktur entspricht den echten Antworten der Schul-Cloud
(``/api/v3/tasks``, ``/api/v3/courses``, Kalender, Kursthemen).

Aktivierung: ``SC_DEMO=1`` in der ``.env`` oder Login mit Benutzer ``demo``
und Passwort ``demo``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .client import FetchResult

BERLIN = timezone(timedelta(hours=2))


def _in(hours: float) -> str:
    return (datetime.now(BERLIN) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _status(submitted: int = 0, graded: int = 0, finished: bool = False) -> dict:
    return {
        "submitted": submitted,
        "maxSubmissions": 1,
        "graded": graded,
        "isDraft": False,
        "isSubstitutionTeacher": False,
        "isFinished": finished,
    }


def demo_user() -> dict:
    return {
        "_id": "demo-user",
        "firstName": "Demo",
        "lastName": "Schülerin",
        "school": "Beispielschule",
        "roles": ["student"],
    }


def demo_fetch() -> FetchResult:
    courses = [
        {"_id": "c1", "name": "Mathematik 10b", "color": "#1DE9B6"},
        {"_id": "c2", "name": "Deutsch 10b", "color": "#FFC107"},
        {"_id": "c3", "name": "Biologie 10b", "color": "#4CAF50"},
        {"_id": "c4", "name": "Geschichte 10b", "color": "#F44336"},
        {"_id": "c5", "name": "Englisch 10b", "color": "#03A9F4"},
    ]
    tasks = [
        {
            "id": "hw1",
            "name": "Arbeitsblatt 4: Quadratische Funktionen",
            "courseName": "Mathematik 10b",
            "courseId": "c1",
            "dueDate": _in(-6),
            "description": "<p>Aufgaben 1–5 auf dem Arbeitsblatt lösen und hochladen.</p>",
            "displayColor": "#1DE9B6",
            "createdBy": "Herr Neumann",
            "status": _status(),
        },
        {
            "id": "hw2",
            "name": "Gedichtanalyse 'Der Panther'",
            "courseName": "Deutsch 10b",
            "courseId": "c2",
            "dueDate": _in(14),
            "description": "<p>Analyse (mind. 500 Wörter) als PDF abgeben.</p>",
            "createdBy": "Frau Weber",
            "status": _status(),
        },
        {
            "id": "hw3",
            "name": "Protokoll Fotosynthese-Versuch",
            "courseName": "Biologie 10b",
            "courseId": "c3",
            "dueDate": _in(40),
            "description": "<p>Versuchsprotokoll nach Vorlage.</p>",
            "status": _status(submitted=1),
        },
        {
            "id": "hw4",
            "name": "Vorbereitung Klassenarbeit Nr. 1",
            "courseName": "Geschichte 10b",
            "courseId": "c4",
            "dueDate": _in(96),
            "description": "<p>Die Klassenarbeit umfasst die Themen Kaiserreich und Weimarer Republik.</p>",
            "status": _status(),
        },
        {
            "id": "hw6",
            "name": "Lesetagebuch Kapitel 1-4",
            "courseName": "Deutsch 10b",
            "courseId": "c2",
            "dueDate": _in(240),
            "description": "<p>Einträge zu den ersten vier Kapiteln.</p>",
            "status": _status(),
        },
        {
            "id": "hw5",
            "name": "Vocabulary Unit 3 – Wordlist",
            "courseName": "Englisch 10b",
            "courseId": "c5",
            "dueDate": _in(-30),
            "description": "<p>Vokabeln lernen, Test folgt.</p>",
            "status": _status(submitted=1, graded=1),
            "_finished": True,
        },
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
    news = [
        {
            "id": "nw1",
            "title": "Praktikumsmappe abgeben",
            "content": "<p>Die Facharbeit ist spätestens bis "
            + (datetime.now(BERLIN) + timedelta(days=12)).strftime("%d.%m.%Y")
            + " im Sekretariat abzugeben.</p>",
            "target": {"id": "c4", "name": "Geschichte 10b"},
        },
        {
            "id": "nw2",
            "title": "Mensa bleibt Freitag geschlossen",
            "content": "<p>Bitte Verpflegung mitbringen.</p>",
        },
    ]
    topics = [
        {
            "id": "tp1",
            "title": "Thema 7: Nationalsozialismus",
            "text": "Hausaufgabe: Quellentext S. 112 lesen und die Fragen 1–3 schriftlich "
                    "beantworten, bis zum "
                    + (datetime.now(BERLIN) + timedelta(days=2)).strftime("%d.%m.%Y") + ".",
            "course_id": "c4",
            "course_name": "Geschichte 10b",
            "color": "#F44336",
            "url": "https://brandenburg.cloud/courses/c4/topics/tp1",
        },
        {
            "id": "tp2",
            "title": "Organische Chemie – Überblick",
            "text": "Der nächste Test wird am "
                    + (datetime.now(BERLIN) + timedelta(days=6)).strftime("%d.%m.%Y")
                    + " geschrieben. Inhalt: Alkane und Alkene.",
            "course_id": "c3",
            "course_name": "Biologie 10b",
            "color": "#4CAF50",
            "url": "https://brandenburg.cloud/courses/c3/topics/tp2",
        },
        {
            "id": "tp3",
            "title": "Materialsammlung",
            "text": "Hier finden Sie die Folien der letzten Stunde sowie weiterführende Links.",
            "course_id": "c1",
            "course_name": "Mathematik 10b",
            "url": "https://brandenburg.cloud/courses/c1/topics/tp3",
        },
    ]
    return FetchResult(
        courses=courses,
        tasks=tasks,
        topics=topics,
        events=events,
        lessons=lessons,
        news=news,
        sources={"courses": "demo", "tasks": "demo", "calendar": "demo",
                 "news": "demo", "topics": "demo"},
    )
