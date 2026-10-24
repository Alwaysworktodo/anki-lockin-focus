"""Python-authoritative SQLite storage for Lock-In collectibles.

Review-time writes are bounded to one event plus the affected card/day and
card/session aggregates. Historical review events are never rewritten or
scanned on the normal answer path.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterable, Iterator, Mapping
from uuid import uuid4

from .catalog import Catalog, load_catalog, normalize_locale


STATE_SCHEMA_VERSION = 1
DEFAULT_PROFILE = "default"
DATABASE_NAME = "collectibles.sqlite3"
LEGACY_STATE_NAME = "state.json"


class StateError(RuntimeError):
    """Raised when collectible state cannot be safely opened or migrated."""


def default_state_path(addon_root: str | Path | None = None) -> Path:
    root = Path(addon_root) if addon_root is not None else Path(__file__).resolve().parent.parent
    return root / "user_files" / DATABASE_NAME


def _normal_database_path(path: str | Path | None) -> tuple[Path, Path]:
    requested = Path(path) if path is not None else default_state_path()
    if requested.suffix.lower() == ".json":
        return requested.with_name(DATABASE_NAME), requested
    return requested, requested.with_name(LEGACY_STATE_NAME)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _normal_profile_name(profile_name: str | None) -> str:
    value = str(profile_name or "").strip()
    return value or DEFAULT_PROFILE


def _normal_counter(counter: Any) -> int | str | None:
    if counter is None or counter == "":
        return None
    if isinstance(counter, bool):
        raise ValueError("undo_counter must not be a boolean")
    try:
        return int(counter)
    except (TypeError, ValueError):
        return str(counter)


def _counter_key(counter: Any) -> str | None:
    normalized = _normal_counter(counter)
    if normalized is None:
        return None
    prefix = "i" if isinstance(normalized, int) else "s"
    return f"{prefix}:{normalized}"


def _counter_from_key(value: str | None) -> int | str | None:
    if value is None:
        return None
    prefix, _, payload = value.partition(":")
    return int(payload) if prefix == "i" else payload


def _normal_rating(rating: int | str) -> str:
    if isinstance(rating, bool):
        raise ValueError("rating must be Again, Hard, Good, Easy, or 1-4")
    if isinstance(rating, int):
        try:
            return {1: "again", 2: "hard", 3: "good", 4: "easy"}[rating]
        except KeyError as exc:
            raise ValueError("numeric rating must be between 1 and 4") from exc
    value = str(rating).strip().lower()
    aliases = {
        "1": "again",
        "2": "hard",
        "3": "good",
        "4": "easy",
        "again": "again",
        "hard": "hard",
        "good": "good",
        "easy": "easy",
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise ValueError("rating must be Again, Hard, Good, Easy, or 1-4") from exc


def _timestamp_ms(value: datetime | int | float | str | None) -> int:
    if value is None:
        return int(datetime.now().timestamp() * 1000)
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(parsed.timestamp() * 1000)
    numeric = float(value)
    return int(numeric * 1000 if abs(numeric) < 100_000_000_000 else numeric)


def _local_day(value: str | date | None, timestamp_ms: int) -> str:
    if value is None:
        return datetime.fromtimestamp(timestamp_ms / 1000).date().isoformat()
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        parsed = date.fromisoformat(str(value))
    return parsed.isoformat()


def _longest_date_run(days: Iterable[date]) -> int:
    ordered = sorted(set(days))
    longest = 0
    current = 0
    previous: date | None = None
    for day in ordered:
        current = current + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        longest = max(longest, current)
        previous = day
    return longest


def _current_date_run(days: set[date], today: date) -> int:
    if today in days:
        cursor = today
    elif today - timedelta(days=1) in days:
        cursor = today - timedelta(days=1)
    else:
        return 0
    run = 0
    while cursor in days:
        run += 1
        cursor -= timedelta(days=1)
    return run


SCHEMA_V1 = """
CREATE TABLE profiles (
    name TEXT PRIMARY KEY,
    current_session_id TEXT,
    next_sequence INTEGER NOT NULL DEFAULT 1,
    equipped_cat_id TEXT
);

CREATE TABLE metrics (
    profile_name TEXT PRIMARY KEY REFERENCES profiles(name) ON DELETE CASCADE,
    resolved_cards_total INTEGER NOT NULL DEFAULT 0,
    recoveries_total INTEGER NOT NULL DEFAULT 0,
    qualified_days_total INTEGER NOT NULL DEFAULT 0,
    session_resolved_max INTEGER NOT NULL DEFAULT 0,
    daily_goal_streak_max INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE events (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name TEXT NOT NULL REFERENCES profiles(name) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    event_id TEXT,
    revlog_id TEXT,
    runtime_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    card_id TEXT NOT NULL,
    deck_id TEXT,
    rating TEXT NOT NULL CHECK (rating IN ('again', 'hard', 'good', 'easy')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    session_id TEXT NOT NULL,
    undo_counter_key TEXT,
    answered_at_ms INTEGER NOT NULL,
    local_day TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    undone_at TEXT,
    UNIQUE(profile_name, event_key)
);

CREATE UNIQUE INDEX events_profile_revlog_unique
ON events(profile_name, revlog_id) WHERE revlog_id IS NOT NULL;
CREATE UNIQUE INDEX events_profile_event_unique
ON events(profile_name, event_id) WHERE event_id IS NOT NULL;
CREATE INDEX events_card_day_active
ON events(profile_name, card_id, local_day, active, sequence);
CREATE INDEX events_session_card_active
ON events(profile_name, session_id, card_id, active, passed);
CREATE INDEX events_runtime_undo_active
ON events(profile_name, runtime_id, undo_counter_key, active);

CREATE TABLE card_day_state (
    profile_name TEXT NOT NULL REFERENCES profiles(name) ON DELETE CASCADE,
    local_day TEXT NOT NULL,
    card_id TEXT NOT NULL,
    resolved INTEGER NOT NULL CHECK (resolved IN (0, 1)),
    recovered INTEGER NOT NULL CHECK (recovered IN (0, 1)),
    PRIMARY KEY(profile_name, local_day, card_id)
);

CREATE TABLE session_card_state (
    profile_name TEXT NOT NULL REFERENCES profiles(name) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    PRIMARY KEY(profile_name, session_id, card_id)
);

CREATE TABLE sessions (
    profile_name TEXT NOT NULL REFERENCES profiles(name) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    resolved_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(profile_name, session_id)
);
CREATE INDEX sessions_resolved_count ON sessions(profile_name, resolved_count);

CREATE TABLE day_progress (
    profile_name TEXT NOT NULL REFERENCES profiles(name) ON DELETE CASCADE,
    local_day TEXT NOT NULL,
    resolved_count INTEGER NOT NULL DEFAULT 0,
    goal_met INTEGER NOT NULL DEFAULT 0 CHECK (goal_met IN (0, 1)),
    PRIMARY KEY(profile_name, local_day)
);
CREATE INDEX day_progress_goal ON day_progress(profile_name, goal_met, local_day);

CREATE TABLE unlocked_cats (
    profile_name TEXT NOT NULL REFERENCES profiles(name) ON DELETE CASCADE,
    cat_id TEXT NOT NULL,
    unlocked_at TEXT NOT NULL,
    source_event TEXT,
    seen_at TEXT,
    PRIMARY KEY(profile_name, cat_id)
);

CREATE TABLE migration_log (
    key TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL
);
"""


class CollectiblesStore:
    """Indexed SQLite repository for one selected Anki profile."""

    def __init__(
        self,
        state_path: str | Path | None = None,
        *,
        profile_name: str | None = None,
        catalog: Catalog | None = None,
        runtime_id: str | None = None,
    ) -> None:
        self.state_path, self.legacy_state_path = _normal_database_path(state_path)
        self.profile_name = _normal_profile_name(profile_name)
        self.catalog = catalog or load_catalog()
        self.runtime_id = str(runtime_id or uuid4())
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._open_database()
        try:
            self._migrate_schema()
            self._maybe_import_legacy_json()
            with self._transaction() as cursor:
                self._ensure_profile(cursor, self.profile_name)
        except Exception:
            self.close()
            raise

    def _open_database(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.state_path,
                timeout=0.1,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 100")
            connection.execute("PRAGMA journal_mode = WAL")
            # WAL + NORMAL is atomic and corruption-safe while avoiding one
            # physical sync for every reviewed card.
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA temp_store = MEMORY")
            self._db = connection
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            raise StateError(f"Unable to open collectible database at {self.state_path}: {exc}") from exc

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            raise StateError("Collectible database is closed")
        return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def __enter__(self) -> "CollectiblesStore":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown is nondeterministic.
        try:
            self.close()
        except Exception:
            pass

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self.db.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                yield cursor
            except Exception:
                self.db.rollback()
                raise
            else:
                self.db.commit()
            finally:
                cursor.close()

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self.db.cursor()
            cursor.execute("BEGIN")
            try:
                yield cursor
            except Exception:
                self.db.rollback()
                raise
            else:
                self.db.commit()
            finally:
                cursor.close()

    def _migrate_schema(self) -> None:
        try:
            current = int(self.db.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise StateError(f"Invalid collectible database at {self.state_path}: {exc}") from exc
        if current > STATE_SCHEMA_VERSION:
            raise StateError(f"Unsupported collectible database schema: {current}")
        if current == 0:
            try:
                self.db.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + SCHEMA_V1
                    + f"\nPRAGMA user_version = {STATE_SCHEMA_VERSION};\nCOMMIT;"
                )
            except sqlite3.DatabaseError as exc:
                if self.db.in_transaction:
                    self.db.rollback()
                raise StateError(f"Unable to initialize collectible database: {exc}") from exc
            current = 1
        # Future versions append explicit current -> next migrations here.
        if current != STATE_SCHEMA_VERSION:
            raise StateError(f"Unable to migrate collectible database schema {current}")

    def _ensure_profile(self, cursor: sqlite3.Cursor, profile_name: str) -> None:
        cursor.execute(
            "INSERT OR IGNORE INTO profiles(name) VALUES (?)",
            (profile_name,),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO metrics(profile_name) VALUES (?)",
            (profile_name,),
        )

    def set_profile(self, profile_name: str | None) -> None:
        chosen = _normal_profile_name(profile_name)
        if chosen == self.profile_name:
            return
        with self._transaction() as cursor:
            self._ensure_profile(cursor, chosen)
        self.profile_name = chosen

    def _maybe_import_legacy_json(self) -> None:
        if not self.legacy_state_path.is_file():
            return
        migration_key = f"legacy-json:{self.legacy_state_path.resolve()}"
        if self.db.execute("SELECT 1 FROM migration_log WHERE key = ?", (migration_key,)).fetchone():
            return
        try:
            legacy = json.loads(self.legacy_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Unable to read legacy collectible state: {exc}") from exc
        if not isinstance(legacy, dict) or legacy.get("schema_version") != 1:
            raise StateError("Unsupported legacy collectible state schema")
        profiles = legacy.get("profiles")
        if not isinstance(profiles, dict):
            raise StateError("Legacy collectible profiles must be an object")

        with self._transaction() as cursor:
            for profile_name, raw_profile in profiles.items():
                if not isinstance(raw_profile, dict):
                    raise StateError(f"Invalid legacy profile {profile_name!r}")
                name = _normal_profile_name(profile_name)
                self._ensure_profile(cursor, name)
                cursor.execute(
                    "UPDATE profiles SET current_session_id = ?, next_sequence = ?, "
                    "equipped_cat_id = ? WHERE name = ?",
                    (
                        raw_profile.get("current_session_id"),
                        max(1, int(raw_profile.get("next_sequence", 1))),
                        raw_profile.get("equipped_cat_id"),
                        name,
                    ),
                )
                events = raw_profile.get("events", {})
                if not isinstance(events, dict):
                    raise StateError(f"Invalid legacy events for {name!r}")
                for event_key, event in events.items():
                    if not isinstance(event, dict):
                        raise StateError(f"Invalid legacy event {event_key!r}")
                    rating = _normal_rating(event.get("rating", "again"))
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO events(
                            profile_name, event_key, event_id, revlog_id, runtime_id,
                            sequence, card_id, deck_id, rating, passed, session_id,
                            undo_counter_key, answered_at_ms, local_day, active,
                            created_at, undone_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            name,
                            str(event_key),
                            event.get("event_id"),
                            event.get("revlog_id"),
                            str(event.get("runtime_id") or "legacy"),
                            int(event.get("sequence", 0)),
                            str(event.get("card_id")),
                            event.get("deck_id"),
                            rating,
                            int(rating != "again"),
                            str(event.get("session_id") or "legacy"),
                            _counter_key(event.get("undo_counter")),
                            int(event.get("answered_at_ms", 0)),
                            str(event.get("local_day")),
                            int(bool(event.get("active", True))),
                            str(event.get("created_at") or _utc_now_iso()),
                            event.get("undone_at"),
                        ),
                    )
                unlocked = raw_profile.get("unlocked_cats", {})
                if not isinstance(unlocked, dict):
                    raise StateError(f"Invalid legacy unlocks for {name!r}")
                for cat_id, record in unlocked.items():
                    record = record if isinstance(record, dict) else {}
                    cursor.execute(
                        "INSERT OR IGNORE INTO unlocked_cats"
                        "(profile_name, cat_id, unlocked_at, source_event, seen_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            name,
                            str(cat_id),
                            str(record.get("unlocked_at") or _utc_now_iso()),
                            record.get("source_event"),
                            record.get("seen_at"),
                        ),
                    )
                self._rebuild_profile_from_events(cursor, name)
            cursor.execute(
                "INSERT INTO migration_log(key, completed_at) VALUES (?, ?)",
                (migration_key, _utc_now_iso()),
            )

    def _rebuild_profile_from_events(self, cursor: sqlite3.Cursor, profile_name: str) -> None:
        cursor.execute("DELETE FROM card_day_state WHERE profile_name = ?", (profile_name,))
        cursor.execute("DELETE FROM session_card_state WHERE profile_name = ?", (profile_name,))
        cursor.execute("DELETE FROM sessions WHERE profile_name = ?", (profile_name,))
        cursor.execute("DELETE FROM day_progress WHERE profile_name = ?", (profile_name,))
        cursor.execute(
            "UPDATE metrics SET resolved_cards_total=0, recoveries_total=0, "
            "qualified_days_total=0, session_resolved_max=0, daily_goal_streak_max=0 "
            "WHERE profile_name = ?",
            (profile_name,),
        )
        card_days = cursor.execute(
            "SELECT DISTINCT local_day, card_id FROM events "
            "WHERE profile_name = ? AND active = 1",
            (profile_name,),
        ).fetchall()
        for row in card_days:
            self._rebuild_card_day(cursor, profile_name, row["local_day"], row["card_id"])
        session_cards = cursor.execute(
            "SELECT DISTINCT session_id, card_id FROM events "
            "WHERE profile_name = ? AND active = 1 AND passed = 1",
            (profile_name,),
        ).fetchall()
        for row in session_cards:
            self._rebuild_session_card(cursor, profile_name, row["session_id"], row["card_id"])
        self._recompute_session_max(cursor, profile_name)
        self._recompute_daily_max(cursor, profile_name)

    def begin_session(self, session_id: str | None = None) -> str:
        chosen = str(session_id or f"session:{uuid4()}")
        if not chosen.strip():
            raise ValueError("session_id must not be empty")
        with self._transaction() as cursor:
            self._ensure_profile(cursor, self.profile_name)
            cursor.execute(
                "INSERT OR IGNORE INTO sessions(profile_name, session_id) VALUES (?, ?)",
                (self.profile_name, chosen),
            )
            cursor.execute(
                "UPDATE profiles SET current_session_id = ? WHERE name = ?",
                (chosen, self.profile_name),
            )
        return chosen

    def end_session(self, session_id: str | None = None) -> bool:
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT current_session_id FROM profiles WHERE name = ?",
                (self.profile_name,),
            ).fetchone()
            current = row[0] if row else None
            if current is None or (session_id is not None and str(session_id) != current):
                return False
            cursor.execute(
                "UPDATE profiles SET current_session_id = NULL WHERE name = ?",
                (self.profile_name,),
            )
            return True

    def record_answer(
        self,
        *,
        card_id: int | str,
        rating: int | str,
        event_id: int | str | None = None,
        revlog_id: int | str | None = None,
        session_id: str | None = None,
        undo_counter: int | str | None = None,
        answered_at: datetime | int | float | str | None = None,
        local_day: str | date | None = None,
        deck_id: int | str | None = None,
        activate_session: bool = True,
    ) -> dict[str, Any]:
        normalized_rating = _normal_rating(rating)
        timestamp_ms = _timestamp_ms(answered_at)
        day = _local_day(local_day, timestamp_ms)
        normalized_event_id = str(event_id) if event_id is not None else None
        normalized_revlog_id = str(revlog_id) if revlog_id is not None else None
        card_key = str(card_id)
        counter_key = _counter_key(undo_counter)

        with self._transaction() as cursor:
            self._ensure_profile(cursor, self.profile_name)
            duplicate = None
            if normalized_revlog_id is not None:
                duplicate = cursor.execute(
                    "SELECT event_key FROM events WHERE profile_name = ? AND revlog_id = ?",
                    (self.profile_name, normalized_revlog_id),
                ).fetchone()
            if duplicate is None and normalized_event_id is not None:
                duplicate = cursor.execute(
                    "SELECT event_key FROM events WHERE profile_name = ? AND event_id = ?",
                    (self.profile_name, normalized_event_id),
                ).fetchone()
            if duplicate is not None:
                return {
                    "changed": False,
                    "duplicate": True,
                    "event_key": duplicate["event_key"],
                    "new_unlock_ids": [],
                    "metrics": self._metrics_snapshot(cursor),
                }

            profile = cursor.execute(
                "SELECT current_session_id, next_sequence FROM profiles WHERE name = ?",
                (self.profile_name,),
            ).fetchone()
            chosen_session = str(
                session_id or profile["current_session_id"] or f"session:{uuid4()}"
            )
            sequence = int(profile["next_sequence"])
            cursor.execute(
                "UPDATE profiles SET current_session_id = ?, next_sequence = ? WHERE name = ?",
                (chosen_session if activate_session else profile["current_session_id"],
                 sequence + 1, self.profile_name),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO sessions(profile_name, session_id) VALUES (?, ?)",
                (self.profile_name, chosen_session),
            )
            if normalized_revlog_id is not None:
                event_key = f"revlog:{normalized_revlog_id}"
            elif normalized_event_id is not None:
                event_key = f"event:{normalized_event_id}"
            else:
                event_key = f"generated:{uuid4()}"

            try:
                cursor.execute(
                    """
                    INSERT INTO events(
                        profile_name, event_key, event_id, revlog_id, runtime_id,
                        sequence, card_id, deck_id, rating, passed, session_id,
                        undo_counter_key, answered_at_ms, local_day, active,
                        created_at, undone_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL)
                    """,
                    (
                        self.profile_name,
                        event_key,
                        normalized_event_id,
                        normalized_revlog_id,
                        self.runtime_id,
                        sequence,
                        card_key,
                        str(deck_id) if deck_id is not None else None,
                        normalized_rating,
                        int(normalized_rating != "again"),
                        chosen_session,
                        counter_key,
                        timestamp_ms,
                        day,
                        _utc_now_iso(),
                    ),
                )
            except sqlite3.IntegrityError:
                # A concurrent store may have committed the same confirmed
                # answer after our preflight query.
                duplicate = cursor.execute(
                    "SELECT event_key FROM events WHERE profile_name = ? "
                    "AND (revlog_id = ? OR event_id = ?)",
                    (self.profile_name, normalized_revlog_id, normalized_event_id),
                ).fetchone()
                if duplicate is None:
                    raise
                return {
                    "changed": False,
                    "duplicate": True,
                    "event_key": duplicate["event_key"],
                    "new_unlock_ids": [],
                    "metrics": self._metrics_snapshot(cursor),
                }

            self._refresh_affected(cursor, day, card_key, chosen_session)
            metrics = self._metrics_snapshot(cursor)
            new_unlock_ids = self._unlock_eligible(cursor, metrics, source_event=event_key)
            return {
                "changed": True,
                "duplicate": False,
                "event_key": event_key,
                "new_unlock_ids": new_unlock_ids,
                "metrics": metrics,
            }

    def _refresh_affected(
        self,
        cursor: sqlite3.Cursor,
        local_day: str,
        card_id: str,
        session_id: str,
    ) -> None:
        self._rebuild_card_day(cursor, self.profile_name, local_day, card_id)
        self._rebuild_session_card(cursor, self.profile_name, session_id, card_id)

    def _rebuild_card_day(
        self, cursor: sqlite3.Cursor, profile_name: str, local_day: str, card_id: str
    ) -> None:
        old = cursor.execute(
            "SELECT resolved, recovered FROM card_day_state "
            "WHERE profile_name = ? AND local_day = ? AND card_id = ?",
            (profile_name, local_day, card_id),
        ).fetchone()
        old_resolved = int(old["resolved"]) if old else 0
        old_recovered = int(old["recovered"]) if old else 0
        ratings = cursor.execute(
            "SELECT rating FROM events WHERE profile_name = ? AND local_day = ? "
            "AND card_id = ? AND active = 1 ORDER BY sequence",
            (profile_name, local_day, card_id),
        ).fetchall()
        saw_again = False
        resolved = False
        recovered = False
        for row in ratings:
            if row["rating"] == "again":
                saw_again = True
            else:
                resolved = True
                recovered = recovered or saw_again

        if ratings:
            cursor.execute(
                "INSERT INTO card_day_state(profile_name, local_day, card_id, resolved, recovered) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(profile_name, local_day, card_id) "
                "DO UPDATE SET resolved=excluded.resolved, recovered=excluded.recovered",
                (profile_name, local_day, card_id, int(resolved), int(recovered)),
            )
        else:
            cursor.execute(
                "DELETE FROM card_day_state WHERE profile_name = ? AND local_day = ? AND card_id = ?",
                (profile_name, local_day, card_id),
            )

        resolved_delta = int(resolved) - old_resolved
        recovered_delta = int(recovered) - old_recovered
        if resolved_delta or recovered_delta:
            cursor.execute(
                "UPDATE metrics SET resolved_cards_total = resolved_cards_total + ?, "
                "recoveries_total = recoveries_total + ? WHERE profile_name = ?",
                (resolved_delta, recovered_delta, profile_name),
            )
        if resolved_delta:
            self._update_day_progress(cursor, profile_name, local_day, resolved_delta)

    def _update_day_progress(
        self, cursor: sqlite3.Cursor, profile_name: str, local_day: str, resolved_delta: int
    ) -> None:
        old = cursor.execute(
            "SELECT resolved_count, goal_met FROM day_progress "
            "WHERE profile_name = ? AND local_day = ?",
            (profile_name, local_day),
        ).fetchone()
        old_count = int(old["resolved_count"]) if old else 0
        old_goal = int(old["goal_met"]) if old else 0
        new_count = max(0, old_count + resolved_delta)
        new_goal = int(new_count >= self.catalog.daily_goal)
        cursor.execute(
            "INSERT INTO day_progress(profile_name, local_day, resolved_count, goal_met) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(profile_name, local_day) DO UPDATE SET "
            "resolved_count=excluded.resolved_count, goal_met=excluded.goal_met",
            (profile_name, local_day, new_count, new_goal),
        )
        if new_goal != old_goal:
            cursor.execute(
                "UPDATE metrics SET qualified_days_total = qualified_days_total + ? "
                "WHERE profile_name = ?",
                (new_goal - old_goal, profile_name),
            )
            self._recompute_daily_max(cursor, profile_name)

    def _rebuild_session_card(
        self, cursor: sqlite3.Cursor, profile_name: str, session_id: str, card_id: str
    ) -> None:
        old_exists = cursor.execute(
            "SELECT 1 FROM session_card_state WHERE profile_name = ? AND session_id = ? AND card_id = ?",
            (profile_name, session_id, card_id),
        ).fetchone() is not None
        new_exists = cursor.execute(
            "SELECT 1 FROM events WHERE profile_name = ? AND session_id = ? AND card_id = ? "
            "AND active = 1 AND passed = 1 LIMIT 1",
            (profile_name, session_id, card_id),
        ).fetchone() is not None
        delta = int(new_exists) - int(old_exists)
        if not delta:
            return
        if new_exists:
            cursor.execute(
                "INSERT INTO session_card_state(profile_name, session_id, card_id) VALUES (?, ?, ?)",
                (profile_name, session_id, card_id),
            )
        else:
            cursor.execute(
                "DELETE FROM session_card_state WHERE profile_name = ? AND session_id = ? AND card_id = ?",
                (profile_name, session_id, card_id),
            )
        cursor.execute(
            "INSERT OR IGNORE INTO sessions(profile_name, session_id) VALUES (?, ?)",
            (profile_name, session_id),
        )
        cursor.execute(
            "UPDATE sessions SET resolved_count = resolved_count + ? "
            "WHERE profile_name = ? AND session_id = ?",
            (delta, profile_name, session_id),
        )
        if delta > 0:
            count = cursor.execute(
                "SELECT resolved_count FROM sessions WHERE profile_name = ? AND session_id = ?",
                (profile_name, session_id),
            ).fetchone()[0]
            cursor.execute(
                "UPDATE metrics SET session_resolved_max = MAX(session_resolved_max, ?) "
                "WHERE profile_name = ?",
                (count, profile_name),
            )
        else:
            self._recompute_session_max(cursor, profile_name)

    def _qualified_dates(self, cursor: sqlite3.Cursor, profile_name: str) -> set[date]:
        rows = cursor.execute(
            "SELECT local_day FROM day_progress WHERE profile_name = ? AND goal_met = 1",
            (profile_name,),
        ).fetchall()
        return {date.fromisoformat(row[0]) for row in rows}

    def _recompute_daily_max(self, cursor: sqlite3.Cursor, profile_name: str) -> None:
        longest = _longest_date_run(self._qualified_dates(cursor, profile_name))
        cursor.execute(
            "UPDATE metrics SET daily_goal_streak_max = ? WHERE profile_name = ?",
            (longest, profile_name),
        )

    def _recompute_session_max(self, cursor: sqlite3.Cursor, profile_name: str) -> None:
        maximum = cursor.execute(
            "SELECT COALESCE(MAX(resolved_count), 0) FROM sessions WHERE profile_name = ?",
            (profile_name,),
        ).fetchone()[0]
        cursor.execute(
            "UPDATE metrics SET session_resolved_max = ? WHERE profile_name = ?",
            (int(maximum), profile_name),
        )

    def _metrics_snapshot(
        self, cursor: sqlite3.Cursor, *, today: date | None = None
    ) -> dict[str, int]:
        row = cursor.execute(
            "SELECT m.*, p.current_session_id FROM metrics m JOIN profiles p "
            "ON p.name = m.profile_name WHERE m.profile_name = ?",
            (self.profile_name,),
        ).fetchone()
        if row is None:
            self._ensure_profile(cursor, self.profile_name)
            return self._metrics_snapshot(cursor, today=today)
        current_session = row["current_session_id"]
        current_count = 0
        if current_session:
            session = cursor.execute(
                "SELECT resolved_count FROM sessions WHERE profile_name = ? AND session_id = ?",
                (self.profile_name, current_session),
            ).fetchone()
            current_count = int(session[0]) if session else 0
        qualified_dates = self._qualified_dates(cursor, self.profile_name)
        return {
            "resolved_cards_total": int(row["resolved_cards_total"]),
            "session_resolved_current": current_count,
            "session_resolved_max": int(row["session_resolved_max"]),
            "recoveries_total": int(row["recoveries_total"]),
            "qualified_days_total": int(row["qualified_days_total"]),
            "daily_goal_streak_current": _current_date_run(
                qualified_dates, today or date.today()
            ),
            "daily_goal_streak_max": int(row["daily_goal_streak_max"]),
        }

    def _unlock_eligible(
        self,
        cursor: sqlite3.Cursor,
        metrics: Mapping[str, int],
        *,
        source_event: str | None,
    ) -> list[str]:
        unlocked = {
            row[0]
            for row in cursor.execute(
                "SELECT cat_id FROM unlocked_cats WHERE profile_name = ?",
                (self.profile_name,),
            ).fetchall()
        }
        newly_unlocked: list[str] = []
        now = _utc_now_iso()
        for entry in self.catalog.entries:
            if entry.id not in unlocked and int(metrics.get(entry.metric, 0)) >= entry.threshold:
                cursor.execute(
                    "INSERT INTO unlocked_cats(profile_name, cat_id, unlocked_at, source_event) "
                    "VALUES (?, ?, ?, ?)",
                    (self.profile_name, entry.id, now, source_event),
                )
                unlocked.add(entry.id)
                newly_unlocked.append(entry.id)
        if newly_unlocked:
            cursor.execute(
                "UPDATE profiles SET equipped_cat_id = COALESCE(equipped_cat_id, ?) WHERE name = ?",
                (newly_unlocked[0], self.profile_name),
            )
        return newly_unlocked

    def _event_identity(self, undo_counter, revlog_id):
        if revlog_id is not None:
            return "revlog_id = ?", str(revlog_id)
        counter_key = _counter_key(undo_counter)
        if counter_key is None:
            raise ValueError("undo_counter or revlog_id is required")
        return "undo_counter_key = ?", counter_key

    def undo(
        self,
        undo_counter: int | str | None = None,
        *,
        revlog_id: int | str | None = None,
    ) -> dict[str, Any]:
        identity_sql, identity = self._event_identity(undo_counter, revlog_id)
        with self._transaction() as cursor:
            rows = cursor.execute(
                "SELECT event_key, local_day, card_id, session_id FROM events "
                "WHERE profile_name = ? AND runtime_id = ? AND " + identity_sql + " AND active = 1 "
                "ORDER BY sequence",
                (self.profile_name, self.runtime_id, identity),
            ).fetchall()
            if not rows:
                return {"changed": False, "reversed_event_keys": [],
                        "metrics": self._metrics_snapshot(cursor)}
            cursor.execute(
                "UPDATE events SET active = 0, undone_at = ? WHERE profile_name = ? "
                "AND runtime_id = ? AND " + identity_sql + " AND active = 1",
                (_utc_now_iso(), self.profile_name, self.runtime_id, identity),
            )
            card_days = {(row["local_day"], row["card_id"]) for row in rows}
            session_cards = {(row["session_id"], row["card_id"]) for row in rows}
            for local_day, card_id in card_days:
                self._rebuild_card_day(cursor, self.profile_name, local_day, card_id)
            for session_id, card_id in session_cards:
                self._rebuild_session_card(cursor, self.profile_name, session_id, card_id)
            metrics = self._metrics_snapshot(cursor)
            # Ownership remains insertion-only across undo/redo.
            self._unlock_eligible(cursor, metrics, source_event=None)
            return {"changed": True, "reversed_event_keys": [row["event_key"] for row in rows],
                    "metrics": metrics}

    def redo(
        self,
        undo_counter: int | str | None = None,
        *,
        revlog_id: int | str | None = None,
    ) -> dict[str, Any]:
        identity_sql, identity = self._event_identity(undo_counter, revlog_id)
        with self._transaction() as cursor:
            rows = cursor.execute(
                "SELECT event_key, local_day, card_id, session_id FROM events "
                "WHERE profile_name = ? AND runtime_id = ? AND " + identity_sql + " AND active = 0 "
                "ORDER BY sequence",
                (self.profile_name, self.runtime_id, identity),
            ).fetchall()

            if not rows:
                return {
                    "changed": False,
                    "restored_event_keys": [],
                    "new_unlock_ids": [],
                    "metrics": self._metrics_snapshot(cursor),
                }

            event_keys = [row["event_key"] for row in rows]
            placeholders = ",".join("?" for _ in event_keys)
            cursor.execute(
                f"UPDATE events SET active = 1, undone_at = NULL WHERE profile_name = ? "
                f"AND runtime_id = ? AND event_key IN ({placeholders}) AND active = 0",
                (self.profile_name, self.runtime_id, *event_keys),
            )

            card_days = {(row["local_day"], row["card_id"]) for row in rows}
            session_cards = {(row["session_id"], row["card_id"]) for row in rows}
            for local_day, card_id in card_days:
                self._rebuild_card_day(cursor, self.profile_name, local_day, card_id)
            for session_id, card_id in session_cards:
                self._rebuild_session_card(cursor, self.profile_name, session_id, card_id)

            metrics = self._metrics_snapshot(cursor)
            new_unlock_ids: list[str] = []
            for k in event_keys:
                new_unlock_ids.extend(self._unlock_eligible(cursor, metrics, source_event=k))
            return {
                "changed": True,
                "restored_event_keys": event_keys,
                "new_unlock_ids": new_unlock_ids,
                "metrics": metrics,
            }

    def equip(self, cat_id: str | None) -> dict[str, Any]:
        with self._transaction() as cursor:
            if cat_id is not None:
                if cat_id not in self.catalog.by_id:
                    raise KeyError(f"Unknown cat: {cat_id}")
                owned = cursor.execute(
                    "SELECT 1 FROM unlocked_cats WHERE profile_name = ? AND cat_id = ?",
                    (self.profile_name, cat_id),
                ).fetchone()
                if owned is None:
                    raise ValueError("Only unlocked cats can be equipped")
            cursor.execute(
                "UPDATE profiles SET equipped_cat_id = ? WHERE name = ?",
                (cat_id, self.profile_name),
            )
            return {"changed": True, "equipped_cat_id": cat_id}

    def mark_seen(self, cat_ids: Iterable[str] | None = None) -> dict[str, Any]:
        requested = None if cat_ids is None else {str(cat_id) for cat_id in cat_ids}
        with self._transaction() as cursor:
            unseen = cursor.execute(
                "SELECT cat_id FROM unlocked_cats WHERE profile_name = ? AND seen_at IS NULL",
                (self.profile_name,),
            ).fetchall()
            changed = [row[0] for row in unseen if requested is None or row[0] in requested]
            if changed:
                placeholders = ",".join("?" for _ in changed)
                cursor.execute(
                    f"UPDATE unlocked_cats SET seen_at = ? WHERE profile_name = ? "
                    f"AND cat_id IN ({placeholders})",
                    (_utc_now_iso(), self.profile_name, *changed),
                )
            return {"changed": bool(changed), "seen_cat_ids": changed}

    def snapshot(
        self,
        locale: str = "de",
        asset_url_prefix: str = "",
        *,
        today: date | None = None,
    ) -> dict[str, Any]:
        language = normalize_locale(locale)
        with self._read_transaction() as cursor:
            metrics = self._metrics_snapshot(cursor, today=today)
            unlocked_rows = cursor.execute(
                "SELECT cat_id, unlocked_at, seen_at FROM unlocked_cats WHERE profile_name = ?",
                (self.profile_name,),
            ).fetchall()
            unlocked = {row["cat_id"]: row for row in unlocked_rows}
            profile = cursor.execute(
                "SELECT equipped_cat_id FROM profiles WHERE name = ?",
                (self.profile_name,),
            ).fetchone()
            equipped_id = profile[0] if profile else None

            cats: list[dict[str, Any]] = []
            locked_candidates: list[tuple[float, int, dict[str, Any]]] = []
            for entry in self.catalog.entries:
                localized = entry.localized(language, asset_url_prefix)
                unlock_record = unlocked.get(entry.id)
                current = int(metrics.get(entry.metric, 0))
                localized.update(
                    {
                        "unlocked": unlock_record is not None,
                        "seen": bool(unlock_record and unlock_record["seen_at"]),
                        "unlocked_at": unlock_record["unlocked_at"] if unlock_record else None,
                        "progress": {"current": current, "target": entry.threshold},
                        "localized_reason": localized["localized_description"],
                    }
                )
                cats.append(localized)
                if unlock_record is None:
                    locked_candidates.append(
                        (min(1.0, current / entry.threshold), -entry.sort_order, localized)
                    )

            next_milestone = None
            if locked_candidates:
                candidate = max(locked_candidates, key=lambda item: (item[0], item[1]))[2]
                next_milestone = {
                    "cat_id": candidate["id"],
                    "localized_label": candidate["localized_name"],
                    "asset_url": candidate["asset_url"],
                    "current": candidate["progress"]["current"],
                    "target": candidate["progress"]["target"],
                }

            equipped = None
            if equipped_id in unlocked and equipped_id in self.catalog.by_id:
                localized = self.catalog.localized_entry(equipped_id, language, asset_url_prefix)
                if localized:
                    equipped = {
                        "id": localized["id"],
                        "localized_name": localized["localized_name"],
                        "localized_alt": localized["localized_alt"],
                        "asset_url": localized["asset_url"],
                    }
            catalog_ids = set(self.catalog.by_id)
            return {
                "schema_version": STATE_SCHEMA_VERSION,
                "locale": language,
                "enabled": True,
                "daily_goal": self.catalog.daily_goal,
                "unlocked_count": len(catalog_ids.intersection(unlocked)),
                "total_count": len(self.catalog.entries),
                "unseen_count": sum(
                    1
                    for cat_id, row in unlocked.items()
                    if cat_id in catalog_ids and not row["seen_at"]
                ),
                "equipped": equipped,
                "next_milestone": next_milestone,
                "metrics": metrics,
                "cats": cats,
            }

    def unlock_events(
        self,
        cat_ids: Iterable[str],
        locale: str = "de",
        asset_url_prefix: str = "",
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for cat_id in cat_ids:
            localized = self.catalog.localized_entry(cat_id, locale, asset_url_prefix)
            if localized:
                events.append(
                    {
                        "id": cat_id,
                        "localized_name": localized["localized_name"],
                        "localized_reason": localized["localized_description"],
                        "asset_url": localized["asset_url"],
                    }
                )
        return events

    def raw_state(self) -> dict[str, Any]:
        """Return the legacy JSON-shaped diagnostic view without mutating state."""

        with self._read_transaction() as cursor:
            output: dict[str, Any] = {
                "schema_version": STATE_SCHEMA_VERSION,
                "profiles": {},
            }
            profiles = cursor.execute(
                "SELECT name, current_session_id, next_sequence, equipped_cat_id "
                "FROM profiles ORDER BY name"
            ).fetchall()
            for profile in profiles:
                name = profile["name"]
                metrics_row = cursor.execute(
                    "SELECT * FROM metrics WHERE profile_name = ?", (name,)
                ).fetchone()
                events: dict[str, Any] = {}
                for row in cursor.execute(
                    "SELECT * FROM events WHERE profile_name = ? ORDER BY sequence", (name,)
                ).fetchall():
                    events[row["event_key"]] = {
                        "event_id": row["event_id"],
                        "revlog_id": row["revlog_id"],
                        "runtime_id": row["runtime_id"],
                        "sequence": int(row["sequence"]),
                        "card_id": row["card_id"],
                        "deck_id": row["deck_id"],
                        "rating": row["rating"],
                        "passed": bool(row["passed"]),
                        "session_id": row["session_id"],
                        "undo_counter": _counter_from_key(row["undo_counter_key"]),
                        "answered_at_ms": int(row["answered_at_ms"]),
                        "local_day": row["local_day"],
                        "active": bool(row["active"]),
                        "created_at": row["created_at"],
                        "undone_at": row["undone_at"],
                    }
                unlocked = {
                    row["cat_id"]: {
                        "unlocked_at": row["unlocked_at"],
                        "source_event": row["source_event"],
                        "seen_at": row["seen_at"],
                    }
                    for row in cursor.execute(
                        "SELECT * FROM unlocked_cats WHERE profile_name = ? ORDER BY cat_id",
                        (name,),
                    ).fetchall()
                }
                current_session = profile["current_session_id"]
                current_count = 0
                if current_session:
                    session = cursor.execute(
                        "SELECT resolved_count FROM sessions WHERE profile_name = ? AND session_id = ?",
                        (name, current_session),
                    ).fetchone()
                    current_count = int(session[0]) if session else 0
                qualified_dates = self._qualified_dates(cursor, name)
                output["profiles"][name] = {
                    "next_sequence": int(profile["next_sequence"]),
                    "current_session_id": current_session,
                    "events": events,
                    "metrics": {
                        "resolved_cards_total": int(metrics_row["resolved_cards_total"]),
                        "session_resolved_current": current_count,
                        "session_resolved_max": int(metrics_row["session_resolved_max"]),
                        "recoveries_total": int(metrics_row["recoveries_total"]),
                        "qualified_days_total": int(metrics_row["qualified_days_total"]),
                        "daily_goal_streak_current": _current_date_run(
                            qualified_dates, date.today()
                        ),
                        "daily_goal_streak_max": int(metrics_row["daily_goal_streak_max"]),
                    },
                    "unlocked_cats": unlocked,
                    "equipped_cat_id": profile["equipped_cat_id"],
                }
            return output


__all__ = [
    "DATABASE_NAME",
    "DEFAULT_PROFILE",
    "STATE_SCHEMA_VERSION",
    "CollectiblesStore",
    "StateError",
    "default_state_path",
]
