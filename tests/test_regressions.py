"""Execute backend functions with a small Anki adapter and real SQLite stores.

Only hook registration and Qt startup are omitted. The functions themselves are
compiled unchanged from the checkout, rather than reimplemented in the tests.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace as NS, ModuleType

import pytest

from collectibles import CollectiblesStore, StateError, load_catalog

ROOT = Path(__file__).resolve().parents[1]


class Timer:
    def __init__(self, parent=None):
        self.active = False
        self.deleted = False
        self.delay = 0
        self.timeout = NS(connect=lambda callback: setattr(self, "callback", callback))

    def setSingleShot(self, value):
        assert value

    def isActive(self):
        return self.active

    def start(self, delay):
        self.active = True
        self.delay = delay

    def stop(self):
        self.active = False

    def deleteLater(self):
        self.deleted = True

    def fire(self):
        assert self.active
        self.active = False
        self.callback()


class Revlog:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER)")
        self.queries = 0

    def scalar(self, sql, *args):
        self.queries += 1
        row = self.db.execute(sql, args).fetchone()
        return row[0] if row else None


@pytest.fixture
def backend(tmp_path):
    revlog = Revlog()
    col = NS(db=revlog, counter=0)
    col.undo_status = lambda: NS(last_step=col.counter)
    scripts, unlocks = [], []
    store = CollectiblesStore(tmp_path / "collection.sqlite3", profile_name="User")
    names = {
        "_profile_name", "get_collectibles_store", "_schedule_retry",
        "_queue_collection_write", "_refill_retry_queue", "_needs_collection_write",
        "_persist_collection_answer", "_drain_retry_queue", "record_collectible_answer",
        "_notify_gameplay", "_set_answer_state", "_reconcile_undo_redo",
        "undo_collectible_answer", "on_reviewer_did_answer_card", "on_state_did_undo",
        "on_operation_did_execute", "_cancel_collection_retries", "on_profile_will_close",
        "on_profile_did_open", "_reward_asset_path", "_reward_thumbnail",
        "on_js_message", "_bridge_payload", "_bounded_number",
    }
    tree = ast.parse((ROOT / "__init__.py").read_text())
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    env = {
        "__file__": str(ROOT / "__init__.py"), "__name__": "lockin_test",
        "mw": NS(col=col, state="review", pm=NS(name="User"),
                 reviewer=NS(web=NS(eval=scripts.append))),
        "QTimer": Timer, "Path": Path, "datetime": datetime, "time": time,
        "json": json, "math": __import__("math"), "load_catalog": load_catalog,
        "CollectiblesStore": CollectiblesStore, "StateError": StateError,
        "_log": logging.getLogger("lockin-regressions"),
        "_collectibles_store": store, "_review_session_id": "session-a",
        "_answer_undo_counters": {}, "_tracked_answers": [], "_retry_queue": [],
        "_pending_redo_counters": [], "_pending_unlock_notifications": [],
        "_retry_timer": None, "_retry_delay_ms": 250, "_retry_overflow_pending": False,
        "_MAX_RETRY_QUEUE_SIZE": 100, "_MAX_BRIDGE_MESSAGE_BYTES": 4096,
        "_current_rating_map": {1: "again", 2: "hard", 3: "good", 4: "easy"},
        "get_config": lambda: {"collectibles_enabled": True},
        "setup_collection_action": lambda: None,
        "push_collection_snapshot": lambda ids=None: unlocks.extend(ids or []),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / "__init__.py"), "exec"), env)
    b = NS(env=env, store=store, col=col, revlog=revlog, scripts=scripts, unlocks=unlocks)

    def answer(card_id=1, revlog_id=1001, missing_identity=False):
        col.counter += 1
        if not missing_identity:
            revlog.db.execute("INSERT INTO revlog VALUES (?, ?)", (revlog_id, card_id))
        env["on_reviewer_did_answer_card"](None, NS(id=card_id, did=1), 3)
        env["on_operation_did_execute"](None)
        return col.counter

    def undo(counter, revlog_id=1001):
        revlog.db.execute("DELETE FROM revlog WHERE id=?", (revlog_id,))
        col.counter += 1  # inverse operations also consume a new Anki counter
        env["on_state_did_undo"](NS(counter=counter))
        env["on_operation_did_execute"](None)

    def redo(card_id=1, revlog_id=1001):
        revlog.db.execute("INSERT INTO revlog VALUES (?, ?)", (revlog_id, card_id))
        col.counter += 1
        env["on_operation_did_execute"](None)
        return col.counter

    def drain():
        for _ in range(200):
            if not env["_retry_queue"]:
                return
            env["_retry_timer"].fire()
        pytest.fail("retry queue did not converge")

    b.answer, b.undo, b.redo, b.drain = answer, undo, redo, drain
    yield b
    if not getattr(store, "_closed", False):
        store.close()
    revlog.db.close()


def resolved(b):
    return b.store.snapshot()["metrics"]["resolved_cards_total"]


def test_repeated_undo_redo_with_native_counter_aliases(backend):
    b = backend
    counter = b.answer()
    assert resolved(b) == 1
    b.undo(counter)
    b.drain()
    assert resolved(b) == 0
    for _ in range(3):
        native_counter = b.redo()
        b.drain()
        assert resolved(b) == 1
        b.undo(native_counter)
        b.drain()
        assert resolved(b) == 0
    assert b.store._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert len(b.unlocks) == 1
    assert any("onUndo(1)" in script for script in b.scripts)


def test_failed_undo_retries_without_claiming_commit(backend, monkeypatch):
    b = backend
    counter = b.answer()
    original = b.store.undo
    monkeypatch.setattr(b.store, "undo", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    b.undo(counter)
    b.env["_retry_timer"].fire()
    item = b.env["_tracked_answers"][0]
    assert (item["desired_status"], item["persisted_status"]) == ("undone", "active")
    assert resolved(b) == 1
    assert b.env["_retry_timer"].delay == 500
    monkeypatch.setattr(b.store, "undo", original)
    b.drain()
    assert resolved(b) == 0


def test_failed_redo_retries_original_event(backend, monkeypatch):
    b = backend
    counter = b.answer()
    b.undo(counter)
    b.drain()
    original = b.store.redo
    monkeypatch.setattr(b.store, "redo", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    native_counter = b.redo()
    b.env["_retry_timer"].fire()
    assert resolved(b) == 0
    assert b.env["_tracked_answers"][0]["persisted_status"] == "undone"
    monkeypatch.setattr(b.store, "redo", original)
    b.drain()
    assert resolved(b) == 1
    b.undo(native_counter)
    b.drain()
    assert resolved(b) == 0
    assert len(b.unlocks) == 1


@pytest.mark.parametrize("action", ["undo", "redo"])
def test_partial_transaction_failure_rolls_back_and_retries(backend, monkeypatch, action):
    b = backend
    counter = b.answer()
    if action == "redo":
        b.undo(counter)
        b.drain()
    original = b.store._rebuild_card_day
    monkeypatch.setattr(b.store, "_rebuild_card_day", lambda *args: (_ for _ in ()).throw(RuntimeError("after event update")))
    b.undo(counter) if action == "undo" else b.redo()
    b.env["_retry_timer"].fire()
    assert resolved(b) == (1 if action == "undo" else 0)
    row = b.store._db.execute("SELECT active FROM events").fetchone()
    assert row[0] == (1 if action == "undo" else 0)
    monkeypatch.setattr(b.store, "_rebuild_card_day", original)
    b.drain()
    assert resolved(b) == (0 if action == "undo" else 1)


def test_multiple_undo_redo_candidates_follow_lifo(backend):
    b = backend
    first = b.answer(card_id=1, revlog_id=1001)
    second = b.answer(card_id=2, revlog_id=1002)
    b.undo(second, revlog_id=1002)
    b.undo(first, revlog_id=1001)
    b.drain()
    assert resolved(b) == 0
    b.redo(card_id=1, revlog_id=1001)
    b.drain()
    assert resolved(b) == 1
    native_second = b.redo(card_id=2, revlog_id=1002)
    b.drain()
    assert resolved(b) == 2
    b.undo(native_second, revlog_id=1002)
    b.drain()
    assert resolved(b) == 1


def test_disabled_collection_still_restores_gameplay_on_redo(backend, monkeypatch):
    b = backend
    monkeypatch.setitem(b.env, "get_config", lambda: {"collectibles_enabled": False})
    counter = b.answer()
    b.undo(counter)
    native_counter = b.redo()
    b.undo(native_counter)
    assert sum("onUndo(1)" in script for script in b.scripts) == 2
    assert sum("onRedo(1)" in script for script in b.scripts) == 1
    assert resolved(b) == 0
    assert not b.env["_retry_queue"]


@pytest.mark.parametrize("state,session", [("overview", None), ("review", "session-b")])
def test_redo_reconciles_across_sessions_without_old_gameplay(backend, state, session):
    b = backend
    counter = b.answer()
    b.undo(counter)
    b.drain()
    b.env["mw"].state = state
    b.env["_review_session_id"] = session
    b.scripts.clear()
    b.redo()
    b.drain()
    assert resolved(b) == 1
    assert not b.scripts


@pytest.mark.parametrize("missing_identity", [False, True])
def test_undo_cancels_failed_queued_answer(backend, monkeypatch, missing_identity):
    b = backend
    original = b.store.record_answer
    monkeypatch.setattr(b.store, "record_answer", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    counter = b.answer(missing_identity=missing_identity)
    assert len(b.env["_retry_queue"]) == 1
    b.undo(counter)
    monkeypatch.setattr(b.store, "record_answer", original)
    b.drain()
    assert resolved(b) == 0
    assert b.store._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_new_answer_invalidates_redo_candidates(backend):
    b = backend
    counter = b.answer()
    b.undo(counter)
    b.drain()
    b.answer(card_id=2, revlog_id=1002)
    assert not b.env["_pending_redo_counters"]
    assert resolved(b) == 1


def test_500_answers_do_not_rescan_revlog_history(backend):
    b = backend
    for card_id in range(500):
        b.answer(card_id=card_id, revlog_id=1000 + card_id)
    assert b.revlog.queries == 500  # only the original answer's identity lookup
    assert resolved(b) == 500


def test_locked_queue_attempts_one_write_per_callback(backend, monkeypatch):
    b = backend
    calls = []
    original = b.store.record_answer

    def counted(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(b.store, "record_answer", counted)
    blocker = sqlite3.connect(b.store.state_path)
    blocker.execute("BEGIN IMMEDIATE")
    for i in range(10):
        b.answer(card_id=i, revlog_id=1000 + i)
    assert len(b.env["_retry_queue"]) == 10
    calls.clear()
    start = time.monotonic()
    b.env["_retry_timer"].fire()
    elapsed = time.monotonic() - start
    assert len(calls) == 1
    assert len(b.env["_retry_queue"]) == 10
    assert elapsed < 0.30
    blocker.rollback()
    blocker.close()
    b.drain()
    assert resolved(b) == 10


def test_queue_overflow_preserves_intent_and_refills(backend, monkeypatch):
    b = backend
    original = b.store.record_answer
    monkeypatch.setattr(b.store, "record_answer", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    for i in range(101):
        b.answer(card_id=i, revlog_id=1000 + i)
    assert len(b.env["_retry_queue"]) == 100
    assert b.env["_retry_overflow_pending"]
    # Cancel the full front queue before the overflow event obtains a slot.
    for item in b.env["_retry_queue"]:
        item["desired_status"] = "undone"
    monkeypatch.setattr(b.store, "record_answer", original)
    b.drain()
    assert resolved(b) == 1
    assert not b.env["_retry_overflow_pending"]


def test_retry_keeps_original_day_and_timestamp(backend, monkeypatch):
    b = backend
    yesterday = datetime.now().astimezone() - timedelta(days=1)
    monkeypatch.setitem(b.env, "datetime", NS(now=lambda: yesterday))
    original = b.store.record_answer
    monkeypatch.setattr(b.store, "record_answer", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    b.answer()
    monkeypatch.setattr(b.store, "record_answer", original)
    b.drain()
    row = b.store._db.execute("SELECT local_day, answered_at_ms FROM events").fetchone()
    assert row[0] == yesterday.date().isoformat()
    assert row[1] == int(yesterday.timestamp() * 1000)


@pytest.mark.parametrize("state,session", [("overview", None), ("review", "session-b")])
def test_deferred_old_answer_does_not_reactivate_old_session(backend, monkeypatch, state, session):
    b = backend
    b.store.begin_session("session-a")
    original = b.store.record_answer
    monkeypatch.setattr(b.store, "record_answer", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    b.answer()
    b.store.end_session("session-a")
    b.env["mw"].state = state
    b.env["_review_session_id"] = session
    if session:
        b.store.begin_session(session)
    monkeypatch.setattr(b.store, "record_answer", original)
    b.drain()
    assert resolved(b) == 1
    assert b.store._db.execute("SELECT current_session_id FROM profiles WHERE name='User'").fetchone()[0] == session
    assert b.store.snapshot()["metrics"]["session_resolved_current"] == 0
    assert b.store.snapshot()["metrics"]["session_resolved_max"] == 1


def test_retry_backoff_caps_at_two_seconds(backend, monkeypatch):
    b = backend
    monkeypatch.setattr(b.store, "record_answer", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    b.answer()
    timer = b.env["_retry_timer"]
    assert timer.delay == 250
    for expected in (500, 1000, 2000, 2000):
        timer.fire()
        assert timer.delay == expected
    assert len(b.env["_retry_queue"]) == 1


def test_profile_teardown_cancels_worker_and_isolates_history(backend):
    b = backend
    blocker = sqlite3.connect(b.store.state_path)
    blocker.execute("BEGIN IMMEDIATE")
    b.answer()
    timer = b.env["_retry_timer"]
    blocker.rollback()
    blocker.close()
    b.env["on_profile_will_close"]()
    assert timer.deleted and not timer.active
    assert b.env["_retry_timer"] is None
    assert not b.env["_retry_queue"] and not b.env["_answer_undo_counters"]
    b.env["mw"].pm.name = "Other"
    b.env["_collectibles_store"] = CollectiblesStore(b.store.state_path, profile_name="Other")
    b.env["on_profile_did_open"]()
    assert b.env["_review_session_id"] is None
    assert b.env["_collectibles_store"].snapshot()["metrics"]["resolved_cards_total"] == 0
    b.env["_collectibles_store"].close()


def test_store_undo_redo_uses_exact_revlog_before_counter(backend):
    b = backend
    for i in (1, 2):
        b.store.record_answer(card_id=i, rating="good", event_id=i, revlog_id=i, undo_counter=7)
    b.store.undo(7, revlog_id=1)
    assert resolved(b) == 1
    b.store.undo(revlog_id=2)
    assert resolved(b) == 0
    b.store.redo(7, revlog_id=1)
    assert resolved(b) == 1
    b.store.redo(7)
    assert resolved(b) == 2
    b.store.set_profile("Other")
    assert not b.store.undo(revlog_id=1)["changed"]


def test_bridge_opens_real_catalog_id_and_rejects_unknown(backend, monkeypatch):
    b = backend
    aqt, qt, reviewer = ModuleType("aqt"), ModuleType("aqt.qt"), ModuleType("aqt.reviewer")
    reviewer.Reviewer = type("Reviewer", (), {})
    qt.sip = NS(isdeleted=lambda obj: False)
    aqt.qt, aqt.reviewer = qt, reviewer
    for name, module in (("aqt", aqt), ("aqt.qt", qt), ("aqt.reviewer", reviewer)):
        monkeypatch.setitem(sys.modules, name, module)
    scheduled, opened = [], []
    monkeypatch.setitem(b.env, "QTimer", NS(singleShot=lambda ms, callback: scheduled.append(callback)))
    monkeypatch.setitem(b.env, "show_collection_dialog", lambda **kwargs: opened.append(kwargs))
    cat_id = b.store.catalog.entries[0].id
    assert "." in cat_id and "_" in cat_id
    result = b.env["on_js_message"]((False, None), "lockin-open-cat-collection:" + cat_id, reviewer.Reviewer())
    assert result == (True, None)
    assert len(scheduled) == 1
    scheduled.pop()()
    assert opened == [{"selected_cat_id": cat_id}]
    b.env["on_js_message"]((False, None), "lockin-open-cat-collection:unknown", reviewer.Reviewer())
    assert not scheduled


def test_summary_thumbnail_resolves_catalog_and_falls_back(backend, monkeypatch):
    b = backend

    class Label:
        def __init__(self, value):
            self.text = value
            self.pixmap = None

        def setAlignment(self, value):
            pass

        def setAccessibleName(self, value):
            self.name = value

        def setPixmap(self, value):
            self.pixmap = value

    class Pixmap:
        broken = False

        def __init__(self, path):
            self.path = path

        def isNull(self):
            return self.broken

        def scaled(self, width, height, aspect, transformation):
            assert (width, height, aspect, transformation) == (64, 64, "aspect", "smooth")
            return self

    b.env.update(QLabel=Label, QPixmap=Pixmap, ALIGN_CENTER=0,
                 Qt=NS(KeepAspectRatio="aspect", SmoothTransformation="smooth"))
    reward = {"id": b.store.catalog.entries[0].id, "localized_name": "Popcat"}
    path = b.env["_reward_asset_path"](reward)
    assert path == ROOT / "web/cats/cat-01.png"
    assert b.env["_reward_thumbnail"](reward).pixmap.path == str(path)
    Pixmap.broken = True
    assert b.env["_reward_thumbnail"](reward).pixmap is None
    assert b.env["_reward_thumbnail"]({"id": "unknown"}).text == "🐈"
