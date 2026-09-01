"""Lokale Persistenz (SQLite): Abhak-Status, Notizen und Daten-Cache.

Der Erledigt-Status wird bewusst *lokal* gefuehrt: die Schul-Cloud kennt keine
Moeglichkeit, eine Aufgabe von aussen als erledigt zu markieren. Dadurch bleibt
ein Haken auch erhalten, wenn eine Aufgabe zwischenzeitlich aus der API
verschwindet - und das Archiv bleibt vollstaendig.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS item_state (
    item_id     TEXT PRIMARY KEY,
    done        INTEGER NOT NULL DEFAULT 0,
    done_at     TEXT,
    note        TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_cache (
    item_id     TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str | Path = "data/dashboard.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Abhak-Status
    # ------------------------------------------------------------------
    def set_done(self, item_id: str, done: bool, note: Optional[str] = None) -> dict:
        now = _now()
        with _LOCK, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO item_state (item_id, done, done_at, note, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    done = excluded.done,
                    done_at = excluded.done_at,
                    note = COALESCE(excluded.note, item_state.note),
                    updated_at = excluded.updated_at
                """,
                (item_id, int(done), now if done else None, note, now),
            )
        return {"item_id": item_id, "done": done, "done_at": now if done else None}

    def set_note(self, item_id: str, note: str) -> None:
        now = _now()
        with _LOCK, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO item_state (item_id, done, note, updated_at)
                VALUES (?, 0, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    note = excluded.note, updated_at = excluded.updated_at
                """,
                (item_id, note, now),
            )

    def states(self) -> dict[str, dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM item_state").fetchall()
        return {
            row["item_id"]: {
                "done": bool(row["done"]),
                "done_at": row["done_at"],
                "note": row["note"] or "",
            }
            for row in rows
        }

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def save_items(self, items: list[dict]) -> None:
        now = _now()
        with _LOCK, self._conn() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO item_cache (item_id, payload, first_seen, last_seen)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        payload = excluded.payload, last_seen = excluded.last_seen
                    """,
                    (item["id"], json.dumps(item, ensure_ascii=False), now, now),
                )

    def cached_items(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT payload FROM item_cache ORDER BY last_seen DESC"
            ).fetchall()
        items = []
        for row in rows:
            try:
                items.append(json.loads(row["payload"]))
            except json.JSONDecodeError:
                continue
        return items

    def prune_cache(self, keep_ids: set[str]) -> int:
        """Entfernt Cache-Eintraege, die weder aktuell noch abgehakt sind."""
        states = self.states()
        with _LOCK, self._conn() as conn:
            rows = conn.execute("SELECT item_id FROM item_cache").fetchall()
            stale = [
                r["item_id"]
                for r in rows
                if r["item_id"] not in keep_ids and not states.get(r["item_id"], {}).get("done")
            ]
            conn.executemany("DELETE FROM item_cache WHERE item_id = ?", [(i,) for i in stale])
        return len(stale)

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------
    def set_meta(self, key: str, value: Any) -> None:
        with _LOCK, self._conn() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
