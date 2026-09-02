"""Login-Wrapper und Datenabruf fuer die Schul-Cloud Brandenburg.

Eine oeffentlich dokumentierte API gibt es nicht. Die tatsaechlich vorhandenen
Endpunkte wurden gegen ``https://brandenburg.cloud`` geprueft:

===================================  =========================================
Endpunkt                             Zweck
===================================  =========================================
``POST /api/v3/authentication/local``  Login mit Nutzername/Passwort -> JWT
``GET  /api/v3/me``                    angemeldeter Nutzer
``GET  /api/v3/tasks``                 offene Aufgaben ("Aufgaben")
``GET  /api/v3/tasks/finished``        erledigte/bewertete Aufgaben
``GET  /api/v3/courses``               Kursuebersicht
``GET  /api/v3/news``                  Ankuendigungen (Quelle fuer Testtermine)
``POST /login``                        Formular-Login, benoetigt ``_csrf``
===================================  =========================================

Die alten Feathers-Services (``/api/v1/homework``, ``/submissions``, ``/lessons``)
sind auf dieser Instanz **nicht** erreichbar; sie werden nur noch als Fallback
fuer abweichende Instanzen versucht.

Login-Strategien in dieser Reihenfolge:

1. ``api``    - JWT ueber ``/api/v3/authentication/local`` (bzw. ``/authentication``
                bei aelteren Instanzen), danach ``Authorization: Bearer <jwt>``.
2. ``form``   - Formular-Login: ``GET /login`` fuer das CSRF-Token, dann
                ``POST /login``; die Plattform setzt das ``jwt``-Cookie.
3. ``cookie`` - ein vorhandenes JWT wird uebernommen (Browser-Erweiterung,
                Zwei-Faktor-Anmeldung, Single-Sign-on).

Fuer jede Ressource wird zuerst JSON versucht; scheitert das, greift die
HTML-Scraper-Logik auf den ausgelieferten Seiten.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 SchulCloudDashboard/1.0"
)

# Praefixe der aelteren Feathers-API (Fallback fuer abweichende Instanzen).
LEGACY_PREFIXES = ("/api/v1", "/api", "")

# Kurzes Zeitlimit fuer die vielen kleinen Themen-Abrufe: ein haengender
# Abruf darf den Durchlauf nicht blockieren.
TOPIC_TIMEOUT = 8


class SchulCloudError(RuntimeError):
    """Allgemeiner Fehler beim Zugriff auf die Schul-Cloud."""


class AuthError(SchulCloudError):
    """Login fehlgeschlagen oder Session abgelaufen."""


@dataclass
class FetchResult:
    """Rohdaten eines Abrufs inklusive Diagnose-Informationen."""

    courses: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)        # /api/v3/tasks
    homework: list[dict] = field(default_factory=list)     # Legacy/HTML
    submissions: list[dict] = field(default_factory=list)  # Legacy
    events: list[dict] = field(default_factory=list)       # Kalender
    lessons: list[dict] = field(default_factory=list)      # Kursthemen (alt)
    topics: list[dict] = field(default_factory=list)       # Kursthemen (Board/Text)
    news: list[dict] = field(default_factory=list)         # Ankuendigungen
    sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class SchulCloudClient:
    """Kapselt Login und Datenabruf einer Schul-Cloud-Instanz."""

    def __init__(
        self,
        base_url: str = "https://brandenburg.cloud",
        api_url: Optional[str] = None,
        timeout: int = 25,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_url = (api_url or "").rstrip("/") or None
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.8"}
        )
        self.session.verify = verify_tls
        self.jwt: Optional[str] = None
        self.user: dict[str, Any] = {}
        self.strategy: str = "none"
        self._legacy_root: Optional[str] = None
        # Kursthemen kosten viele Abrufe; sie werden zwischengespeichert.
        self._topics: list[dict] = []
        self._topics_read: float = 0.0

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> dict[str, Any]:
        """Meldet sich an: erst ueber die JSON-API, dann ueber das Formular.

        Beide Wege werden auch dann versucht, wenn der erste mit 401 antwortet.
        Ein abgelehnter API-Login heisst naemlich nicht zwingend "falsches
        Passwort": nutzt die Schule ein Schulportal oder ist die lokale
        Anmeldung abgeschaltet, antwortet dieser Endpunkt ebenfalls mit 401,
        waehrend das Formular funktioniert. Zwei Versuche bleiben deutlich
        unter jeder Sperrschwelle.
        """
        errors: list[str] = []
        rejected = False
        for strategy in (self._login_api, self._login_form):
            try:
                strategy(username, password)
            except AuthError as exc:
                rejected = rejected or getattr(exc, "final", False)
                errors.append(str(exc))
                continue
            except requests.RequestException as exc:
                errors.append(f"Netzwerkfehler: {exc}")
                continue
            self.user = self._load_me()
            return self.user

        detail = " | ".join(errors)
        if rejected:
            raise AuthError(
                "Die Schul-Cloud hat die Anmeldung abgelehnt. Falls die Daten stimmen: "
                "Meldet sich deine Schule über ein Schulportal (SSO) an oder ist eine "
                "Zwei-Faktor-Anmeldung aktiv, funktioniert dieser Weg nicht – nimm dann "
                f"das Session-Token. ({detail})"
            )
        raise AuthError(f"Anmeldung nicht möglich. ({detail})")

    def login_with_jwt(self, jwt: str) -> dict[str, Any]:
        """Uebernimmt ein bestehendes JWT (Browser-Erweiterung / Cookie)."""
        self._set_jwt(jwt.strip())
        self.strategy = "cookie"
        self.user = self._load_me()
        return self.user

    def _login_api(self, username: str, password: str) -> None:
        """POST /api/v3/authentication/local (neu) bzw. /authentication (alt)."""
        candidates: list[tuple[str, dict]] = [
            (f"{self.base_url}/api/v3/authentication/local",
             {"username": username, "password": password}),
        ]
        for root in self._legacy_roots():
            candidates.append(
                (f"{root}/authentication",
                 {"strategy": "local", "username": username, "password": password})
            )

        last: Optional[str] = None
        for url, payload in candidates:
            try:
                resp = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last = str(exc)
                continue

            if resp.status_code in (200, 201):
                data = _json_or_none(resp) or {}
                token = data.get("accessToken") or data.get("access_token")
                if token:
                    self._set_jwt(token)
                    self.strategy = "api"
                    return
                last = f"Antwort ohne accessToken von {url}"
            elif resp.status_code in (400, 401, 403):
                # Der Endpunkt existiert und lehnt ab. Was genau, sagt der Body -
                # das gehoert in die Meldung, sonst steht der Nutzer vor einem
                # blossen "fehlgeschlagen".
                raise _final_auth_error(f"API {resp.status_code}: {_error_detail(resp)}")
            else:
                last = f"HTTP {resp.status_code} bei {url}"
        raise AuthError(f"API-Login nicht verfügbar ({last or 'kein Endpunkt gefunden'})")

    def _login_form(self, username: str, password: str) -> None:
        """Formular-Login inklusive CSRF-Token (``POST /login``)."""
        login_url = f"{self.base_url}/login"
        page = self.session.get(login_url, timeout=self.timeout)
        csrf = _extract_csrf(page.text)

        data = {"username": username, "password": password, "redirect": ""}
        if csrf:
            data["_csrf"] = csrf

        resp = self.session.post(
            login_url,
            data=data,
            timeout=self.timeout,
            allow_redirects=True,
            headers={"Referer": login_url, "Origin": self.base_url},
        )
        if resp.status_code == 403 and not csrf:
            raise AuthError("Formular-Login abgelehnt (CSRF-Token nicht gefunden).")
        if resp.status_code >= 500:
            raise AuthError(f"Server antwortete mit HTTP {resp.status_code}")

        cookie_jwt = self.session.cookies.get("jwt") or self.session.cookies.get("token")
        if cookie_jwt:
            self._set_jwt(cookie_jwt, keep_cookie=True)
            self.strategy = "form"
            return

        body = resp.text or ""
        if "/login" in resp.url:
            raise _final_auth_error(
                "Formular: Zugangsdaten abgelehnt"
                if _looks_like_login_error(body)
                else f"Formular: Anmeldung abgewiesen (HTTP {resp.status_code})"
            )
        match = re.search(r'"(?:accessToken|jwt)"\s*:\s*"([A-Za-z0-9._-]{20,})"', body)
        if match:
            self._set_jwt(match.group(1))
            self.strategy = "form"
            return
        raise AuthError("Formular-Login lieferte keine Session (kein jwt-Cookie).")

    def _set_jwt(self, token: str, keep_cookie: bool = False) -> None:
        self.jwt = token
        self.session.headers["Authorization"] = f"Bearer {token}"
        if not keep_cookie:
            try:
                self.session.cookies.set("jwt", token, domain=_cookie_domain(self.base_url))
            except Exception:  # pragma: no cover
                log.debug("Konnte jwt-Cookie nicht setzen", exc_info=True)

    def _load_me(self) -> dict[str, Any]:
        """Liest das eigene Profil (``/api/v3/me``) und verifiziert die Session."""
        data = self._v3("me")
        if isinstance(data, dict) and data:
            user = data.get("user") if isinstance(data.get("user"), dict) else data
            school = data.get("school") if isinstance(data.get("school"), dict) else {}
            roles = data.get("roles") or []
            return {
                "_id": user.get("id") or user.get("_id"),
                "firstName": user.get("firstName", ""),
                "lastName": user.get("lastName", ""),
                "school": school.get("name", ""),
                "roles": [r.get("name") for r in roles if isinstance(r, dict)],
            }

        legacy = self._legacy("me")
        if isinstance(legacy, dict) and (legacy.get("_id") or legacy.get("id")):
            return legacy

        soup = self._get_soup("/dashboard")
        if soup is not None:
            name = soup.select_one(".username, .user-name, [data-testid='username']")
            if name:
                return {"displayName": name.get_text(strip=True)}
        if self.jwt:
            return {"displayName": "Angemeldet"}
        raise AuthError("Session konnte nicht verifiziert werden.")

    @property
    def logged_in(self) -> bool:
        return bool(self.jwt or self.session.cookies.get("jwt"))

    def logout(self) -> None:
        try:
            self.session.post(f"{self.base_url}/logout", timeout=5)
        except requests.RequestException:
            pass
        self.session.cookies.clear()
        self.session.headers.pop("Authorization", None)
        self.jwt = None
        self.user = {}
        self.strategy = "none"

    # ------------------------------------------------------------------
    # HTTP-Helfer
    # ------------------------------------------------------------------
    def _request_json(
        self, url: str, params: Optional[dict] = None, timeout: Optional[int] = None
    ) -> Any:
        try:
            resp = self.session.get(url, params=params, timeout=timeout or self.timeout)
        except requests.RequestException as exc:
            log.debug("GET %s fehlgeschlagen: %s", url, exc)
            return None
        if resp.status_code == 401:
            raise AuthError("Session abgelaufen – bitte neu anmelden.")
        if resp.status_code >= 400:
            log.debug("GET %s -> HTTP %s", url, resp.status_code)
            return None
        return _json_or_none(resp)

    def _v3(self, resource: str, params: Optional[dict] = None, timeout: Optional[int] = None) -> Any:
        """Ruft einen Endpunkt der aktuellen API (``/api/v3``) ab."""
        base = self.api_url or self.base_url
        return self._request_json(f"{base}/api/v3/{resource.lstrip('/')}", params, timeout)

    def _legacy_roots(self) -> Iterable[str]:
        if self._legacy_root:
            yield self._legacy_root
            return
        for base in filter(None, (self.api_url, self.base_url)):
            for prefix in LEGACY_PREFIXES:
                yield f"{base}{prefix}"

    def _legacy(self, resource: str, params: Optional[dict] = None) -> Any:
        """Fallback auf die aeltere Feathers-API abweichender Instanzen."""
        for root in list(self._legacy_roots()):
            data = self._request_json(f"{root}/{resource.lstrip('/')}", params)
            if data is not None:
                self._legacy_root = root
                return data
        return None

    def _get_soup(self, path: str, timeout: Optional[int] = None) -> Optional[BeautifulSoup]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            resp = self.session.get(url, timeout=timeout or self.timeout)
        except requests.RequestException as exc:
            log.debug("HTML-Abruf %s fehlgeschlagen: %s", url, exc)
            return None
        if resp.status_code >= 400:
            return None
        if "/login" in resp.url and "login" not in path:
            raise AuthError("Session abgelaufen – bitte neu anmelden.")
        return BeautifulSoup(resp.text, "html.parser")

    # ------------------------------------------------------------------
    # Datenabruf
    # ------------------------------------------------------------------
    def fetch_all(
        self, days_ahead: int = 120, scan_topics: bool = True, topics_ttl: float = 1800
    ) -> FetchResult:
        """Laedt Kurse, Aufgaben, Ankuendigungen und Termine."""
        result = FetchResult()

        # --- Kursuebersicht --------------------------------------------
        courses = _v3_list(self._v3("courses", {"skip": 0, "limit": 100}))
        if courses:
            result.courses = [_normalize_course(c) for c in courses]
            result.sources["courses"] = "api/v3"
        else:
            legacy = _feathers_list(self._legacy("courses", {"$limit": 100}))
            if legacy:
                result.courses = [_normalize_course(c) for c in legacy]
                result.sources["courses"] = "api/v1"
            else:
                result.courses = self._scrape_courses()
                result.sources["courses"] = "html" if result.courses else "none"
                if not result.courses:
                    result.warnings.append("Kursübersicht konnte nicht gelesen werden.")

        # --- Aufgaben ("Aufgaben" / "Abgaben") -------------------------
        open_tasks = _v3_list(self._v3("tasks", {"skip": 0, "limit": 100}))
        done_tasks = _v3_list(self._v3("tasks/finished", {"skip": 0, "limit": 100}))
        if open_tasks or done_tasks:
            for task in open_tasks:
                task["_finished"] = False
            for task in done_tasks:
                task["_finished"] = True
            result.tasks = open_tasks + done_tasks
            result.sources["tasks"] = "api/v3"
        else:
            legacy = _feathers_list(
                self._legacy("homework", {"$limit": 200, "$populate[]": "courseId"})
            )
            if legacy:
                result.homework = legacy
                result.sources["tasks"] = "api/v1"
                result.submissions = _feathers_list(
                    self._legacy("submissions", {"$limit": 200})
                )
            else:
                result.homework = self._scrape_homework()
                result.sources["tasks"] = "html" if result.homework else "none"
                if not result.homework:
                    result.warnings.append(
                        "Aufgabenmodul konnte nicht gelesen werden – bitte neu anmelden."
                    )

        # --- Ankuendigungen und Kalender -------------------------------
        result.news = _v3_list(self._v3("news", {"skip": 0, "limit": 50}))
        if result.news:
            result.sources["news"] = "api/v3"

        result.events = self._fetch_calendar(days_ahead)
        if result.events:
            result.sources["calendar"] = "api"

        result.lessons = _feathers_list(self._legacy("lessons", {"$limit": 200}))
        if result.lessons:
            result.sources["lessons"] = "api/v1"

        if scan_topics and result.courses:
            fresh = (time.monotonic() - self._topics_read) < topics_ttl
            if fresh and self._topics:
                result.topics = self._topics
                result.sources["topics"] = "zwischenspeicher"
            else:
                try:
                    self._topics = self.fetch_course_topics(result.courses)
                    self._topics_read = time.monotonic()
                    result.topics = self._topics
                except AuthError:
                    raise
                except Exception:  # pragma: no cover - darf den Rest nicht kippen
                    log.exception("Kursthemen konnten nicht gelesen werden")
                    result.warnings.append("Kursthemen konnten nicht gelesen werden.")
                if result.topics:
                    result.sources["topics"] = "api/v3+html"

        return result

    def fetch_course_topics(
        self,
        courses: list[dict],
        max_courses: int = 12,
        max_requests: int = 45,
        max_seconds: float = 20.0,
    ) -> list[dict]:
        """Liest die Themen der Kurse - dort kuendigen Lehrkraefte oft an.

        Zwei Formen kommen vor und werden beide beruecksichtigt:

        * klassische Themen ("lesson"): Titel ueber das Kurs-Board, der Text
          ueber die HTML-Seite ``/courses/<kurs>/topics/<thema>``
        * Spalten-Boards ("column-board"): ``/api/v3/boards/<id>`` liefert die
          Spalten mit Karten-Ids, ``/api/v3/cards?ids=…`` deren Inhalte

        ``max_requests`` deckelt die Zahl der Abrufe, damit eine Aktualisierung
        nicht minutenlang dauert.
        """
        budget = _Budget(max_requests, max_seconds)
        topics: list[dict] = []

        for course in courses[:max_courses]:
            course_id = course.get("_id")
            if not course_id or not budget.take():
                continue
            board = self._v3(f"course-rooms/{course_id}/board", timeout=TOPIC_TIMEOUT)
            if not isinstance(board, dict):
                continue

            elements = _board_elements(board)
            for position, element in enumerate(elements):
                kind = (element.get("type") or "").lower()
                content = element.get("content") if isinstance(element.get("content"), dict) else element
                element_id = content.get("id") or content.get("_id")
                if not element_id:
                    continue

                # Abstand zum Ende der Liste: 0 ist der neueste Eintrag. Dient
                # als Notbehelf, wenn weder Datum noch Zeitstempel vorliegen.
                rank = len(elements) - 1 - position

                if "lesson" in kind or "topic" in kind:
                    topics.append(
                        self._read_lesson(course, element_id, content, budget, rank)
                    )
                elif "board" in kind:
                    topics.extend(self._read_column_board(course, element_id, budget))

        return [t for t in topics if t]

    def _read_lesson(
        self, course: dict, lesson_id: str, content: dict, budget: "_Budget", rank: int = 0
    ) -> Optional[dict]:
        """Klassisches Thema: Titel vom Board, Text aus der HTML-Seite."""
        title = content.get("name") or content.get("title") or ""
        text = ""
        if budget.take():
            soup = None
            try:
                soup = self._get_soup(
                    f"/courses/{course['_id']}/topics/{lesson_id}", timeout=TOPIC_TIMEOUT
                )
            except AuthError:
                raise
            except Exception:  # pragma: no cover - Netzwerkausfall
                log.debug("Thema %s nicht lesbar", lesson_id, exc_info=True)
            if soup is not None:
                main = soup.select_one("main, .section-content, #topic-content, .container")
                text = (main or soup).get_text(" ", strip=True)[:2000]

        if not title and not text:
            return None
        return {
            "id": lesson_id,
            "title": title,
            "text": text,
            "course_id": course.get("_id"),
            "course_name": course.get("name"),
            "color": course.get("color"),
            "updated_at": content.get("updatedAt") or content.get("createdAt"),
            "recent_rank": rank,
            "url": f"{self.base_url}/courses/{course['_id']}/topics/{lesson_id}",
        }

    def _read_column_board(
        self, course: dict, board_id: str, budget: "_Budget"
    ) -> list[dict]:
        """Spalten-Board: Karten einsammeln und deren Texte zusammenfassen."""
        if not budget.take():
            return []
        board = self._v3(f"boards/{board_id}", timeout=TOPIC_TIMEOUT)
        if not isinstance(board, dict):
            return []

        card_ids: list[str] = []
        for column in board.get("columns") or []:
            for card in (column or {}).get("cards") or []:
                card_id = card.get("cardId") or card.get("id") if isinstance(card, dict) else card
                if isinstance(card_id, str):
                    card_ids.append(card_id)
        if not card_ids or not budget.take():
            return []

        cards = self._v3("cards", {"ids": card_ids[:30]}, timeout=TOPIC_TIMEOUT)
        entries = _v3_list(cards)
        found = []
        for position, card in enumerate(entries):
            text = _collect_text(card)
            if not text:
                continue
            found.append(
                {
                    "id": card.get("id") or card.get("_id") or board_id,
                    "title": card.get("title") or board.get("title") or "Kursthema",
                    "text": text[:2000],
                    "course_id": course.get("_id"),
                    "course_name": course.get("name"),
                    "color": course.get("color"),
                    "updated_at": card.get("updatedAt") or card.get("createdAt"),
                    "recent_rank": len(entries) - 1 - position,
                    "url": f"{self.base_url}/courses/{course['_id']}",
                }
            )
        return found

    def _fetch_calendar(self, days_ahead: int) -> list[dict]:
        """Kalendertermine; der Kalenderdienst haengt an mehreren Routen."""
        now = datetime.now(timezone.utc)
        window = {
            "from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        candidates = (
            (f"{self.base_url}/calendar/events", window),
            (f"{self.base_url}/api/v3/calendar/events", window),
            (f"{self.base_url}/calendar", {"all": "true", **window}),
        )
        for url, params in candidates:
            data = self._request_json(url, params)
            items = _v3_list(data) or _feathers_list(data)
            if items:
                return items
        return []

    # ------------------------------------------------------------------
    # HTML-Fallbacks (Scraper-Logik)
    # ------------------------------------------------------------------
    def _scrape_courses(self) -> list[dict]:
        soup = self._get_soup("/courses")
        if soup is None:
            return []
        courses: list[dict] = []
        for link in soup.select("a[href*='/courses/']"):
            href = link.get("href", "")
            course_id = href.rstrip("/").split("/")[-1]
            title_el = link.select_one(".title, h5, h4, .card-title") or link
            name = title_el.get_text(" ", strip=True)
            if not name or not re.fullmatch(r"[0-9a-f]{6,}", course_id or ""):
                continue
            if any(c["_id"] == course_id for c in courses):
                continue
            courses.append({"_id": course_id, "name": name, "color": _style_color(link)})
        return courses

    def _scrape_homework(self) -> list[dict]:
        """Liest /homework (Aufgaben & Abgaben) aus dem HTML."""
        items: list[dict] = []
        for path in ("/homework", "/homework/asked", "/dashboard"):
            soup = self._get_soup(path)
            if soup is None:
                continue
            for card in soup.select("[class*='card'], li, tr"):
                link = card.select_one("a[href*='/homework/']")
                if link is None:
                    continue
                href = link.get("href", "")
                hw_id = href.rstrip("/").split("/")[-1]
                if not re.fullmatch(r"[0-9a-f]{6,}", hw_id or ""):
                    continue
                if any(i["_id"] == hw_id for i in items):
                    continue
                text = card.get_text(" ", strip=True)
                items.append(
                    {
                        "_id": hw_id,
                        "name": link.get_text(" ", strip=True) or "Aufgabe",
                        "courseName": _scrape_course_name(card),
                        "dueDateText": _scrape_due_text(text),
                        "statusText": text,
                        "description": "",
                        "_source": "html",
                    }
                )
            if items:
                break
        return items


# ----------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------
def _final_auth_error(message: str) -> AuthError:
    """Fehler, nach dem keine weitere Strategie mehr probiert wird."""
    error = AuthError(message)
    error.final = True  # type: ignore[attr-defined]
    return error


def _extract_csrf(html: str) -> Optional[str]:
    """Liest das ``_csrf``-Feld aus dem Login-Formular.

    Bewusst ueber den HTML-Parser statt per Regex: im Formular steht vor dem
    ``name``-Attribut noch ein ``data-force-value="true"``, an dem sich ein
    Regex-Ansatz verschluckt.
    """
    field = BeautifulSoup(html or "", "html.parser").find("input", attrs={"name": "_csrf"})
    if field is None:
        return None
    value = field.get("value")
    return value or None


class _Budget:
    """Deckelt einen Kursdurchlauf - nach Abrufen und nach Zeit.

    Die Zahl allein genuegt nicht: antwortet die Schul-Cloud langsam, dauert
    der Durchlauf trotzdem Minuten. Deshalb zusaetzlich eine Frist.
    """

    def __init__(self, limit: int, seconds: float = 20.0) -> None:
        self.left = limit
        self.deadline = time.monotonic() + seconds

    def take(self) -> bool:
        if self.left <= 0 or time.monotonic() > self.deadline:
            return False
        self.left -= 1
        return True


def _board_elements(board: dict) -> list[dict]:
    """Die Elementliste eines Kurs-Boards; die Feldnamen variieren."""
    for key in ("elements", "roomElements", "boardElements", "data"):
        value = board.get(key)
        if isinstance(value, list):
            return [e for e in value if isinstance(e, dict)]
    return []


TEXT_KEYS = ("text", "title", "name", "caption", "description", "content")


def _collect_text(node: Any, depth: int = 0) -> str:
    """Sammelt alle Textbausteine aus einer verschachtelten Kartenstruktur.

    Die Board-API schachtelt Karteninhalte unterschiedlich tief; statt eine
    feste Struktur anzunehmen, werden alle bekannten Textfelder eingesammelt.
    """
    if depth > 6:
        return ""
    if isinstance(node, str):
        return node
    parts: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in TEXT_KEYS or isinstance(value, (dict, list)):
                parts.append(_collect_text(value, depth + 1))
    elif isinstance(node, list):
        for value in node[:50]:
            parts.append(_collect_text(value, depth + 1))
    return " ".join(p for p in parts if p).strip()


def _error_detail(resp: requests.Response) -> str:
    """Kurzfassung der Fehlerantwort - hilft beim Einordnen einer Ablehnung."""
    data = _json_or_none(resp)
    if isinstance(data, dict):
        for key in ("message", "title", "error", "type"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value[:120]
    text = (resp.text or "").strip()
    return text[:120] if text else "ohne Angabe"


def _json_or_none(resp: requests.Response) -> Any:
    if "json" not in resp.headers.get("Content-Type", ""):
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _v3_list(data: Any) -> list[dict]:
    """Die v3-API antwortet mit ``{"data": [...], "total": n}``."""
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return [d for d in data["data"] if isinstance(d, dict)]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def _feathers_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("data", "events", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [d for d in value if isinstance(d, dict)]
    return []


def _normalize_course(course: dict) -> dict:
    """Vereinheitlicht v3- (``id``) und Feathers-Kurse (``_id``)."""
    return {
        "_id": course.get("id") or course.get("_id"),
        "name": course.get("name") or course.get("title") or "",
        "color": course.get("displayColor") or course.get("color"),
    }


def _cookie_domain(base_url: str) -> str:
    host = re.sub(r"^https?://", "", base_url).split("/")[0]
    return host.split(":")[0]


def _looks_like_login_error(html: str) -> bool:
    lowered = (html or "").lower()
    needles = ("falsche", "nicht korrekt", "invalid", "fehlgeschlagen", "not authenticated")
    return any(n in lowered for n in needles)


def _style_color(node) -> Optional[str]:
    style = node.get("style") or ""
    match = re.search(r"(#[0-9a-fA-F]{3,6})", style)
    return match.group(1) if match else None


def _scrape_course_name(card) -> str:
    for sel in (".course-name", ".subtitle", "[class*='course']", "small"):
        el = card.select_one(sel)
        if el:
            name = el.get_text(" ", strip=True)
            if name:
                return name
    return ""


DUE_TEXT_RE = re.compile(
    r"(\d{1,2}\.\d{1,2}\.\d{2,4}(?:\s*,?\s*(?:um\s*)?\d{1,2}[:.]\d{2})?)"
)


def _scrape_due_text(text: str) -> str:
    match = DUE_TEXT_RE.search(text or "")
    return match.group(1) if match else ""
