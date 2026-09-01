# Schul-Cloud Brandenburg – persönliches Dashboard

Ein lokal laufendes Dashboard, das Aufgaben, Abgabetermine und Testankündigungen
aus der Schul-Cloud Brandenburg einsammelt, nach Dringlichkeit sortiert und eine
eigene Abhak-Funktion mit Archiv bereitstellt.

**Backend:** Python 3.11 + Flask · **Frontend:** HTML + Tailwind CSS + Vanilla JS ·
**Speicher:** SQLite (nur lokal)

![Dashboard](docs/screenshot.png)

---

## 1. Schnellstart

```bash
git clone https://github.com/sawad4832-boop/School-Management-.git
cd School-Management-

./run.sh              # legt venv an, installiert Abhängigkeiten, startet den Server
```

Alternativ manuell:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Werte anpassen (mindestens SECRET_KEY)
python app.py
```

Danach im Browser **http://127.0.0.1:5000** öffnen.

> Zum Ausprobieren ohne Zugangsdaten: auf der Login-Seite **„Demo-Daten ansehen“**
> klicken (oder `SC_DEMO=1` in der `.env` setzen).

### Konfiguration (`.env`)

| Variable | Bedeutung | Standard |
|---|---|---|
| `SC_BASE_URL` | Adresse der Schul-Cloud-Instanz | `https://brandenburg.cloud` |
| `SC_API_URL` | optionale separate API-Adresse | – |
| `SECRET_KEY` | Signatur der Flask-Session | zufällig pro Start |
| `SC_REFRESH_MINUTES` | Hintergrund-Aktualisierung (0 = aus) | `15` |
| `SC_INGEST_TOKEN` | Token für die Browser-Erweiterung (leer = `/api/ingest` aus) | leer |
| `SC_DB_PATH` | Pfad der SQLite-Datei | `data/dashboard.sqlite3` |
| `SC_DEMO` | Demo-Modus | `0` |
| `PORT` / `HOST` | Bindung des Servers | `5000` / `127.0.0.1` |

### Tests

```bash
pip install pytest
python -m pytest -q          # 19 Tests: Parsing, Dringlichkeit, Store, HTTP-API
```

---

## 2. Anmeldung an der Schul-Cloud

Es gibt **keine offizielle öffentliche API**. Die Schul-Cloud Brandenburg basiert auf
der quelloffenen HPI-/dBildungscloud-Plattform mit einem Feathers-Backend. Der
Client in `schulcloud/client.py` geht deshalb gestaffelt vor und nimmt den ersten
Weg, der funktioniert:

| Strategie | Ablauf |
|---|---|
| `api` | `POST /authentication` mit `{strategy:"local", username, password}` → JWT → `Authorization: Bearer …` |
| `form` | klassischer Formular-Login `POST /login/` → Plattform setzt das `jwt`-Cookie, die Session wird weitergeführt |
| `cookie` | ein vorhandenes JWT (Login-Feld „Session-Token“ oder Browser-Erweiterung) wird übernommen |

Für jede Ressource wird zuerst die JSON-API (`/api/v1`, `/api/v3`, `/api`, `/`) probiert;
schlägt das fehl, greift die HTML-Scraper-Logik (`_scrape_courses`, `_scrape_homework`).

**Umgang mit Zugangsdaten**

* Das Passwort wird nur zum Login an die Schul-Cloud durchgereicht und **nie gespeichert**.
* JWT und Session liegen ausschließlich im Arbeitsspeicher des lokalen Servers
  (`SESSIONS` in `app.py`) – nach einem Neustart ist eine neue Anmeldung nötig.
* In SQLite landen nur Aufgaben-Metadaten und der eigene Abhak-Status.
* Der Server bindet standardmäßig auf `127.0.0.1`, ist also nicht aus dem Netz erreichbar.
* Bei Zwei-Faktor-Anmeldung oder Single-Sign-on den Weg über Session-Token /
  Browser-Erweiterung nutzen.

### Aktualisierung

* **Button „Aktualisieren“** in der Kopfzeile (`POST /api/refresh`).
* **Automatisch** alle `SC_REFRESH_MINUTES` Minuten – serverseitig durch einen
  Hintergrund-Thread und zusätzlich im Browser-Tab.
* **Per Erweiterung** durch `POST /api/ingest`.

---

## 3. Browser-Erweiterung (Ordner `browser-extension/`)

Nützlich, wenn der direkte Login nicht möglich ist (2FA, SSO, Captcha): Die
Erweiterung liest das Session-Cookie der bereits angemeldeten Schul-Cloud aus und
schickt es an das lokale Dashboard – oder scrapt die sichtbare Aufgabenliste direkt
im Browser.

**Installation (Chrome/Edge):**

1. In der `.env` ein `SC_INGEST_TOKEN=<beliebiges-geheimnis>` setzen und den Server neu starten.
2. `chrome://extensions` öffnen → **Entwicklermodus** aktivieren → **Entpackte Erweiterung laden**
   → Ordner `browser-extension/` wählen.
3. Im Popup der Erweiterung Dashboard-URL und Ingest-Token eintragen, **Einstellungen speichern**.
4. In der Schul-Cloud anmelden, dann **„Session & Daten übertragen“** klicken.
   Auf der Aufgabenseite funktioniert zusätzlich **„Sichtbare Aufgaben scrapen“**.

`/api/ingest` ist ohne gesetztes `SC_INGEST_TOKEN` deaktiviert und prüft den Header
`X-Ingest-Token` per `secrets.compare_digest`.

---

## 4. Datenanalyse & Parsing

`schulcloud/parser.py` normalisiert alle Quellen auf ein einheitliches Format:

```json
{
  "id": "hw:5f2b…", "kind": "homework|exam", "title": "…", "course": "Mathematik 10b",
  "due": "2026-09-03T14:00:00+02:00", "status": "open|submitted|graded",
  "grade": "13", "url": "https://…/homework/5f2b…", "teacher": "Herr Neumann"
}
```

* **Kursübersicht** – `/courses` (API) bzw. Kurskarten im HTML; liefert Name und Farbe.
* **Aufgabenmodul** – `/homework` inklusive `courseId`; Titel, Fach, Beschreibung, `dueDate`.
* **Abgabetermine** – `parse_datetime()` versteht ISO-8601, Millisekunden-Timestamps und
  deutsche Schreibweisen (`03.09.2026, 14:00`, `3.9.26`). Fehlt ein Termin, sucht
  `find_deadline_in_text()` Formulierungen wie „Abgabe bis 12.09.2026 um 18:00“.
* **Status** – aus `/submissions`: keine Abgabe → `offen`, Abgabe ohne Note →
  `eingereicht`, mit `grade`/`gradeComment` → `bewertet` (wandert automatisch ins Archiv).
  Im HTML-Fallback werden die Statuswörter der Karten ausgewertet.
* **Testankündigungen** – Titel, Beschreibungen, Kalendereinträge (`/calendar`) und
  Kursthemen (`/lessons`) werden gegen eine Stichwortliste geprüft: Klassenarbeit,
  Klausur, Lernkontrolle, Vokabeltest, Prüfung, Diktat, Referat, Präsentation, …
  Treffer erscheinen als eigener Typ **„Test / Arbeit“**; gewöhnliche Stundenplan-Termine
  werden bewusst ignoriert.

## 5. Übersicht & Interaktivität

* **Sortierung nach Dringlichkeit:** überfällig → < 24 h → < 48 h → diese Woche → später →
  ohne Termin, innerhalb einer Stufe nach Abgabezeitpunkt.
* **Farbliche Warnungen:** roter Balken/Chip bei überfällig und < 24 h, orange bei < 48 h,
  gelb innerhalb einer Woche, grau danach. Dazu ein Countdown („in 13 Std. 59 Min.“).
* **Abhaken:** Klick auf die Checkbox → `POST /api/items/<id>/done` → Eintrag verschwindet
  aus der To-do-Liste und landet im Archiv (dort per „Zurückholen“ reversibel).
  Der Status wird **lokal** in SQLite geführt, weil die Schul-Cloud kein Setzen von außen
  erlaubt – er bleibt deshalb auch nach einer Aktualisierung erhalten, selbst wenn die
  Aufgabe dort nicht mehr auftaucht.
* **Kennzahlen** oben: offen, überfällig, nächste 24 h / 48 h, Tests, erledigt.
* **Filter** nach Typ (Aufgaben / Tests / dringend), Kurs und Volltextsuche.

### HTTP-Endpunkte

| Methode & Pfad | Zweck |
|---|---|
| `GET /` | Oberfläche |
| `GET /api/status` | Login-Status, Nutzer, letzte Aktualisierung |
| `POST /api/login` | `{username,password}` \| `{jwt}` \| `{demo:true}` |
| `POST /api/logout` | Session verwerfen |
| `GET /api/items` | aktive Liste + Archiv + Kennzahlen |
| `POST /api/refresh` | Daten neu von der Schul-Cloud holen |
| `POST /api/items/<id>/done` | `{done:true|false}` – abhaken / zurückholen |
| `POST /api/items/<id>/note` | eigene Notiz speichern |
| `POST /api/ingest` | Endpunkt der Browser-Erweiterung (Token nötig) |
| `GET /api/health` | Health-Check |

---

## 6. Projektstruktur

```
app.py                    Flask-App: Routen, Sessions, Hintergrund-Aktualisierung
schulcloud/client.py      Login-Wrapper (API / Formular / Cookie) + Scraper-Fallback
schulcloud/parser.py      Normalisierung, Terminerkennung, Testerkennung, Dringlichkeit
schulcloud/store.py       SQLite: Abhak-Status, Notizen, Cache
schulcloud/demo.py        Beispieldaten für den Demo-Modus
templates/index.html      Oberfläche (Tailwind)
static/js/app.js          Frontend-Logik (Fetch + DOM)
browser-extension/        Chrome-/Edge-Erweiterung (MV3) als Alternative zum Login
tests/                    pytest-Suite
```

### Ohne CDN betreiben

Standardmäßig lädt die Seite Tailwind über das CDN. Ein lokaler Build wird
automatisch bevorzugt, sobald `static/css/tailwind.css` existiert:

```bash
npx tailwindcss -i static/css/input.css -o static/css/tailwind.css --minify
```

---

## 7. Hinweise

* Das Tool ist ein privates Hilfsmittel für den **eigenen** Account. Es werden nur
  Daten gelesen, die der angemeldete Account ohnehin im Browser sieht.
* Da keine offizielle API existiert, können Änderungen an der Schul-Cloud die
  Abfragen brechen. Anpassungen betreffen dann fast immer nur `schulcloud/client.py`
  (Endpunkte) und die Selektoren der Scraper-Methoden.
* Für den Dauerbetrieb hinter einem eigenen Reverse-Proxy unbedingt einen festen
  `SECRET_KEY`, HTTPS und einen zusätzlichen Zugriffsschutz vorsehen.
