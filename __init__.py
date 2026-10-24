import json
import logging
import math
import time
import uuid
from datetime import datetime
from pathlib import Path
from aqt import mw, gui_hooks
from aqt.qt import (
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QPixmap,
    QPushButton,
    QShortcut,
    QSpinBox,
    QTabWidget,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
)
from aqt.utils import showInfo

from .collectibles import CollectiblesStore, StateError, load_catalog, show_collectibles_dialog
from .i18n import normalize_language, tr

# Register the web folder for exports
if mw and getattr(mw, "addonManager", None):
    mw.addonManager.setWebExports(__name__, r"web/.*\.(css|js|png)$")

# Global references to keep track of shortcuts and session stats
_fs_shortcut = None
_df_shortcut = None
session_stats = {"cards": 0, "correct": 0, "max_streak": 0, "shields": 0, "study_time": 0}
pomo_state = {
    "state": "study",
    "pre_pause_state": "study",
    "time_left": -1,
    "last_sync_timestamp": 0.0,
}
# Number of answer buttons currently displayed (2, 3, or 4) — used to
# translate numeric ease into a semantic label ("again"/"hard"/"good"/"easy")
# for health mapping in JS.
_current_button_count = 4
_current_rating_map = {1: "again", 2: "hard", 3: "good", 4: "easy"}
_layout_snapshot = None
_review_session_id = None
_answer_undo_counters = {}
_collectibles_store = None
_collection_action = None
_latest_buttons_payload = None
_pending_unlock_notifications = []
_MAX_RETRY_QUEUE_SIZE = 100
_retry_queue = []
_tracked_answers = []
_pending_redo_counters = []
_retry_timer = None
_retry_delay_ms = 250
_retry_overflow_pending = False

_log = logging.getLogger("anki-lockin-focus")

# Safe cross-version alignments
ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter if hasattr(Qt, "AlignmentFlag") else Qt.AlignCenter
ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight if hasattr(Qt, "AlignmentFlag") else Qt.AlignRight

def get_config():
    if not mw or not getattr(mw, "addonManager", None):
        return {}
    return mw.addonManager.getConfig(__name__) or {}


def ui_language(config=None):
    return normalize_language((config or get_config()).get("ui_language", "de"))


def text(key, **values):
    return tr(key, ui_language(), **values)


def _profile_name():
    profile_manager = getattr(mw, "pm", None)
    name = getattr(profile_manager, "name", None)
    if callable(name):
        name = name()
    return str(name or "default")


def get_collectibles_store():
    """Return the local, profile-scoped store without ever blocking reviews."""
    global _collectibles_store
    if _collectibles_store is not None:
        try:
            if _collectibles_store.profile_name != _profile_name():
                _collectibles_store.set_profile(_profile_name())
            return _collectibles_store
        except Exception:
            _log.exception("Unable to update profile on collectibles store")
            return None
    state_path = Path(__file__).resolve().parent / "user_files" / "collectibles.sqlite3"
    try:
        _collectibles_store = CollectiblesStore(
            state_path,
            profile_name=_profile_name(),
        )
    except (OSError, StateError, ValueError, Exception):
        _log.exception("Cat collection state could not be opened")
        return None
    return _collectibles_store


def _addon_asset_prefix():
    try:
        package = mw.addonManager.addonFromModule(__name__)
    except Exception:
        package = "anki-lockin-focus"
    return f"/_addons/{package}"


def collection_snapshot(include_cats=False):
    config = get_config()
    enabled = bool(config.get("collectibles_enabled", True))
    fallback = {
        "locale": ui_language(config),
        "enabled": False,
        "unlocked_count": 0,
        "total_count": 50,
        "unseen_count": 0,
        "equipped": None,
        "next_milestone": None,
        "metrics": {},
    }
    if not enabled:
        return fallback
    store = get_collectibles_store()
    if store is None:
        return fallback
    try:
        snapshot = store.snapshot(ui_language(config), _addon_asset_prefix())
        snapshot["enabled"] = enabled
        if not include_cats:
            snapshot.pop("cats", None)
        return snapshot
    except Exception:
        _log.exception("Unable to build collection snapshot")
        return fallback


def push_collection_snapshot(unlock_ids=None):
    reviewer = getattr(mw, "reviewer", None)
    web = getattr(reviewer, "web", None)
    snapshot = collection_snapshot(include_cats=False)
    script = (
        "if (window.AnkiLockIn && window.AnkiLockIn.updateCollection) {"
        f"window.AnkiLockIn.updateCollection({_json_for_script(snapshot)});"
        "}"
    )
    if unlock_ids:
        store = get_collectibles_store()
        if store is not None:
            try:
                events = store.unlock_events(
                    unlock_ids, ui_language(), _addon_asset_prefix()
                )
                if events:
                    _pending_unlock_notifications.extend(events)
                    script += (
                        "if (window.AnkiLockIn && window.AnkiLockIn.showCatUnlock) {"
                        f"window.AnkiLockIn.showCatUnlock({_json_for_script(events)});"
                        "}"
                    )
            except Exception:
                _log.exception("Unable to format unlock events")
    if not web:
        return
    try:
        web.eval(script)
    except Exception:
        _log.exception("Unable to update the reviewer cat collection")


def _schedule_retry():
    global _retry_timer
    if not _retry_queue or not mw or not getattr(mw, "col", None):
        return
    if _retry_timer is None:
        _retry_timer = QTimer(mw)
        _retry_timer.setSingleShot(True)
        _retry_timer.timeout.connect(_drain_retry_queue)
    if not _retry_timer.isActive():
        _retry_timer.start(_retry_delay_ms)


def _queue_collection_write(item):
    global _retry_overflow_pending
    if not any(queued is item for queued in _retry_queue):
        if len(_retry_queue) >= _MAX_RETRY_QUEUE_SIZE:
            # Retain the identity in history; never silently claim it was saved.
            _log.error("Cat collection retry queue is full; event %s remains pending", item["event_id"])
            _retry_overflow_pending = True
            _schedule_retry()
            return
        _retry_queue.append(item)
    _schedule_retry()


def _refill_retry_queue():
    global _retry_overflow_pending
    if not _retry_overflow_pending:
        return
    queued_ids = {id(item) for item in _retry_queue}
    for item in _tracked_answers:
        if (item["profile_name"] != _profile_name() or id(item) in queued_ids
                or not _needs_collection_write(item)):
            continue
        if len(_retry_queue) >= _MAX_RETRY_QUEUE_SIZE:
            return
        _retry_queue.append(item)
    _retry_overflow_pending = False


def _needs_collection_write(item):
    return item["persisted_status"] != item["desired_status"] and not (
        item["persisted_status"] == "missing" and item["desired_status"] == "undone"
    )


def _persist_collection_answer(item, store):
    """Perform one transaction. A failed write never advances persisted state."""
    desired = item["desired_status"]
    if item["persisted_status"] == "missing":
        result = store.record_answer(
            **{key: item[key] for key in (
                "card_id", "rating", "event_id", "revlog_id", "session_id",
                "undo_counter", "deck_id", "answered_at", "local_day",
            )},
            activate_session=(mw.state == "review" and item["session_id"] == _review_session_id),
        )
    else:
        action = store.undo if desired == "undone" else store.redo
        result = action(undo_counter=item["undo_counter"], revlog_id=item["revlog_id"])
    item["persisted_status"] = desired
    push_collection_snapshot(result.get("new_unlock_ids", []))


def _drain_retry_queue():
    """One deferred write per callback, with bounded backoff under contention."""
    global _retry_delay_ms
    current_profile = _profile_name()
    _refill_retry_queue()
    # Cancellation can discard entries without touching SQLite.
    while _retry_queue:
        item = _retry_queue[0]
        if item["profile_name"] != current_profile or not _needs_collection_write(item):
            _retry_queue.pop(0)
            continue
        break
    _refill_retry_queue()
    if not _retry_queue:
        _retry_delay_ms = 250
        return
    item = _retry_queue[0]
    try:
        # An undo hook may have been missed while the webview was tearing down.
        if item["desired_status"] == "active" and item["revlog_id"] is not None:
            if not mw.col.db.scalar("SELECT 1 FROM revlog WHERE id = ?", item["revlog_id"]):
                item["desired_status"] = "undone"
        if _needs_collection_write(item):
            store = get_collectibles_store()
            if store is None:
                raise StateError("Collection store unavailable")
            _persist_collection_answer(item, store)
        _retry_queue.pop(0)
        _retry_delay_ms = 250
    except Exception:
        _log.exception("Deferred collection write failed; retaining pending state")
        _retry_delay_ms = min(2000, _retry_delay_ms * 2)
    _refill_retry_queue()
    _schedule_retry()


def record_collectible_answer(card, rating, undo_counter):
    # Confirmed new answers invalidate Anki's redo history, including canceled
    # writes that must never be revived by a later queue drain.
    _pending_redo_counters.clear()
    if card is None:
        return
    revlog_id = None
    try:
        revlog_id = mw.col.db.scalar("select max(id) from revlog where cid = ?", card.id)
    except Exception:
        _log.exception("Unable to read the new review log entry")
    operation = None
    if undo_counter is not None:
        operation = _answer_undo_counters.setdefault(undo_counter, {
            "session_id": _review_session_id, "items": [], "status": "active", "counter": undo_counter,
        })
        operation.setdefault("revlog_ids", []).extend([revlog_id] if revlog_id is not None else [])
    if not get_config().get("collectibles_enabled", True):
        return
    if revlog_id is not None:
        event_id = f"revlog:{revlog_id}"
    elif undo_counter is not None:
        event_id = f"undo:{undo_counter}:card:{card.id}"
    else:
        event_id = f"generated:{time.time_ns()}:card:{card.id}"
    answered_at = datetime.now().astimezone()
    item = {
        "card_id": card.id, "rating": rating, "event_id": event_id,
        "revlog_id": revlog_id, "session_id": _review_session_id,
        "undo_counter": undo_counter, "deck_id": getattr(card, "did", None),
        "profile_name": _profile_name(), "answered_at": answered_at,
        "local_day": answered_at.date(),
        "desired_status": "active", "persisted_status": "missing",
    }
    _tracked_answers.append(item)
    if operation is not None:
        operation["items"].append(item)
    if _retry_queue:
        _queue_collection_write(item)
        return
    try:
        store = get_collectibles_store()
        if store is None:
            raise StateError("Collection store unavailable")
        _persist_collection_answer(item, store)
    except Exception:
        _log.exception("Unable to record cat progress; queuing retry")
        _queue_collection_write(item)


def _notify_gameplay(operation, counter, action):
    reviewer = getattr(mw, "reviewer", None)
    if (mw.state == "review" and _review_session_id is not None
            and operation["session_id"] == _review_session_id
            and getattr(reviewer, "web", None)):
        try:
            reviewer.web.eval(
                f"if (window.AnkiLockIn) {{ window.AnkiLockIn.{action}({json.dumps(counter)}); }}"
            )
        except Exception:
            _log.exception("Unable to restore reviewer gameplay")


def _set_answer_state(counter, desired):
    operation = _answer_undo_counters.get(counter)
    if not operation or operation["status"] == desired:
        return
    operation["status"] = desired
    for item in operation["items"]:
        item["desired_status"] = desired
        if not _needs_collection_write(item):
            continue
        # The hook only records intent. Database writes happen on the deferred
        # worker, so multiple events in an operation cannot stack lock waits.
        _queue_collection_write(item)
    _notify_gameplay(operation, operation["counter"], "onUndo" if desired == "undone" else "onRedo")


def _reconcile_undo_redo(target_counter=None):
    """Inspect only the next undone answer; normal operations do no queries."""
    if not _pending_redo_counters or not mw or not getattr(mw, "col", None):
        return
    counter = _pending_redo_counters[-1]
    if target_counter is not None and counter != target_counter:
        return
    operation = _answer_undo_counters.get(counter)
    if not operation:
        return
    try:
        # Anki assigns a new counter to each inverse operation. Revlog presence
        # proves restoration; last_step becomes an alias for the NEXT undo.
        revlogs = operation.get("revlog_ids", [])
        native_status = mw.col.undo_status()
        if not revlogs:
            # No durable identity to prove a redo: retain canceled intent.
            if not getattr(native_status, "redo", True):
                _pending_redo_counters.clear()
            return
        placeholders = ",".join("?" for _ in revlogs)
        restored = mw.col.db.scalar(
            f"SELECT COUNT(*) FROM revlog WHERE id IN ({placeholders})", *revlogs
        )
        if restored != len(revlogs):
            if not getattr(native_status, "redo", True):
                _pending_redo_counters.clear()
            return
        native_counter = native_status.last_step
    except Exception:
        _log.exception("Unable to verify pending answer redo")
        return
    _pending_redo_counters.pop()
    _answer_undo_counters[native_counter] = operation
    _set_answer_state(counter, "active")


def undo_collectible_answer(undo_counter):
    _set_answer_state(undo_counter, "undone")


def show_collection_dialog(_checked=False, *, parent=None, selected_cat_id=None):
    _schedule_retry()
    store = get_collectibles_store()
    if store is None:
        showInfo(
            "Die Katzensammlung konnte nicht geöffnet werden."
            if ui_language() == "de"
            else "The cat collection could not be opened."
        )
        return
    try:
        show_collectibles_dialog(
            store,
            locale=ui_language(),
            parent=parent or mw,
            asset_root=Path(__file__).resolve().parent,
            selected_cat_id=selected_cat_id,
        )
    except Exception:
        _log.exception("Unable to open the cat collection dialog")
        showInfo(
            "Die Katzensammlung konnte nicht geöffnet werden."
            if ui_language() == "de"
            else "The cat collection could not be opened."
        )
        return
    push_collection_snapshot()


def _widget_visible(widget):
    return bool(widget and widget.isVisible())


def capture_review_layout():
    """Remember only the window state this add-on is about to change."""
    global _layout_snapshot
    if _layout_snapshot is not None:
        return
    toolbar_web = getattr(getattr(mw, "toolbar", None), "web", None)
    bottom_web = getattr(mw, "bottomWeb", None)
    _layout_snapshot = {
        "menu": _widget_visible(mw.menuBar()),
        "toolbar": _widget_visible(toolbar_web),
        "bottom": _widget_visible(bottom_web),
        "fullscreen": bool(mw.isFullScreen()),
    }

def set_distraction_free(visible: bool, ignore_config: bool = False):
    """
    Shows or hides standard Anki window chrome to create a distraction-free screen.
    """
    config = get_config()
    if not config:
        return

    distraction_enabled = ignore_config or config.get("no_distraction_enabled", True)

    if visible:
        snapshot = _layout_snapshot
        if snapshot is None:
            return
        mw.menuBar().setVisible(snapshot["menu"])
        toolbar_web = getattr(getattr(mw, "toolbar", None), "web", None)
        if toolbar_web:
            toolbar_web.setVisible(snapshot["toolbar"])
        bottom_web = getattr(mw, "bottomWeb", None)
        if bottom_web:
            bottom_web.setVisible(snapshot["bottom"])
    else:
        if distraction_enabled and (ignore_config or config.get("no_distraction_hide_menu_bar", True)):
            mw.menuBar().setVisible(False)
        if distraction_enabled and (ignore_config or config.get("no_distraction_hide_top_toolbar", True)):
            if hasattr(mw, "toolbar") and mw.toolbar and hasattr(mw.toolbar, "web") and mw.toolbar.web:
                mw.toolbar.web.setVisible(False)
        if hasattr(mw, "bottomWeb") and mw.bottomWeb:
            should_hide = (
                config.get("custom_answer_buttons", True)
                or (distraction_enabled and config.get("no_distraction_hide_bottom_bar", False))
                or config.get("hide_answer_buttons", False)
            )
            if should_hide:
                mw.bottomWeb.setVisible(False)
            else:
                original = _layout_snapshot.get("bottom", True) if _layout_snapshot else True
                mw.bottomWeb.setVisible(original)

def restore_normal_layout_deferred():
    global _layout_snapshot
    from aqt.qt import sip
    if sip.isdeleted(mw) or mw.state == "review":
        return
    set_distraction_free(True)
    if _layout_snapshot:
        if _layout_snapshot["fullscreen"] and not mw.isFullScreen():
            mw.showFullScreen()
        elif not _layout_snapshot["fullscreen"] and mw.isFullScreen():
            mw.showNormal()
    _layout_snapshot = None

def on_state_did_change(new_state, old_state):
    """
    Triggers automatically when the main Anki state changes.
    Enters fullscreen/distraction-free mode when entering the 'review' state,
    and returns to normal layout when leaving it.
    """
    global session_stats, _review_session_id
    config = get_config()
    if not config:
        return

    # When entering review mode
    if new_state == "review":
        pomo_state["last_sync_timestamp"] = time.time()
        # Reset session stats for new session
        session_stats = {"cards": 0, "correct": 0, "max_streak": 0, "shields": 0, "study_time": 0}
        _review_session_id = uuid.uuid4().hex
        if config.get("collectibles_enabled", True):
            store = get_collectibles_store()
            if store is not None:
                try:
                    store.begin_session(_review_session_id)
                except Exception:
                    _log.exception("Unable to start the cat collection session")
        capture_review_layout()
        # Custom buttons must replace the native row even when the rest of the
        # distraction-free layout is disabled.
        set_distraction_free(False)
        if config.get("no_distraction_enabled", True):
            if config.get("no_distraction_auto_fullscreen", True):
                mw.showFullScreen()
    
    # When leaving review mode or entering deck browser / overview
    if new_state in ("deckBrowser", "overview") or old_state == "review":
        if old_state == "review":
            pomo_state["last_sync_timestamp"] = 0.0
            if mw.reviewer and mw.reviewer.web:
                try:
                    mw.reviewer.web.eval(
                        "if (window.AnkiLockIn && window.AnkiLockIn.flushStats) { window.AnkiLockIn.flushStats(); }"
                    )
                except Exception:
                    _log.exception("Unable to flush final session statistics")
        # Restore the exact pre-review window state after Anki finishes its own transition.
        QTimer.singleShot(150, restore_normal_layout_deferred)
        
        # Show session stats dialog if we are transitioning out of review and cards were studied or undelivered rewards exist
        if old_state == "review":
            closing_stats = session_stats
            has_cards = closing_stats.get("cards", 0) > 0
            has_pending_rewards = bool(_pending_unlock_notifications)
            if has_cards or has_pending_rewards:
                pending_to_show = list(_pending_unlock_notifications)
                _pending_unlock_notifications.clear()
                QTimer.singleShot(
                    400,
                    lambda rewards=pending_to_show, stats=closing_stats: show_session_stats_dialog(
                        pending_rewards=rewards, stats=stats
                    ),
                )
            store = get_collectibles_store()
            if store is not None:
                try:
                    store.end_session(_review_session_id)
                except Exception:
                    _log.exception("Unable to close the cat collection session")
            _review_session_id = None

def toggle_fullscreen():
    """
    Manual hotkey action to toggle fullscreen.
    """
    if mw.isFullScreen():
        mw.showNormal()
    else:
        mw.showFullScreen()

def toggle_distraction_free():
    """
    Manual hotkey action to toggle toolbars and menu bar visibility.
    """
    if mw.state != "review":
        return
    capture_review_layout()
    menu_bar_visible = mw.menuBar().isVisible()
    set_distraction_free(not menu_bar_visible, ignore_config=True)

def setup_shortcuts():
    """
    Binds the keyboard shortcuts globally.
    """
    global _fs_shortcut, _df_shortcut
    
    if _fs_shortcut:
        _fs_shortcut.setEnabled(False)
        _fs_shortcut.setParent(None)
    if _df_shortcut:
        _df_shortcut.setEnabled(False)
        _df_shortcut.setParent(None)

    _fs_shortcut = QShortcut(QKeySequence("Shift+F11"), mw)
    _fs_shortcut.activated.connect(toggle_fullscreen)

    # Ctrl+Shift+D is Anki's Set Due shortcut. Use a collision-resistant chord.
    _df_shortcut = QShortcut(QKeySequence("Ctrl+Alt+Shift+D"), mw)
    _df_shortcut.activated.connect(toggle_distraction_free)

# Register hooks
gui_hooks.state_did_change.append(on_state_did_change)


def _json_for_script(value):
    """Serialize data for an inline script without allowing a closing tag."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _advance_pomodoro(state, time_left, elapsed, study_duration, break_duration):
    """Advance through any number of elapsed focus/break phases."""
    if state not in ("study", "break") or time_left <= 0 or elapsed <= 0:
        return state, time_left

    if elapsed < time_left:
        return state, time_left - elapsed

    elapsed -= time_left
    state = "break" if state == "study" else "study"
    time_left = break_duration if state == "break" else study_duration

    cycle_duration = study_duration + break_duration
    if cycle_duration > 0 and elapsed >= cycle_duration:
        elapsed %= cycle_duration

    while elapsed >= time_left and time_left > 0:
        elapsed -= time_left
        state = "break" if state == "study" else "study"
        time_left = break_duration if state == "break" else study_duration
    return state, max(0.0, time_left - elapsed)


# Hook into Web Content Loading to inject JS and CSS
def on_webview_will_set_content(web_content, context):
    from aqt.qt import sip
    try:
        if sip.isdeleted(context):
            return
    except Exception:
        pass

    import aqt.reviewer
    allowed_contexts = [aqt.reviewer.Reviewer]
    if hasattr(aqt.reviewer, "ReviewerBottomBar"):
        allowed_contexts.append(aqt.reviewer.ReviewerBottomBar)
        
    if not isinstance(context, tuple(allowed_contexts)):
        return

    addon_package = mw.addonManager.addonFromModule(__name__)
    config = get_config()
    
    # Calculate real-time elapsed since last sync if not paused
    current_time = time.time()
    state = pomo_state.get("state", "study")
    time_left = pomo_state.get("time_left", -1)
    last_sync = pomo_state.get("last_sync_timestamp", 0.0)
    
    if getattr(mw, "state", None) == "review" and time_left > 0 and last_sync > 0.0 and state != "paused":
        elapsed = current_time - last_sync
        if elapsed > 0:
            study_duration = config.get("pomodoro_study_duration", 25) * 60
            break_duration = config.get("pomodoro_break_duration", 5) * 60
            state, time_left = _advance_pomodoro(
                state, time_left, elapsed, study_duration, break_duration
            )
            pomo_state["state"] = state
            pomo_state["time_left"] = time_left
            pomo_state["last_sync_timestamp"] = current_time
    elif getattr(mw, "state", None) != "review":
        pomo_state["last_sync_timestamp"] = 0.0

    # Determine the webview kind from Python context
    if hasattr(aqt.reviewer, "ReviewerBottomBar") and isinstance(context, aqt.reviewer.ReviewerBottomBar):
        webview_kind = "bottom"
    else:
        webview_kind = "main"

    # Inject current configuration and persisted Pomodoro state into global JS variables
    config_json = _json_for_script(config)
    pomo_json = _json_for_script(pomo_state)
    collection_json = _json_for_script(
        collection_snapshot(include_cats=False) if webview_kind == "main" else None
    )
    web_content.head += (
        "<script>"
        f"window.AnkiLockInConfig = {config_json};"
        f"window.AnkiLockInPomoState = {pomo_json};"
        f"window.AnkiLockInCollectionSnapshot = {collection_json};"
        f"window.AnkiLockInWebviewKind = '{webview_kind}';"
        "</script>"
    )

    # Append custom stylesheet and script to the reviewer content.
    # Version query strings bust Anki's web cache after addon updates.
    web_content.css.append(f"/_addons/{addon_package}/web/lockin.css?v=5")
    web_content.js.append(f"/_addons/{addon_package}/web/lockin.js?v=5")

gui_hooks.webview_will_set_content.append(on_webview_will_set_content)

def apply_distraction_free_if_reviewing():
    if mw.state == "review":
        config = get_config()
        if config:
            capture_review_layout()
            set_distraction_free(False)
            if config.get("no_distraction_enabled", True) and config.get("no_distraction_auto_fullscreen", True):
                if not mw.isFullScreen():
                    mw.showFullScreen()

# Hook into Reviewer Lifecycle events to sync state to JavaScript
def on_reviewer_did_show_question(card):
    if mw.reviewer and mw.reviewer.web:
        # Track that we're in question mode so JS can restore state after webview re-renders
        mw.reviewer.web.eval("window.AnkiLockInAnswerMode = false; if (window.AnkiLockIn) { window.AnkiLockIn.onShowQuestion(); }")
    # Enforce distraction-free window state deferred by 100ms to override any Anki layout resets (e.g. during deck switching)
    QTimer.singleShot(100, apply_distraction_free_if_reviewing)

def on_reviewer_did_show_answer(card):
    if not (mw.reviewer and mw.reviewer.web):
        return

    # Track that we're in answer mode so JS can restore state after webview re-renders.
    # Note: Anki's _showAnswer() updates #qa innerHTML; it does NOT call setHtml(),
    # so window.AnkiLockIn and its DOM persist across card transitions. No
    # retry-with-delay injections are needed.
    mw.reviewer.web.eval(
        "window.AnkiLockInAnswerMode = true; "
        "if (window.AnkiLockIn) { window.AnkiLockIn.onShowAnswer(); }"
    )


def _semantic_ratings(buttons):
    """Map Anki's actual 2/3/4-button layout to stable rating names."""
    layouts = {
        2: ("again", "good"),
        3: ("again", "good", "easy"),
        4: ("again", "hard", "good", "easy"),
    }
    names = layouts.get(len(buttons), ())
    return {
        ease: names[index] if index < len(names) else ("again" if ease == 1 else "good")
        for index, (ease, _label) in enumerate(buttons)
    }


def on_reviewer_will_init_answer_buttons(buttons, reviewer, card):
    """Mirror Anki 26.08's supported answer buttons without private APIs."""
    global _current_button_count, _current_rating_map, _latest_buttons_payload
    _current_button_count = len(buttons)
    _current_rating_map = _semantic_ratings(buttons)

    intervals = []
    try:
        if reviewer.mw.col.conf.get("estTimes", True):
            intervals = reviewer.mw.col.sched.describe_next_states(
                reviewer.get_scheduling_states()
            )
    except Exception:
        _log.exception("Unable to calculate answer interval labels")

    payload = {}
    for ease, label in buttons:
        interval = intervals[ease - 1] if 0 < ease <= len(intervals) else ""
        payload[str(ease)] = {
            "label": str(label),
            "interval": str(interval),
            "rating": _current_rating_map[ease],
            "enabled": True,
        }

    _latest_buttons_payload = payload
    if not get_config().get("custom_answer_buttons", True):
        return buttons

    try:
        reviewer.web.eval(
            "if (window.AnkiLockIn) { window.AnkiLockIn.updateButtons(%s); }"
            % json.dumps(payload, ensure_ascii=False)
        )
    except Exception:
        _log.exception("Unable to update custom answer buttons")
    return buttons

def on_reviewer_did_answer_card(reviewer, card, ease):
    counter = None
    try:
        counter = mw.col.undo_status().last_step
    except Exception:
        _log.exception("Unable to read Anki undo counter")
    if counter is not None:
        _answer_undo_counters[counter] = {"session_id": _review_session_id, "items": [], "status": "active", "counter": counter}

    label = _current_rating_map.get(ease, "again" if ease == 1 else "good")
    if mw.reviewer and mw.reviewer.web:
        mw.reviewer.web.eval(
            "if (window.AnkiLockIn) { window.AnkiLockIn.onAnswerCard(%s, %s, %s); }"
            % (ease, json.dumps(label), json.dumps(counter))
        )
    record_collectible_answer(card, label, counter)

# Fires for BOTH mouse clicks (pycmd("easeN") -> _linkHandler -> _answerCard)
# AND keyboard shortcuts (Qt QShortcut -> _answerCard). Send the animation
# command to JS BEFORE the card is answered so the "picked" highlight lands
# in time for the card transition.
if hasattr(gui_hooks, "reviewer_will_answer_card"):
    def on_will_answer_card(proceed_ease, reviewer, card):
        try:
            proceed, ease = proceed_ease
            if proceed and ease and reviewer and reviewer.web:
                reviewer.web.eval(
                    f"if (window.AnkiLockIn && window.AnkiLockIn.animateButton) "
                    f"{{ window.AnkiLockIn.animateButton({{ease: {ease}}}); }}"
                )
        except Exception:
            pass
        return proceed_ease
    gui_hooks.reviewer_will_answer_card.append(on_will_answer_card)

gui_hooks.reviewer_did_show_question.append(on_reviewer_did_show_question)
gui_hooks.reviewer_did_show_answer.append(on_reviewer_did_show_answer)
gui_hooks.reviewer_did_answer_card.append(on_reviewer_did_answer_card)
if hasattr(gui_hooks, "reviewer_will_init_answer_buttons"):
    gui_hooks.reviewer_will_init_answer_buttons.append(on_reviewer_will_init_answer_buttons)

# Hook into undo event to revert health/stats gains
if hasattr(gui_hooks, "state_did_undo"):
    def on_state_did_undo(changes):
        counter = getattr(changes, "counter", None)
        if counter not in _answer_undo_counters:
            return
        operation = _answer_undo_counters[counter]
        if operation["status"] != "active":
            return
        _pending_redo_counters.append(counter)
        undo_collectible_answer(counter)
    gui_hooks.state_did_undo.append(on_state_did_undo)

if hasattr(gui_hooks, "operation_did_execute"):
    def on_operation_did_execute(changes, handler=None):
        _reconcile_undo_redo()
    gui_hooks.operation_did_execute.append(on_operation_did_execute)

# Bridge communication from JavaScript back to Python
_MAX_BRIDGE_MESSAGE_BYTES = 4096


def _bridge_payload(message, prefix):
    if not isinstance(message, str) or not message.startswith(prefix):
        return None
    if len(message.encode("utf-8", errors="ignore")) > _MAX_BRIDGE_MESSAGE_BYTES:
        return None
    try:
        data = json.loads(message[len(prefix):])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _bounded_number(value, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        return None
    return int(number) if integer else number


def on_js_message(handled, message, context):
    global session_stats
    from aqt.qt import sip
    try:
        if sip.isdeleted(context):
            return handled
    except Exception:
        pass

    import aqt.reviewer
    allowed_contexts = [aqt.reviewer.Reviewer]
    if hasattr(aqt.reviewer, "ReviewerBottomBar"):
        allowed_contexts.append(aqt.reviewer.ReviewerBottomBar)
        
    if not isinstance(context, tuple(allowed_contexts)):
        return handled

    if not isinstance(message, str):
        return handled

    if message == "anki-lockin-exit":
        # Exit review mode and return to deck browser (Stapelansicht) deferred to avoid unloading conflicts
        QTimer.singleShot(100, lambda: mw.moveToState("deckBrowser"))
        return (True, None)

    if message == "lockin-open-cat-collection":
        QTimer.singleShot(0, show_collection_dialog)
        return (True, None)

    if message.startswith("lockin-open-cat-collection:"):
        cat_id = message[len("lockin-open-cat-collection:"):].strip()
        store = get_collectibles_store()
        if len(message.encode("utf-8")) <= _MAX_BRIDGE_MESSAGE_BYTES and store is not None and cat_id in store.catalog.by_id:
            QTimer.singleShot(0, lambda cid=cat_id: show_collection_dialog(selected_cat_id=cid))
        return (True, None)

    if message.startswith("lockin-unlock-delivered:"):
        data = _bridge_payload(message, "lockin-unlock-delivered:")
        if data is not None and isinstance(data.get("cat_ids"), list):
            delivered_ids = {cid for cid in data["cat_ids"] if isinstance(cid, str)}
            _pending_unlock_notifications[:] = [
                e for e in _pending_unlock_notifications if e.get("id") not in delivered_ids
            ]
        return (True, None)

    # NOTE: The following three handlers were removed because they
    # duplicate state we now push from Python proactively:
    #   - "lockin-request-intervals"   -> on_reviewer_did_show_answer pushes updateButtons
    #   - "anki-lockin-answer:"        -> buttons now call Anki's native pycmd("easeN")
    #   - "lockin-animate:"            -> reviewer_will_answer_card hook fires animateButton

    if message.startswith("anki-lockin-stats:"):
        data = _bridge_payload(message, "anki-lockin-stats:")
        if data is not None:
            parsed = {
                "cards": _bounded_number(data.get("cards"), 0, 10_000_000, True),
                "correct": _bounded_number(data.get("correct"), 0, 10_000_000, True),
                "max_streak": _bounded_number(data.get("max_streak"), 0, 10_000_000, True),
                "shields": _bounded_number(data.get("shields"), 0, 10_000_000, True),
                "study_time": _bounded_number(data.get("study_time"), 0, 604_800),
            }
            if all(value is not None for value in parsed.values()) and parsed["correct"] <= parsed["cards"]:
                if isinstance(globals().get("session_stats"), dict):
                    session_stats.update(parsed)
                else:
                    session_stats = parsed
        return (True, None)

    if message.startswith("lockin-pomo-sync:"):
        data = _bridge_payload(message, "lockin-pomo-sync:")
        if data is not None:
            state = data.get("state")
            pre_pause_state = data.get("pre_pause_state", pomo_state.get("pre_pause_state", "study"))
            time_left = _bounded_number(data.get("time_left"), 0, 86_400)
            if state in ("study", "break", "paused") and pre_pause_state in ("study", "break") and time_left is not None:
                pomo_state["state"] = state
                pomo_state["pre_pause_state"] = pre_pause_state
                pomo_state["time_left"] = time_left
                pomo_state["last_sync_timestamp"] = time.time()
        return (True, None)

    # Old debug messages are consumed but never written; card HTML and study
    # content must not leak to temporary files.
    if message.startswith("lockin-debug:"):
        return (True, None)

    return handled

gui_hooks.webview_did_receive_js_message.append(on_js_message)


# ----------------------------------------------------
# Session Stats Dialog
# ----------------------------------------------------

def _reward_asset_path(reward):
    # IDs are stable collection keys, not image filenames.
    catalog = _collectibles_store.catalog if _collectibles_store is not None else load_catalog()
    entry = catalog.by_id.get(reward.get("id"))
    return Path(__file__).resolve().parent / entry.asset if entry else None


def _reward_thumbnail(reward):
    thumb = QLabel("🐈")
    thumb.setAlignment(ALIGN_CENTER)
    thumb.setAccessibleName(reward.get("localized_name", ""))
    img_path = _reward_asset_path(reward)
    if img_path and img_path.is_file():
        pix = QPixmap(str(img_path))
        if not pix.isNull():
            thumb.setPixmap(pix.scaled(
                64, 64,
                Qt.AspectRatioMode.KeepAspectRatio if hasattr(Qt, "AspectRatioMode") else Qt.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation if hasattr(Qt, "TransformationMode") else Qt.SmoothTransformation,
            ))
    return thumb


class SessionStatsDialog(QDialog):
    def __init__(self, stats, pending_rewards=None, parent=None):
        super().__init__(parent or mw)
        self.language = ui_language()
        self.setWindowTitle(tr("session_title", self.language))
        self.setMinimumWidth(380)
        self.stats = stats
        self.pending_rewards = pending_rewards or []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel(tr("session_complete", self.language))
        title.setAlignment(ALIGN_CENTER)
        title.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px; color: #1e90ff;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(ALIGN_RIGHT)
        
        cards = self.stats.get("cards", 0)
        correct = self.stats.get("correct", 0)
        accuracy = (correct / cards * 100) if cards > 0 else 0
        
        form.addRow(tr("cards_studied", self.language), QLabel(str(cards)))
        form.addRow(tr("accuracy", self.language), QLabel(f"{accuracy:.1f}%"))
        form.addRow(tr("max_focus_streak", self.language), QLabel(str(self.stats.get("max_streak", 0))))
        form.addRow(tr("shields", self.language), QLabel(str(self.stats.get("shields", 0))))
        
        study_time = self.stats.get("study_time", 0)
        mins = int(study_time // 60)
        secs = int(study_time % 60)
        form.addRow(tr("focus_time", self.language), QLabel(f"{mins:02d}:{secs:02d}"))
        
        layout.addLayout(form)

        if self.pending_rewards:
            rewards_box = QGroupBox(tr("new_rewards", self.language))
            rewards_layout = QVBoxLayout(rewards_box)
            for reward in self.pending_rewards:
                row = QHBoxLayout()
                row.addWidget(_reward_thumbnail(reward))
                info = QVBoxLayout()
                name_lbl = QLabel(reward.get("localized_name", ""))
                name_lbl.setStyleSheet("font-weight: bold; color: #1e90ff;")
                reason_lbl = QLabel(reward.get("localized_reason", ""))
                reason_lbl.setWordWrap(True)
                info.addWidget(name_lbl)
                info.addWidget(reason_lbl)
                row.addLayout(info)
                rewards_layout.addLayout(row)
            layout.addWidget(rewards_box)

        quote = QLabel(tr("session_quote", self.language))
        quote.setWordWrap(True)
        quote.setAlignment(ALIGN_CENTER)
        quote.setStyleSheet("font-style: italic; color: #777; margin-top: 10px; margin-bottom: 10px;")
        layout.addWidget(quote)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def show_session_stats_dialog(pending_rewards=None, stats=None):
    dialog = SessionStatsDialog(stats if stats is not None else session_stats, pending_rewards=pending_rewards)
    dialog.exec()


# ----------------------------------------------------
# Settings Dialog Implementation (PyQt)
# ----------------------------------------------------

class LockInSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or mw)
        self.language = ui_language()
        self.setWindowTitle(tr("settings_title", self.language))
        self.setMinimumWidth(550)
        self.config = get_config()
        self.init_ui()

    def init_ui(self):
        def t(key):
            return tr(key, self.language)

        main_layout = QVBoxLayout(self)

        # Tab Widget to organize groups of configurations
        self.tabs = QTabWidget()

        # --- Tab 0: Presets Mode ---
        self.tab_preset = QWidget()
        layout_preset = QFormLayout(self.tab_preset)
        
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(t("preset_relaxed"), "relaxed")
        self.preset_combo.addItem(t("preset_balanced"), "balanced")
        self.preset_combo.addItem(t("preset_hardcore"), "hardcore")
        
        # Select current preset in combobox
        current_preset = self.config.get("preset_mode", "balanced")
        index = self.preset_combo.findData(current_preset)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
            
        self.preset_combo.currentIndexChanged.connect(self.on_preset_changed)
        layout_preset.addRow(t("select_preset"), self.preset_combo)

        self.reapply_preset_btn = QPushButton(t("reapply_preset"))
        self.reapply_preset_btn.clicked.connect(
            lambda _checked=False: self.on_preset_changed(self.preset_combo.currentIndex())
        )
        layout_preset.addRow("", self.reapply_preset_btn)

        self.preset_status_label = QLabel("")
        self.preset_status_label.setWordWrap(True)
        self.preset_status_label.setStyleSheet("color: #1e90ff; margin-top: 8px; font-weight: bold;")
        layout_preset.addRow("", self.preset_status_label)
        
        desc = QLabel(t("preset_help"))
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #777; margin-top: 15px; font-style: italic;")
        layout_preset.addRow("", desc)
        
        self.tabs.addTab(self.tab_preset, t("profile_presets"))

        # --- Tab 1: Health Bar Settings ---
        self.tab_health = QWidget()
        layout_health = QFormLayout(self.tab_health)
        
        self.health_max = QSpinBox()
        self.health_max.setRange(50, 500)
        self.health_max.setValue(self.config.get("health_max", 100))
        layout_health.addRow(t("maximum_health"), self.health_max)

        self.health_drain = QDoubleSpinBox()
        self.health_drain.setRange(0.1, 15.0)
        self.health_drain.setSingleStep(0.1)
        self.health_drain.setValue(self.config.get("health_drain_rate", 2.0))
        layout_health.addRow(t("drain_rate"), self.health_drain)

        self.heal_easy = QSpinBox()
        self.heal_easy.setRange(0, 100)
        self.heal_easy.setValue(self.config.get("health_heal_easy", 15))
        layout_health.addRow(t("heal_easy"), self.heal_easy)

        self.heal_good = QSpinBox()
        self.heal_good.setRange(0, 100)
        self.heal_good.setValue(self.config.get("health_heal_good", 10))
        layout_health.addRow(t("heal_good"), self.heal_good)

        self.heal_hard = QSpinBox()
        self.heal_hard.setRange(0, 100)
        self.heal_hard.setValue(self.config.get("health_heal_hard", 5))
        layout_health.addRow(t("heal_hard"), self.heal_hard)

        self.heal_again = QSpinBox()
        self.heal_again.setRange(-100, 100)
        self.heal_again.setValue(self.config.get("health_heal_again", -10))
        layout_health.addRow(t("heal_again"), self.heal_again)

        self.tabs.addTab(self.tab_health, t("life_drain"))

        # --- Tab 2: Speed Focus (Silent) ---
        self.tab_speed = QWidget()
        layout_speed = QFormLayout(self.tab_speed)

        self.alert_delay = QSpinBox()
        self.alert_delay.setRange(0, 120)
        self.alert_delay.setValue(self.config.get("speed_focus_alert_delay", 8))
        layout_speed.addRow(t("warning_delay"), self.alert_delay)

        self.reveal_delay = QSpinBox()
        self.reveal_delay.setRange(0, 120)
        self.reveal_delay.setValue(self.config.get("speed_focus_reveal_delay", 0))
        layout_speed.addRow(t("reveal_delay"), self.reveal_delay)

        safe_grading_note = QLabel(t("auto_grade_removed"))
        safe_grading_note.setWordWrap(True)
        safe_grading_note.setStyleSheet("color: #777; font-style: italic;")
        layout_speed.addRow("", safe_grading_note)

        self.speed_volume = QDoubleSpinBox()
        self.speed_volume.setRange(0.0, 1.0)
        self.speed_volume.setSingleStep(0.1)
        self.speed_volume.setValue(self.config.get("speed_focus_volume", 0.5))
        layout_speed.addRow(t("volume"), self.speed_volume)

        self.tabs.addTab(self.tab_speed, t("speed_focus"))

        # --- Tab 3: Pomodoro Timer ---
        self.tab_pomo = QWidget()
        layout_pomo = QFormLayout(self.tab_pomo)

        self.study_dur = QSpinBox()
        self.study_dur.setRange(1, 180)
        self.study_dur.setValue(self.config.get("pomodoro_study_duration", 25))
        layout_pomo.addRow(t("study_duration"), self.study_dur)

        self.break_dur = QSpinBox()
        self.break_dur.setRange(1, 60)
        self.break_dur.setValue(self.config.get("pomodoro_break_duration", 5))
        layout_pomo.addRow(t("break_duration"), self.break_dur)

        self.pomo_sound = QCheckBox()
        self.pomo_sound.setChecked(self.config.get("pomodoro_enable_sound", True))
        layout_pomo.addRow(t("pomo_chimes"), self.pomo_sound)

        self.pomo_visible = QCheckBox()
        self.pomo_visible.setChecked(self.config.get("pomodoro_visible", True))
        layout_pomo.addRow(t("pomo_visible"), self.pomo_visible)

        self.breath_pattern = QComboBox()
        for key, value in (
            ("breath_box", "box"),
            ("breath_478", "478"),
            ("breath_coherent", "coherent"),
            ("breath_sigh", "sigh"),
            ("breath_simple", "simple"),
        ):
            self.breath_pattern.addItem(t(key), value)
        current_pattern = self.config.get("breathing_pattern", "box")
        pattern_index = self.breath_pattern.findData(current_pattern)
        self.breath_pattern.setCurrentIndex(max(0, pattern_index))
        layout_pomo.addRow(t("breathing"), self.breath_pattern)

        self.tabs.addTab(self.tab_pomo, t("pomodoro"))

        # --- Tab 4: No Distractions Full Screen ---
        self.tab_dist = QWidget()
        layout_dist = QFormLayout(self.tab_dist)

        self.dist_enabled = QCheckBox()
        self.dist_enabled.setChecked(self.config.get("no_distraction_enabled", True))
        layout_dist.addRow(t("dist_enabled"), self.dist_enabled)

        self.auto_fs = QCheckBox()
        self.auto_fs.setChecked(self.config.get("no_distraction_auto_fullscreen", True))
        layout_dist.addRow(t("auto_fullscreen"), self.auto_fs)

        self.hide_menu = QCheckBox()
        self.hide_menu.setChecked(self.config.get("no_distraction_hide_menu_bar", True))
        layout_dist.addRow(t("hide_menu"), self.hide_menu)

        self.hide_toolbar = QCheckBox()
        self.hide_toolbar.setChecked(self.config.get("no_distraction_hide_top_toolbar", True))
        layout_dist.addRow(t("hide_toolbar"), self.hide_toolbar)

        self.hide_bottom = QCheckBox()
        self.hide_bottom.setChecked(self.config.get("no_distraction_hide_bottom_bar", False))
        layout_dist.addRow(t("hide_bottom"), self.hide_bottom)

        self.hide_buttons = QCheckBox()
        self.hide_buttons.setChecked(self.config.get("hide_answer_buttons", False))
        layout_dist.addRow(t("hide_buttons"), self.hide_buttons)

        self.custom_buttons = QCheckBox()
        self.custom_buttons.setChecked(self.config.get("custom_answer_buttons", True))
        layout_dist.addRow(t("custom_buttons"), self.custom_buttons)

        self.bar_pos = QComboBox()
        self.bar_pos.addItem(t("top"), "top")
        self.bar_pos.addItem(t("bottom"), "bottom")
        current_pos = self.config.get("bar_position", "bottom")
        index_pos = self.bar_pos.findData(current_pos)
        if index_pos >= 0:
            self.bar_pos.setCurrentIndex(index_pos)
        layout_dist.addRow(t("bar_position"), self.bar_pos)

        self.tabs.addTab(self.tab_dist, t("fullscreen"))

        # --- Tab 5: ADHD Boosters ---
        self.tab_adhd = QWidget()
        layout_adhd = QFormLayout(self.tab_adhd)

        self.streak_enabled = QCheckBox()
        self.streak_enabled.setChecked(self.config.get("adhd_boosters_streak_enabled", True))
        layout_adhd.addRow(t("streak_enabled"), self.streak_enabled)

        self.shield_enabled = QCheckBox()
        self.shield_enabled.setChecked(self.config.get("adhd_boosters_shield_enabled", True))
        layout_adhd.addRow(t("shield_enabled"), self.shield_enabled)

        self.autopause_enabled = QCheckBox()
        self.autopause_enabled.setChecked(self.config.get("adhd_boosters_autopause_enabled", True))
        layout_adhd.addRow(t("autopause"), self.autopause_enabled)

        self.heartbeat_enabled = QCheckBox()
        self.heartbeat_enabled.setChecked(self.config.get("adhd_boosters_low_health_heartbeat", True))
        layout_adhd.addRow(t("heartbeat"), self.heartbeat_enabled)

        self.grace_period = QSpinBox()
        self.grace_period.setRange(0, 600)
        self.grace_period.setValue(self.config.get("grace_period_duration", 180))
        layout_adhd.addRow(t("grace"), self.grace_period)

        self.sigmoid_enabled = QCheckBox()
        self.sigmoid_enabled.setChecked(self.config.get("sigmoid_drain_enabled", True))
        layout_adhd.addRow(t("sigmoid"), self.sigmoid_enabled)

        self.streak_reset_time = QSpinBox()
        self.streak_reset_time.setRange(5, 300)
        self.streak_reset_time.setValue(self.config.get("streak_reset_idle_time", 30))
        layout_adhd.addRow(t("idle_reset"), self.streak_reset_time)

        self.streak_speed = QDoubleSpinBox()
        self.streak_speed.setRange(1.0, 30.0)
        self.streak_speed.setSingleStep(0.5)
        self.streak_speed.setValue(self.config.get("streak_speed_threshold", 5.0))
        layout_adhd.addRow(t("speed_threshold"), self.streak_speed)

        self.tabs.addTab(self.tab_adhd, t("adhd_lockin"))

        # --- Tab 6: Cat collection and language ---
        self.tab_collectibles = QWidget()
        layout_collectibles = QFormLayout(self.tab_collectibles)

        self.collectibles_enabled = QCheckBox()
        self.collectibles_enabled.setChecked(self.config.get("collectibles_enabled", True))
        layout_collectibles.addRow(t("collectibles_enabled"), self.collectibles_enabled)

        self.language_combo = QComboBox()
        self.language_combo.addItem("Deutsch", "de")
        self.language_combo.addItem("English", "en")
        language_index = self.language_combo.findData(self.language)
        self.language_combo.setCurrentIndex(max(0, language_index))
        layout_collectibles.addRow(t("language"), self.language_combo)

        collection_help = QLabel(t("collection_help"))
        collection_help.setWordWrap(True)
        collection_help.setStyleSheet("color: #777; margin-top: 8px;")
        layout_collectibles.addRow("", collection_help)

        open_collection_button = QPushButton(t("open_collection"))
        open_collection_button.clicked.connect(
            lambda _checked=False: show_collection_dialog(parent=self)
        )
        layout_collectibles.addRow("", open_collection_button)

        self.tabs.addTab(self.tab_collectibles, t("collectibles"))

        main_layout.addWidget(self.tabs)

        # Standard dialog save/cancel button box
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.save_settings)
        self.buttons.rejected.connect(self.reject)
        main_layout.addWidget(self.buttons)

        for spinbox in (
            self.health_max,
            self.health_drain,
            self.heal_easy,
            self.heal_good,
            self.heal_hard,
            self.heal_again,
            self.alert_delay,
            self.reveal_delay,
            self.grace_period,
            self.streak_speed,
        ):
            spinbox.valueChanged.connect(self.update_preset_status)
        self.sigmoid_enabled.toggled.connect(self.update_preset_status)
        self.update_preset_status()

    _PRESET_TARGETS = {
        "relaxed": (100, 1.0, 20, 15, 10, -5, 15, 30, 300, False, 8.0),
        "balanced": (100, 2.0, 15, 10, 5, -10, 8, 0, 180, True, 5.0),
        "hardcore": (100, 4.0, 10, 5, 2, -20, 5, 10, 60, True, 3.0),
    }

    def _current_preset_tuple(self):
        return (
            self.health_max.value(),
            round(self.health_drain.value(), 2),
            self.heal_easy.value(),
            self.heal_good.value(),
            self.heal_hard.value(),
            self.heal_again.value(),
            self.alert_delay.value(),
            self.reveal_delay.value(),
            self.grace_period.value(),
            self.sigmoid_enabled.isChecked(),
            round(self.streak_speed.value(), 2),
        )

    def update_preset_status(self, *_args):
        preset = self.preset_combo.currentData()
        expected = self._PRESET_TARGETS.get(preset)
        matched = expected is not None and self._current_preset_tuple() == expected
        key = "preset_status_matched" if matched else "preset_status_custom"
        self.preset_status_label.setText(tr(key, self.language))
        self.preset_status_label.setStyleSheet(
            "color: #1e90ff; margin-top: 8px; font-weight: bold;"
            if matched
            else "color: #f59e0b; margin-top: 8px; font-weight: bold;"
        )

    def on_preset_changed(self, _index):
        """
        Dynamically adjusts configuration input fields based on the selected profile preset.
        """
        # Block signals to prevent infinite recursion loop
        self.health_max.blockSignals(True)
        self.health_drain.blockSignals(True)
        self.heal_easy.blockSignals(True)
        self.heal_good.blockSignals(True)
        self.heal_hard.blockSignals(True)
        self.heal_again.blockSignals(True)
        self.alert_delay.blockSignals(True)
        self.reveal_delay.blockSignals(True)
        self.grace_period.blockSignals(True)
        self.sigmoid_enabled.blockSignals(True)
        self.streak_speed.blockSignals(True)

        preset = self.preset_combo.currentData()
        if preset == "relaxed":
            self.health_max.setValue(100)
            self.health_drain.setValue(1.0)
            self.heal_easy.setValue(20)
            self.heal_good.setValue(15)
            self.heal_hard.setValue(10)
            self.heal_again.setValue(-5)
            self.alert_delay.setValue(15)
            self.reveal_delay.setValue(30)
            self.grace_period.setValue(300)
            self.sigmoid_enabled.setChecked(False)
            self.streak_speed.setValue(8.0)
        elif preset == "balanced":
            self.health_max.setValue(100)
            self.health_drain.setValue(2.0)
            self.heal_easy.setValue(15)
            self.heal_good.setValue(10)
            self.heal_hard.setValue(5)
            self.heal_again.setValue(-10)
            self.alert_delay.setValue(8)
            self.reveal_delay.setValue(0)
            self.grace_period.setValue(180)
            self.sigmoid_enabled.setChecked(True)
            self.streak_speed.setValue(5.0)
        elif preset == "hardcore":
            self.health_max.setValue(100)
            self.health_drain.setValue(4.0)
            self.heal_easy.setValue(10)
            self.heal_good.setValue(5)
            self.heal_hard.setValue(2)
            self.heal_again.setValue(-20)
            self.alert_delay.setValue(5)
            self.reveal_delay.setValue(10)
            self.grace_period.setValue(60)
            self.sigmoid_enabled.setChecked(True)
            self.streak_speed.setValue(3.0)

        # Unblock signals
        self.health_max.blockSignals(False)
        self.health_drain.blockSignals(False)
        self.heal_easy.blockSignals(False)
        self.heal_good.blockSignals(False)
        self.heal_hard.blockSignals(False)
        self.heal_again.blockSignals(False)
        self.alert_delay.blockSignals(False)
        self.reveal_delay.blockSignals(False)
        self.grace_period.blockSignals(False)
        self.sigmoid_enabled.blockSignals(False)
        self.streak_speed.blockSignals(False)
        self.update_preset_status()

    def save_settings(self):
        # Merge instead of rebuilding from scratch so future/default keys are
        # not silently deleted by older settings dialogs.
        new_config = dict(self.config)
        new_config.update({
            "ui_language": self.language_combo.currentData(),
            "collectibles_enabled": self.collectibles_enabled.isChecked(),
            "health_max": self.health_max.value(),
            "health_drain_rate": self.health_drain.value(),
            "health_heal_easy": self.heal_easy.value(),
            "health_heal_good": self.heal_good.value(),
            "health_heal_hard": self.heal_hard.value(),
            "health_heal_again": self.heal_again.value(),
            "pomodoro_study_duration": self.study_dur.value(),
            "pomodoro_break_duration": self.break_dur.value(),
            "pomodoro_enable_sound": self.pomo_sound.isChecked(),
            "pomodoro_visible": self.pomo_visible.isChecked(),
            "speed_focus_alert_delay": self.alert_delay.value(),
            "speed_focus_reveal_delay": self.reveal_delay.value(),
            # Kept for migration compatibility; automatic grading is never run.
            "speed_focus_fail_delay": 0,
            "speed_focus_volume": self.speed_volume.value(),
            "no_distraction_enabled": self.dist_enabled.isChecked(),
            "no_distraction_auto_fullscreen": self.auto_fs.isChecked(),
            "no_distraction_hide_menu_bar": self.hide_menu.isChecked(),
            "no_distraction_hide_top_toolbar": self.hide_toolbar.isChecked(),
            "no_distraction_hide_bottom_bar": self.hide_bottom.isChecked(),
            "hide_answer_buttons": self.hide_buttons.isChecked(),
            "custom_answer_buttons": self.custom_buttons.isChecked(),
            "adhd_boosters_streak_enabled": self.streak_enabled.isChecked(),
            "adhd_boosters_shield_enabled": self.shield_enabled.isChecked(),
            "adhd_boosters_autopause_enabled": self.autopause_enabled.isChecked(),
            "adhd_boosters_low_health_heartbeat": self.heartbeat_enabled.isChecked(),
            "preset_mode": self.preset_combo.currentData(),
            "grace_period_duration": self.grace_period.value(),
            "sigmoid_drain_enabled": self.sigmoid_enabled.isChecked(),
            "streak_reset_idle_time": self.streak_reset_time.value(),
            "streak_speed_threshold": self.streak_speed.value(),
            "breathing_pattern": self.breath_pattern.currentData(),
            "bar_position": self.bar_pos.currentData(),
        })

        # Write to Anki configuration system
        mw.addonManager.writeConfig(__name__, new_config)
        if _collection_action is not None:
            _collection_action.setText(
                tr("open_collection", new_config.get("ui_language", "de"))
            )

        # Defer live reviewer layout update to ensure the dialog modal loop closes
        self.accept()
        QTimer.singleShot(200, lambda: update_live_reviewer_layout(new_config))
        showInfo(tr("settings_saved", new_config.get("ui_language", "de")))

def update_live_reviewer_layout(new_config):
    if mw.state == "review":
        config_json = _json_for_script(new_config)
        if mw.reviewer and mw.reviewer.web:
            mw.reviewer.web.eval(f"if (window.AnkiLockIn) {{ window.AnkiLockIn.updateConfig({config_json}); }}")
            if _latest_buttons_payload:
                buttons_json = _json_for_script(_latest_buttons_payload)
                mw.reviewer.web.eval(f"if (window.AnkiLockIn && window.AnkiLockIn.updateButtons) {{ window.AnkiLockIn.updateButtons({buttons_json}); }}")
        
        bottom_web = None
        if hasattr(mw, "bottomWeb") and mw.bottomWeb:
            bottom_web = mw.bottomWeb
        elif mw.reviewer and hasattr(mw.reviewer, "bottom") and hasattr(mw.reviewer.bottom, "web") and mw.reviewer.bottom.web:
            bottom_web = mw.reviewer.bottom.web
            
        if bottom_web:
            bottom_web.eval(f"if (window.AnkiLockIn) {{ window.AnkiLockIn.updateConfig({config_json}); }}")
        
        # Restore our baseline first, then apply the new choices. This keeps
        # custom/native answer rows mutually exclusive without forcing chrome
        # that was already hidden before the review started.
        capture_review_layout()
        set_distraction_free(True)
        set_distraction_free(False)
        wants_fullscreen = (
            new_config.get("no_distraction_enabled", True)
            and new_config.get("no_distraction_auto_fullscreen", True)
        )
        baseline_fullscreen = bool(_layout_snapshot and _layout_snapshot["fullscreen"])
        if wants_fullscreen and not mw.isFullScreen():
            mw.showFullScreen()
        elif not wants_fullscreen and baseline_fullscreen and not mw.isFullScreen():
            mw.showFullScreen()
        elif not wants_fullscreen and not baseline_fullscreen and mw.isFullScreen():
            mw.showNormal()
        push_collection_snapshot()

def show_settings_dialog():
    dialog = LockInSettingsDialog(mw)
    dialog.exec()


def setup_collection_action():
    global _collection_action
    if _collection_action is not None:
        _collection_action.setText(text("open_collection"))
        return
    menu = getattr(getattr(mw, "form", None), "menuTools", None)
    if menu is None:
        return
    _collection_action = QAction(text("open_collection"), mw)
    _collection_action.triggered.connect(show_collection_dialog)
    menu.addAction(_collection_action)


def _cancel_collection_retries():
    global _retry_timer, _retry_delay_ms, _retry_overflow_pending
    if _retry_timer is not None:
        _retry_timer.stop()
        _retry_timer.deleteLater()
        _retry_timer = None
    _retry_delay_ms = 250
    _retry_overflow_pending = False


def on_profile_did_open(*_args):
    global _review_session_id, _collectibles_store, _tracked_answers, _retry_queue, _pending_unlock_notifications, _latest_buttons_payload, _answer_undo_counters, session_stats, pomo_state
    _cancel_collection_retries()
    _tracked_answers.clear()
    _pending_redo_counters.clear()
    _retry_queue.clear()
    _pending_unlock_notifications.clear()
    _latest_buttons_payload = None
    _answer_undo_counters.clear()
    _review_session_id = None
    session_stats = {"cards": 0, "correct": 0, "max_streak": 0, "shields": 0, "study_time": 0}
    pomo_state = {
        "state": "study",
        "pre_pause_state": "study",
        "time_left": -1,
        "last_sync_timestamp": 0.0,
    }
    store = get_collectibles_store()
    if store is not None:
        store.set_profile(_profile_name())
    setup_collection_action()


def on_profile_will_close(*_args):
    global _review_session_id, _collectibles_store, _tracked_answers, _retry_queue, _pending_unlock_notifications, _latest_buttons_payload, _answer_undo_counters
    _cancel_collection_retries()
    _tracked_answers.clear()
    _pending_redo_counters.clear()
    _retry_queue.clear()
    _pending_unlock_notifications.clear()
    _latest_buttons_payload = None
    _answer_undo_counters.clear()
    store = _collectibles_store
    if store is None:
        _review_session_id = None
        return
    try:
        if _review_session_id:
            store.end_session(_review_session_id)
    except Exception:
        _log.exception("Unable to end cat collection session")
    try:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    except Exception:
        _log.exception("Unable to close cat collection state")
    _collectibles_store = None
    _review_session_id = None

# Hook the Config Action in the Anki Add-ons list
if mw and getattr(mw, "addonManager", None):
    mw.addonManager.setConfigAction(__name__, show_settings_dialog)

if hasattr(gui_hooks, "profile_did_open"):
    gui_hooks.profile_did_open.append(on_profile_did_open)
if hasattr(gui_hooks, "profile_will_close"):
    gui_hooks.profile_will_close.append(on_profile_will_close)

# Set up shortcuts on startup
if mw:
    setup_shortcuts()
    setup_collection_action()
