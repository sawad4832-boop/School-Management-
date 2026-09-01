"""Login-Wrapper und Datenabruf fuer die Schul-Cloud Brandenburg.

Es gibt keine oeffentlich dokumentierte API. Die Schul-Cloud Brandenburg basiert
auf der (quelloffenen) HPI-/dBildungscloud-Plattform, deren Backend ein
Feathers-Service ist. Dieser Client geht deshalb gestaffelt vor:

1. ``strategy=api``    - JWT ueber ``POST /authentication`` holen und alle
                         weiteren Aufrufe mit ``Authorization: Bearer <jwt>``
                         durchfuehren.
2. ``strategy=form``   - klassischer Formular-Login (``POST /login/``); die
                         Plattform setzt ein ``jwt``-Cookie, das die Session
                         traegt.
3. ``strategy=cookie`` - ein bereits vorhandenes JWT/Cookie (z.B. aus der
                         mitgelieferten Browser-Erweiterung) wird uebernommen.

Fuer jede Ressource wird zuerst die JSON-API versucht; scheitert sie, faellt der
Client auf das Parsen der ausgelieferten HTML-Seiten zurueck (Scraper-Logik).
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

# Reihenfolge, in der API-Praefixe ausprobiert werden.
API_PREFIXES = ("/api/v1", "/api/v3", "/api", "")


class SchulCloudError(RuntimeError):
    """Allgemeiner Fehler beim Zugriff auf die Schul-Cloud."""


class AuthError(SchulCloudError):
    """Login fehlgeschlagen oder Session abgelaufen."""


@dataclass
class FetchResult:
    """Rohdaten eines Abrufs inklusive Diagnose-Informationen."""

    courses: list[dict] = field(default_factory=list)
    homework: list[dict] = field(default_factory=list)
    submissions: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    lessons: list[dict] = field(default_factory=list)
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
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/html"})
        self.session.verify = verify_tls
        self.jwt: Optional[str] = None
        self.user: dict[str, Any] = {}
        self.strategy: str = "none"
        self._api_root: Optional[str] = None  # gemerkter, funktionierender API-Praefix

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> dict[str, Any]:
        """Meldet sich an. Versucht API-Login, danach Formular-Login."""
        errors: list[str] = []
        for strategy in (self._login_api, self._login_form):
            try:
                strategy(username, password)
            except AuthError as exc:
                errors.append(str(exc))
                continue
            except requests.RequestException as exc:  # Netzwerkprobleme
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
        payload = {"strategy": "local", "username": username, "password": password}
        last: Optional[str] = None
        for root in self._api_roots():
            url = f"{root}/authentication"
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
                    self._api_root = root
                    self.strategy = "api"
                    return
                last = "Antwort enthielt kein accessToken"
            elif resp.status_code in (400, 401, 403):
                raise AuthError("Benutzername oder Passwort wurde nicht akzeptiert.")
            else:
                last = f"HTTP {resp.status_code} bei {url}"
        raise AuthError(f"API-Login nicht verfügbar ({last or 'kein Endpunkt gefunden'})")

    def _login_form(self, username: str, password: str) -> None:
        login_url = urljoin(self.base_url + "/", "login/")
        data = {"username": username, "password": password, "challenge": ""}
        resp = self.session.post(
            login_url,
            data=data,
            timeout=self.timeout,
            allow_redirects=True,
            headers={"Referer": login_url},
        )
        if resp.status_code >= 500:
            raise AuthError(f"Server antwortete mit HTTP {resp.status_code}")

        cookie_jwt = self.session.cookies.get("jwt") or self.session.cookies.get("token")
        if cookie_jwt:
            self._set_jwt(cookie_jwt, keep_cookie=True)
            self.strategy = "form"
            return

        body = resp.text or ""
        if "login" in resp.url and _looks_like_login_error(body):
            raise AuthError("Benutzername oder Passwort wurde nicht akzeptiert.")
        # Manche Instanzen legen das JWT in ein Inline-Skript.
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
            except Exception:  # pragma: no cover - Cookie-Domain exotisch
                log.debug("Konnte jwt-Cookie nicht setzen", exc_info=True)

    def _load_me(self) -> dict[str, Any]:
        data = self._get_json("me")
        if isinstance(data, dict) and (data.get("_id") or data.get("id")):
            return data
        users = self._get_json("users", params={"$limit": 1})
        if isinstance(users, dict) and users.get("data"):
            return users["data"][0]
        # Fallback: Name aus dem Dashboard-HTML ziehen.
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
            self.session.post(urljoin(self.base_url + "/", "logout/"), timeout=5)
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
    def _api_roots(self) -> Iterable[str]:
        if self._api_root:
            yield self._api_root
            return
        bases = [self.api_url] if self.api_url else []
        bases.append(self.base_url)
        for base in bases:
            if not base:
                continue
            for prefix in API_PREFIXES:
                yield f"{base}{prefix}"

    def _get_json(self, resource: str, params: Optional[dict] = None) -> Any:
        """Holt eine Feathers-Ressource; probiert bekannte API-Praefixe durch."""
        last_error: Optional[str] = None
        for root in list(self._api_roots()):
            url = f"{root}/{resource.lstrip('/')}"
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
                continue
            if resp.status_code == 401:
                raise AuthError("Session abgelaufen – bitte neu anmelden.")
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code} bei {url}"
                continue
            data = _json_or_none(resp)
            if data is None:
                last_error = f"Keine JSON-Antwort von {url}"
                continue
            self._api_root = root
            return data
        log.debug("API-Abruf %s fehlgeschlagen: %s", resource, last_error)
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
        """Laedt Kurse, Aufgaben, Abgaben, Termine und Themen."""
        result = FetchResult()

        courses = self._get_json("courses", params={"$limit": 100})
        if courses:
            result.courses = _feathers_list(courses)
            result.sources["courses"] = "api"
        else:
            result.courses = self._scrape_courses()
            result.sources["courses"] = "html" if result.courses else "none"
            if not result.courses:
                result.warnings.append("Kursübersicht konnte nicht gelesen werden. Melde dich ggf. neu an.")

        homework = self._get_json(
            "homework",
            params={"$limit": 200, "$populate[]": "courseId", "$sort": "dueDate"},
        )
        if homework:
            result.homework = _feathers_list(homework)
            result.sources["homework"] = "api"
        else:
            result.homework = self._scrape_homework()
            result.sources["homework"] = "html" if result.homework else "none"
            if not result.homework:
                result.warnings.append("Aufgabenmodul konnte nicht gelesen werden. Melde dich ggf. neu an.")

        user_id = self.user.get("_id") or self.user.get("id")
        params = {"$limit": 200}
        if user_id:
            params["studentId"] = user_id
        submissions = self._get_json("submissions", params=params)
        if submissions:
            result.submissions = _feathers_list(submissions)
            result.sources["submissions"] = "api"

        result.events = self._fetch_calendar(days_ahead)
        result.sources["calendar"] = "api" if result.events else "none"

        lessons = self._get_json("lessons", params={"$limit": 200})
        if lessons:
            result.lessons = _feathers_list(lessons)
            result.sources["lessons"] = "api"

        return result

    def _fetch_calendar(self, days_ahead: int) -> list[dict]:
        now = datetime.now(timezone.utc)
        window = {
            "from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for resource, params in (
            ("calendar", {"all": "true", **window}),
            ("calendar/events", window),
            ("events", {"$limit": 200}),
        ):
            data = self._get_json(resource, params=params)
            items = _feathers_list(data) if data else []
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
        """Liest /homework/ (Aufgaben & Abgaben) aus dem HTML."""
        items: list[dict] = []
        for path in ("/homework/", "/homework/asked", "/dashboard"):
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
def _json_or_none(resp: requests.Response) -> Any:
    ctype = resp.headers.get("Content-Type", "")
    if "json" not in ctype:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _feathers_list(data: Any) -> list[dict]:
    """Feathers liefert entweder eine Liste oder ``{"data": [...]}``."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("data", "events", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [d for d in value if isinstance(d, dict)]
    return []


def _cookie_domain(base_url: str) -> str:
    host = re.sub(r"^https?://", "", base_url).split("/")[0]
    return host.split(":")[0]


def _looks_like_login_error(html: str) -> bool:
    lowered = html.lower()
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
