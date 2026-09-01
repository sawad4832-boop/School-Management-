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
    lessons: list[dict] = field(default_factory=list)      # Kursthemen
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

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> dict[str, Any]:
        """Meldet sich an: erst ueber die JSON-API, dann ueber das Formular."""
        errors: list[str] = []
        for strategy in (self._login_api, self._login_form):
            try:
                strategy(username, password)
            except AuthError as exc:
                # Falsche Zugangsdaten sind endgueltig - nicht weiter probieren.
                if getattr(exc, "final", False):
                    raise
                errors.append(str(exc))
                continue
            except requests.RequestException as exc:
                errors.append(f"Netzwerkfehler: {exc}")
                continue
            self.user = self._load_me()
            return self.user
        raise AuthError("Login nicht möglich: " + "; ".join(errors))

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
                # Der Endpunkt existiert und lehnt die Daten ab.
                raise _final_auth_error(
                    "Benutzername oder Passwort wurde nicht akzeptiert. "
                    "Bei Anmeldung über die Schul-Cloud-App/SSO bitte den Weg "
                    "über das Session-Token nutzen."
                )
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
                "Benutzername oder Passwort wurde nicht akzeptiert."
                if _looks_like_login_error(body)
                else "Login wurde abgewiesen (evtl. Zwei-Faktor-Anmeldung). "
                     "Bitte den Weg über das Session-Token nutzen."
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
    def _request_json(self, url: str, params: Optional[dict] = None) -> Any:
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            log.debug("GET %s fehlgeschlagen: %s", url, exc)
            return None
        if resp.status_code == 401:
            raise AuthError("Session abgelaufen – bitte neu anmelden.")
        if resp.status_code >= 400:
            log.debug("GET %s -> HTTP %s", url, resp.status_code)
            return None
        return _json_or_none(resp)

    def _v3(self, resource: str, params: Optional[dict] = None) -> Any:
        """Ruft einen Endpunkt der aktuellen API (``/api/v3``) ab."""
        base = self.api_url or self.base_url
        return self._request_json(f"{base}/api/v3/{resource.lstrip('/')}", params)

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

    def _get_soup(self, path: str) -> Optional[BeautifulSoup]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            resp = self.session.get(url, timeout=self.timeout)
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
    def fetch_all(self, days_ahead: int = 120) -> FetchResult:
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

        return result

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
