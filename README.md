# Schul-Cloud Brandenburg – persönliches Dashboard

Ein Dashboard, das **auf deinem eigenen Rechner läuft** und Aufgaben, Abgabetermine
und Testankündigungen aus der Schul-Cloud Brandenburg einsammelt, nach Dringlichkeit
sortiert und eine eigene Abhak-Funktion mit Archiv bereitstellt.

**Backend:** Python 3.9+ / Flask · **Frontend:** HTML + Tailwind CSS + Vanilla JS ·
**Speicher:** SQLite (nur lokal)

![Dashboard](docs/screenshot.png)

---

## 1. Wichtig vorab: `http://127.0.0.1:5000` ist *dein* Rechner

`127.0.0.1` (= `localhost`) bedeutet immer „das Gerät, auf dem der Browser läuft".
Der Link funktioniert also **nur, während das Programm auf genau diesem Gerät läuft**.
Wenn Safari „Server nicht gefunden" oder „Verbindung fehlgeschlagen" meldet, ist der
Server bei dir schlicht nicht gestartet. Es gibt keine Internetadresse, unter der das
Dashboard ohne eigene Installation erreichbar wäre – und das ist Absicht: So bleiben
Zugangsdaten und Aufgaben auf deinem Gerät.

## 2. Installation auf dem Mac (Schritt für Schritt)

1. **Terminal öffnen** – ⌘ + Leertaste, „Terminal" eingeben, Enter.
2. **Python prüfen:**
   ```bash
   python3 --version
   ```
   Kommt eine Versionsnummer (3.9 oder höher), ist alles gut. Kommt ein Fehler oder
   öffnet sich die Entwickler-Installation, Python von [python.org/downloads](https://www.python.org/downloads/)
   installieren und das Terminal danach neu öffnen.
3. **Projekt laden und starten:**
   ```bash
   git clone https://github.com/sawad4832-boop/School-Management-.git
   cd School-Management-
   ./run.sh
   ```
   `run.sh` legt beim ersten Mal eine virtuelle Umgebung an, installiert die
   Abhängigkeiten und startet den Server. Das dauert einmalig ein bis zwei Minuten.
4. Im Terminal erscheint:
   ```
   → Dashboard läuft auf http://127.0.0.1:5000
   ```
   **Jetzt** diesen Link in Safari öffnen. Das Terminal muss dabei offen bleiben –
   es *ist* der Server. Beenden mit `Ctrl + C`.
5. Auf der Login-Seite die Zugangsdaten der Schul-Cloud eingeben. Sie gehen direkt
   an `brandenburg.cloud` und werden nirgends gespeichert.

> **Erstmal ohne Zugangsdaten ausprobieren:** auf **„Demo-Daten ansehen"** klicken.
> Wenn das läuft, funktioniert die Installation – dann fehlt nur noch der Login.

Ohne `git` geht es auch: auf GitHub **Code → Download ZIP**, entpacken, im Terminal
in den Ordner wechseln (`cd ~/Downloads/School-Management--main`) und `./run.sh` starten.

### Wenn der Login nicht klappt: Verbindungstest

```bash
.venv/bin/python -m schulcloud.check
```

Der Test prüft die Erreichbarkeit, meldet sich an und zeigt die nächsten Termine.
Das Passwort wird verdeckt eingegeben und nicht gespeichert. Nur die Erreichbarkeit
prüfen: `.venv/bin/python -m schulcloud.check --no-login`.

Ausgabe im Erfolgsfall:

```
1) Erreichbarkeit von https://brandenburg.cloud
  ✅ Login-Seite erreichbar (HTTP 200)
2) Erwartete Endpunkte (401 = vorhanden, aber Login nötig)
  ✅ /api/v3/tasks: HTTP 401
3) Anmeldung
  ✅ Angemeldet als Mia Muster (Beispielschule)
  • verwendete Strategie: api
```

### Auf dem iPhone/iPad benutzen

Auf iOS lässt sich der Server nicht installieren. Zwei Wege:

* **Im gleichen WLAN:** auf dem Mac `HOST=0.0.0.0 ./run.sh` starten. Das Skript zeigt
  dann zusätzlich eine Adresse wie `http://192.168.2.31:5000`, die du am iPhone im
  Safari öffnen kannst – solange der Mac läuft und beide im selben WLAN sind.
  (Damit ist das Dashboard für alle Geräte im Netz erreichbar; nur im Heimnetz nutzen.)
* **Dauerhaft:** auf einem kleinen Server/Raspberry Pi betreiben – dann aber unbedingt
  HTTPS und ein zusätzliches Passwort davorschalten.

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
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q     # 28 Tests: Client, Parsing, Store, HTTP-API
```

---

## 3. Anmeldung an der Schul-Cloud

Es gibt **keine offizielle öffentliche API**. Die folgenden Endpunkte wurden gegen
`https://brandenburg.cloud` geprüft und werden vom Client verwendet:

| Endpunkt | Zweck |
|---|---|
| `POST /api/v3/authentication/local` | Login mit Nutzername/Passwort → JWT |
| `GET /api/v3/me` | angemeldeter Nutzer, Schule, Rollen |
| `GET /api/v3/tasks` | offene Aufgaben (Modul „Aufgaben") |
| `GET /api/v3/tasks/finished` | abgeschlossene und bewertete Aufgaben |
| `GET /api/v3/courses` | Kursübersicht inkl. Farbe |
| `GET /api/v3/news` | Ankündigungen (Quelle für Testtermine) |
| `POST /login` | Formular-Login – benötigt das `_csrf`-Feld der Login-Seite |

Die älteren Feathers-Services (`/api/v1/homework`, `/submissions`, `/lessons`) sind auf
dieser Instanz **nicht** erreichbar und werden nur noch als Fallback für abweichende
Instanzen versucht; danach greift die HTML-Scraper-Logik.

**Login-Strategien** (in dieser Reihenfolge, die erste funktionierende gewinnt):

| Strategie | Ablauf |
|---|---|
| `api` | `POST /api/v3/authentication/local` → JWT → `Authorization: Bearer …` |
| `form` | `GET /login` (CSRF-Token lesen) → `POST /login` → `jwt`-Cookie |
| `cookie` | vorhandenes JWT übernehmen (Login-Feld „Session-Token" oder Erweiterung) |

Wird das Passwort abgelehnt (HTTP 401), bricht der Client sofort ab, statt es beim
Formular-Login ein zweites Mal zu versuchen – so laufen keine Fehlversuche auf.

**Bei Zwei-Faktor-Anmeldung oder Single-Sign-on** funktioniert der direkte Login nicht.
Dann in der Schul-Cloud im Browser anmelden, das Cookie `jwt` kopieren und im Dashboard
unter „Alternative: Session-Token" einfügen – oder die Browser-Erweiterung nutzen.

**Umgang mit Zugangsdaten**

* Das Passwort wird nur zum Login durchgereicht und **nie gespeichert**.
* JWT und Session liegen ausschließlich im Arbeitsspeicher des lokalen Servers
  (`SESSIONS` in `app.py`) – nach einem Neustart ist eine neue Anmeldung nötig.
* In SQLite landen nur Aufgaben-Metadaten und der eigene Abhak-Status.
* Der Server bindet standardmäßig auf `127.0.0.1` und ist nicht von außen erreichbar.

### Aktualisierung

* **Button „Aktualisieren"** in der Kopfzeile (`POST /api/refresh`).
* **Automatisch** alle `SC_REFRESH_MINUTES` Minuten – im Hintergrund-Thread des Servers
  und zusätzlich im geöffneten Browser-Tab.
* **Per Erweiterung** durch `POST /api/ingest`.

---

## 4. Browser-Erweiterung (Ordner `browser-extension/`)

Für den Fall, dass der direkte Login nicht möglich ist (2FA, SSO): Die Erweiterung liest
das Session-Cookie der bereits angemeldeten Schul-Cloud aus und schickt es an das lokale
Dashboard – oder scrapt die sichtbare Aufgabenliste direkt im Browser.

**Installation (Chrome/Edge):**

1. In der `.env` ein `SC_INGEST_TOKEN=<beliebiges-geheimnis>` setzen, Server neu starten.
2. `chrome://extensions` öffnen → **Entwicklermodus** → **Entpackte Erweiterung laden**
   → Ordner `browser-extension/` wählen.
3. Im Popup Dashboard-URL und Ingest-Token eintragen, **Einstellungen speichern**.
4. In der Schul-Cloud anmelden, dann **„Session & Daten übertragen"** klicken.
   Auf der Aufgabenseite funktioniert zusätzlich **„Sichtbare Aufgaben scrapen"**.

`/api/ingest` ist ohne gesetztes `SC_INGEST_TOKEN` deaktiviert und prüft den Header
`X-Ingest-Token` per `secrets.compare_digest`. (Safari verlangt für eigene Erweiterungen
ein Apple-Entwicklerkonto und Xcode – deshalb ist die Erweiterung für Chrome/Edge gebaut.)

---

## 5. Datenanalyse & Parsing

`schulcloud/parser.py` normalisiert alle Quellen auf ein einheitliches Format:

```json
{
  "id": "hw:5f2b…", "kind": "homework|exam", "title": "…", "course": "Mathematik 10b",
  "due": "2026-09-03T14:00:00+02:00", "status": "open|submitted|graded",
  "finished": false, "url": "https://…/homework/5f2b…", "teacher": "Herr Neumann"
}
```

* **Kursübersicht** – `/api/v3/courses`; liefert Name und Anzeigefarbe.
* **Aufgabenmodul** – `/api/v3/tasks` und `/api/v3/tasks/finished`: Titel, Fach,
  Beschreibung, Fälligkeit. Entwürfe von Lehrkräften werden übersprungen.
* **Abgabetermine** – `parse_datetime()` versteht ISO-8601, Millisekunden-Timestamps und
  deutsche Schreibweisen (`03.09.2026, 14:00`, `3.9.26`). Fehlt ein Termin, sucht
  `find_deadline_in_text()` Formulierungen wie „Abgabe bis 12.09.2026 um 18:00".
* **Status** – aus dem `status`-Objekt der Aufgabe: `graded > 0` → **bewertet**,
  `submitted > 0` → **eingereicht**, sonst **offen**. Bewertete und in der Schul-Cloud
  abgeschlossene Aufgaben landen automatisch im Archiv.
* **Testankündigungen** – Titel, Beschreibungen, Ankündigungen (`/api/v3/news`),
  Kalendereinträge und Kursthemen werden gegen eine Stichwortliste geprüft:
  Klassenarbeit, Klausur, Lernkontrolle, Vokabeltest, Prüfung, Diktat, Referat,
  Präsentation … Treffer erscheinen als Typ **„Test / Arbeit"**; gewöhnliche
  Stundenplan- und Schultermine werden bewusst ignoriert.

## 6. Übersicht & Interaktivität

* **Sortierung nach Dringlichkeit:** überfällig → < 24 h → < 48 h → diese Woche → später →
  ohne Termin, innerhalb einer Stufe nach Abgabezeitpunkt.
* **Farbliche Warnungen:** roter Balken bei überfällig und < 24 h, orange bei < 48 h,
  gelb innerhalb einer Woche, grau danach. Dazu ein Countdown („in 13 Std. 59 Min.").
* **Abhaken:** Klick auf die Checkbox → Eintrag wandert ins Archiv, dort per
  „Zurückholen" reversibel. Der Status wird **lokal** in SQLite geführt, weil die
  Schul-Cloud kein Setzen von außen erlaubt – er bleibt deshalb auch nach einer
  Aktualisierung erhalten, selbst wenn die Aufgabe dort verschwindet.
* **Kennzahlen:** offen, überfällig, nächste 24 h / 48 h, Tests, erledigt.
* **Filter** nach Typ (Aufgaben / Tests / dringend), Kurs und Volltextsuche.

### HTTP-Endpunkte des Dashboards

| Methode & Pfad | Zweck |
|---|---|
| `GET /` | Oberfläche |
| `GET /api/status` | Login-Status, Nutzer, letzte Aktualisierung |
| `POST /api/login` | `{username,password}` \| `{jwt}` \| `{demo:true}` |
| `POST /api/logout` | Session verwerfen |
| `GET /api/items` | aktive Liste + Archiv + Kennzahlen |
| `POST /api/refresh` | Daten neu von der Schul-Cloud holen |
| `POST /api/items/<id>/done` | `{done:true\|false}` – abhaken / zurückholen |
| `POST /api/items/<id>/note` | eigene Notiz speichern |
| `POST /api/ingest` | Endpunkt der Browser-Erweiterung (Token nötig) |
| `GET /api/health` | Health-Check |

---

## 7. Projektstruktur

```
app.py                    Flask-App: Routen, Sessions, Hintergrund-Aktualisierung
schulcloud/client.py      Login-Wrapper (API / Formular+CSRF / Cookie) + Scraper-Fallback
schulcloud/parser.py      Normalisierung, Terminerkennung, Testerkennung, Dringlichkeit
schulcloud/store.py       SQLite: Abhak-Status, Notizen, Cache
schulcloud/check.py       Verbindungstest für die Kommandozeile
schulcloud/demo.py        Beispieldaten für den Demo-Modus
templates/index.html      Oberfläche (Tailwind)
static/js/app.js          Frontend-Logik (Fetch + DOM)
browser-extension/        Chrome-/Edge-Erweiterung (MV3) als Alternative zum Login
tests/                    pytest-Suite (28 Tests)
```

### Ohne CDN betreiben

Die Seite lädt Tailwind über das CDN, ein lokaler Build wird aber automatisch bevorzugt
(`static/css/tailwind.css` liegt bereits bei). Neu bauen:

```bash
npx tailwindcss -i static/css/input.css -o static/css/tailwind.css --minify
```

---

## 8. Hinweise

* Das Tool ist ein privates Hilfsmittel für den **eigenen** Account und liest nur Daten,
  die dieser Account ohnehin im Browser sieht.
* Ändert die Schul-Cloud ihre Endpunkte, brechen die Abfragen. Anpassungen betreffen
  dann fast immer nur `schulcloud/client.py`; `python -m schulcloud.check` zeigt sofort,
  welcher Endpunkt nicht mehr antwortet.
* Für den Dauerbetrieb hinter einem Reverse-Proxy unbedingt festen `SECRET_KEY`, HTTPS
  und einen zusätzlichen Zugriffsschutz vorsehen.
