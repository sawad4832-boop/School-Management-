"""Aufbereitung der Rohdaten zu einheitlichen Dashboard-Eintraegen.

Ein normalisierter Eintrag ("item") sieht so aus::

    {
      "id":        "hw:5f2b...",        # stabil ueber Aktualisierungen hinweg
      "kind":      "homework" | "exam" | "event",
      "title":     "Lineare Funktionen - AB 3",
      "course":    "Mathematik 9c",
      "course_id": "5f2a...",
      "color":     "#1DE9B6",
      "due":       "2026-09-03T14:00:00+02:00",  # ISO 8601 oder None
      "status":    "open" | "submitted" | "graded",
      "grade":     "13 Punkte" oder None,
      "url":       "https://brandenburg.cloud/homework/5f2b...",
      "teacher":   "Frau Muster",
      "description": "Kurztext ohne HTML",
      "source":    "api" | "html" | "extension" | "demo",
    }
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# Schluesselwoerter fuer Testankuendigungen / Leistungsnachweise.
EXAM_KEYWORDS = (
    "klassenarbeit", "klausur", "leistungskontrolle", "lernkontrolle", "lek",
    "test", "vokabeltest", "pruefung", "prüfung", "abfrage", "diktat",
    "referat", "praesentation", "präsentation", "vortrag", "kolloquium",
    "arbeit schreiben", "schulaufgabe", "ex ", "hausarbeit", "facharbeit",
)
EXAM_RE = re.compile("|".join(re.escape(k) for k in EXAM_KEYWORDS), re.IGNORECASE)

# Fristformulierungen in Freitexten ("Abgabe bis 12.09.2026, 18:00").
DEADLINE_RE = re.compile(
    r"(?:abgabe|frist|faellig|fällig|bis|termin|spaetestens|spätestens|\bam\b|\bden\b)\D{0,20}"
    r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?:\D{1,10}(\d{1,2})[:.](\d{2}))?",
    re.IGNORECASE,
)

BERLIN = timezone(timedelta(hours=2))  # Fallback, wenn keine Zone geliefert wird


# ----------------------------------------------------------------------
# Datums-Helfer
# ----------------------------------------------------------------------
def parse_datetime(value: Any) -> Optional[datetime]:
    """Erkennt ISO-8601, Millisekunden-Timestamps und deutsche Datumsangaben."""
    if value in (None, "", False):
        return None
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None

    iso = text.replace("Z", "+00:00")
    try:
        return _aware(datetime.fromisoformat(iso))
    except ValueError:
        pass

    match = re.search(
        r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?:\D{1,10}(\d{1,2})[:.](\d{2}))?", text
    )
    if match:
        day, month, year, hour, minute = match.groups()
        year_i = int(year)
        if year_i < 100:
            year_i += 2000
        try:
            return datetime(
                year_i, int(month), int(day), int(hour or 23), int(minute or 59), tzinfo=BERLIN
            )
        except ValueError:
            return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return _aware(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=BERLIN)


def find_deadline_in_text(text: str) -> Optional[datetime]:
    """Sucht eine Frist in einem Freitext (z.B. Kursbeschreibung)."""
    match = DEADLINE_RE.search(text or "")
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    year_i = int(year) + (2000 if int(year) < 100 else 0)
    try:
        return datetime(
            year_i, int(month), int(day), int(hour or 23), int(minute or 59), tzinfo=BERLIN
        )
    except ValueError:
        return None


def strip_html(value: Any, limit: int = 400) -> str:
    if not value:
        return ""
    text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def is_exam(*texts: Any) -> bool:
    return any(EXAM_RE.search(strip_html(t, 2000)) for t in texts if t)


# ----------------------------------------------------------------------
# Normalisierung
# ----------------------------------------------------------------------
def build_items(fetch, base_url: str, source: str = "api") -> list[dict]:
    """Erzeugt aus einem :class:`FetchResult` die Dashboard-Eintraege."""
    courses = {}
    for course in getattr(fetch, "courses", []) or []:
        cid = course.get("_id") or course.get("id")
        if cid:
            courses[cid] = course

    submissions = _index_submissions(getattr(fetch, "submissions", []) or [])

    items: list[dict] = []
    for hw in getattr(fetch, "homework", []) or []:
        item = normalize_homework(hw, courses, submissions, base_url, source)
        if item:
            items.append(item)

    for event in getattr(fetch, "events", []) or []:
        item = normalize_event(event, courses, base_url, source)
        if item:
            items.append(item)

    for lesson in getattr(fetch, "lessons", []) or []:
        item = normalize_lesson(lesson, courses, base_url, source)
        if item:
            items.append(item)

    return dedupe(items)


def _index_submissions(submissions: Iterable[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for sub in submissions:
        hw = sub.get("homeworkId")
        hw_id = hw.get("_id") if isinstance(hw, dict) else hw
        if hw_id:
            index[hw_id] = sub
    return index


def normalize_homework(
    hw: dict,
    courses: dict[str, dict],
    submissions: dict[str, dict],
    base_url: str,
    source: str,
) -> Optional[dict]:
    hw_id = hw.get("_id") or hw.get("id")
    title = (hw.get("name") or hw.get("title") or "").strip() or "Aufgabe ohne Titel"
    if not hw_id:
        hw_id = _fallback_id(title, hw.get("dueDate") or hw.get("dueDateText"))

    course = _resolve_course(hw.get("courseId") or hw.get("course"), courses)
    course_name = (course or {}).get("name") or hw.get("courseName") or "Ohne Kurs"

    description = strip_html(hw.get("description"))
    due = parse_datetime(hw.get("dueDate") or hw.get("dueDateText") or hw.get("due"))
    if due is None:
        due = find_deadline_in_text(f"{title} {description}")

    submission = submissions.get(hw_id) or hw.get("submission")
    status, grade = _submission_status(submission, hw)

    return {
        "id": f"hw:{hw_id}",
        "kind": "exam" if is_exam(title, description) else "homework",
        "title": title,
        "course": course_name,
        "course_id": (course or {}).get("_id") or _id_of(hw.get("courseId")),
        "color": (course or {}).get("color"),
        "due": due.isoformat() if due else None,
        "status": status,
        "grade": grade,
        "url": urljoin(base_url + "/", f"homework/{hw_id}"),
        "teacher": _person_name(hw.get("teacherId")),
        "description": description,
        "source": hw.get("_source") or source,
    }


def normalize_event(event: dict, courses: dict[str, dict], base_url: str, source: str) -> Optional[dict]:
    attributes = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
    title = (
        event.get("title")
        or event.get("summary")
        or attributes.get("summary")
        or ""
    ).strip()
    if not title:
        return None

    description = strip_html(event.get("description") or attributes.get("description"))
    start = parse_datetime(
        event.get("start")
        or event.get("startDate")
        or attributes.get("dtstart")
        or event.get("dtstart")
    )
    if start is None:
        start = find_deadline_in_text(f"{title} {description}")
    if start is None:
        return None

    exam = is_exam(title, description)
    if not exam:
        # Normale Stundenplan-Termine interessieren im To-do-Dashboard nicht.
        return None

    event_id = event.get("_id") or event.get("id") or _fallback_id(title, start.isoformat())
    course = _resolve_course(event.get("courseId") or attributes.get("x-sc-courseId"), courses)
    return {
        "id": f"ev:{event_id}",
        "kind": "exam",
        "title": title,
        "course": (course or {}).get("name") or event.get("courseName") or "Kalender",
        "course_id": (course or {}).get("_id"),
        "color": (course or {}).get("color"),
        "due": start.isoformat(),
        "status": "open",
        "grade": None,
        "url": urljoin(base_url + "/", "calendar"),
        "teacher": "",
        "description": description,
        "source": source,
    }


def normalize_lesson(lesson: dict, courses: dict[str, dict], base_url: str, source: str) -> Optional[dict]:
    """Erkennt Testankuendigungen in Kursthemen (Unterrichtsstunden)."""
    title = (lesson.get("name") or lesson.get("title") or "").strip()
    text_blocks = [title]
    for content in lesson.get("contents") or []:
        if isinstance(content, dict):
            text_blocks.append(content.get("title") or "")
            component = content.get("content")
            if isinstance(component, dict):
                text_blocks.append(strip_html(component.get("text"), 2000))
    joined = " ".join(b for b in text_blocks if b)
    if not joined or not is_exam(joined):
        return None

    due = find_deadline_in_text(joined) or parse_datetime(lesson.get("date"))
    if due is None:
        return None

    lesson_id = lesson.get("_id") or lesson.get("id") or _fallback_id(title, due.isoformat())
    course = _resolve_course(lesson.get("courseId"), courses)
    return {
        "id": f"ls:{lesson_id}",
        "kind": "exam",
        "title": title or "Angekündigter Leistungsnachweis",
        "course": (course or {}).get("name") or "Kursthema",
        "course_id": (course or {}).get("_id"),
        "color": (course or {}).get("color"),
        "due": due.isoformat(),
        "status": "open",
        "grade": None,
        "url": urljoin(base_url + "/", f"courses/{_id_of(lesson.get('courseId')) or ''}"),
        "teacher": "",
        "description": strip_html(joined),
        "source": source,
    }


def _submission_status(submission: Any, hw: dict) -> tuple[str, Optional[str]]:
    if isinstance(submission, dict) and submission:
        grade = submission.get("grade")
        comment = strip_html(submission.get("gradeComment"), 200)
        if grade not in (None, "") or comment:
            label = f"{grade}" if grade not in (None, "") else comment
            return "graded", label
        return "submitted", None

    # HTML-Fallback: Statuswoerter aus dem Kartentext.
    text = (hw.get("statusText") or "").lower()
    if "bewertet" in text or "note" in text:
        return "graded", None
    if "abgegeben" in text or "eingereicht" in text:
        return "submitted", None
    return "open", None


def _resolve_course(course_ref: Any, courses: dict[str, dict]) -> Optional[dict]:
    if isinstance(course_ref, dict):
        cid = course_ref.get("_id") or course_ref.get("id")
        merged = dict(courses.get(cid, {}))
        merged.update({k: v for k, v in course_ref.items() if v})
        return merged or None
    if isinstance(course_ref, str):
        return courses.get(course_ref) or {"_id": course_ref}
    return None


def _id_of(ref: Any) -> Optional[str]:
    if isinstance(ref, dict):
        return ref.get("_id") or ref.get("id")
    return ref if isinstance(ref, str) else None


def _person_name(ref: Any) -> str:
    if isinstance(ref, dict):
        name = " ".join(x for x in (ref.get("firstName"), ref.get("lastName")) if x)
        return name or ref.get("displayName") or ""
    return ""


def _fallback_id(*parts: Any) -> str:
    import hashlib

    raw = "|".join(str(p) for p in parts if p)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def dedupe(items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for item in items:
        existing = seen.get(item["id"])
        if existing is None or (existing.get("due") is None and item.get("due")):
            seen[item["id"]] = item
    return list(seen.values())


# ----------------------------------------------------------------------
# Dringlichkeit / Sortierung
# ----------------------------------------------------------------------
def urgency(item: dict, now: Optional[datetime] = None) -> dict[str, Any]:
    """Berechnet Restzeit und Dringlichkeitsstufe eines Eintrags."""
    now = now or datetime.now(timezone.utc)
    due = parse_datetime(item.get("due"))
    if due is None:
        return {"level": "none", "hours_left": None, "label": "Ohne Termin"}

    delta = due - now
    hours = delta.total_seconds() / 3600
    if hours < 0:
        level = "overdue"
    elif hours <= 24:
        level = "critical"
    elif hours <= 48:
        level = "warning"
    elif hours <= 24 * 7:
        level = "soon"
    else:
        level = "later"
    return {"level": level, "hours_left": round(hours, 2), "label": humanize(delta)}


def humanize(delta: timedelta) -> str:
    seconds = delta.total_seconds()
    overdue = seconds < 0
    seconds = abs(seconds)
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60

    if days >= 1:
        text = f"{days} Tag{'e' if days != 1 else ''}"
        if days < 3 and hours:
            text += f", {hours} Std."
    elif hours >= 1:
        text = f"{hours} Std. {minutes} Min."
    else:
        text = f"{minutes} Min."
    return f"seit {text} überfällig" if overdue else f"in {text}"


SORT_LEVELS = {"overdue": 0, "critical": 1, "warning": 2, "soon": 3, "later": 4, "none": 5}


def decorate_and_sort(items: list[dict], now: Optional[datetime] = None) -> list[dict]:
    """Ergaenzt Dringlichkeitsinfos und sortiert nach Faelligkeit."""
    now = now or datetime.now(timezone.utc)
    decorated = []
    for item in items:
        enriched = dict(item)
        enriched["urgency"] = urgency(item, now)
        decorated.append(enriched)

    def key(entry: dict):
        due = parse_datetime(entry.get("due"))
        return (
            SORT_LEVELS.get(entry["urgency"]["level"], 9),
            due.timestamp() if due else float("inf"),
            entry.get("title", ""),
        )

    return sorted(decorated, key=key)
