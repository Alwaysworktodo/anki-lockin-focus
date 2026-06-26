import json
import re
import time
import os
import tempfile
from aqt import mw, gui_hooks

_temp_dir = tempfile.gettempdir()
_log_file_path = os.path.join(_temp_dir, 'anki-lockin-debug.log')
_config_debug_path = os.path.join(_temp_dir, 'lockin-config-debug.json')
_js_debug_log_path = os.path.join(_temp_dir, 'lockin-js-debug.log')
from aqt.qt import *
from aqt.utils import showInfo

# Register the web folder for exports
if mw and getattr(mw, "addonManager", None):
    mw.addonManager.setWebExports(__name__, r"web/.*(css|js)")

# Global references to keep track of shortcuts and session stats
_fs_shortcut = None
_df_shortcut = None
session_stats = {"cards": 0, "correct": 0, "max_streak": 0, "shields": 0, "study_time": 0}
pomo_state = {"state": "study", "time_left": -1, "last_sync_timestamp": 0.0}
# Number of answer buttons currently displayed (2, 3, or 4) — used to
# translate numeric ease into a semantic label ("again"/"hard"/"good"/"easy")
# for health mapping in JS.
_current_button_count = 4

# Safe cross-version alignments
ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter if hasattr(Qt, "AlignmentFlag") else Qt.AlignCenter
ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight if hasattr(Qt, "AlignmentFlag") else Qt.AlignRight

def get_config():
    if not mw or not getattr(mw, "addonManager", None):
        return {}
    return mw.addonManager.getConfig(__name__) or {}

def set_distraction_free(visible: bool, ignore_config: bool = False):
    """
    Shows or hides standard Anki window chrome to create a distraction-free screen.
    """
    config = get_config()
    if not config:
        return

    # If hiding, we check the config unless ignore_config is set
    if not visible and not ignore_config and not config.get("no_distraction_enabled", True):
        return

    # If visible is True, we want to show EVERYTHING (restore state)
    if visible:
        mw.menuBar().setVisible(True)
        if hasattr(mw, "toolbar") and mw.toolbar and hasattr(mw.toolbar, "web") and mw.toolbar.web:
            mw.toolbar.web.setVisible(True)
        if hasattr(mw, "bottomWeb") and mw.bottomWeb:
            mw.bottomWeb.setVisible(True)
    else:
        # Hiding based on config preferences (or hiding everything if ignore_config is True)
        if ignore_config or config.get("no_distraction_hide_menu_bar", True):
            mw.menuBar().setVisible(False)
        if ignore_config or config.get("no_distraction_hide_top_toolbar", True):
            if hasattr(mw, "toolbar") and mw.toolbar and hasattr(mw.toolbar, "web") and mw.toolbar.web:
                mw.toolbar.web.setVisible(False)
        if hasattr(mw, "bottomWeb") and mw.bottomWeb:
            should_hide = ignore_config or \
                          config.get("no_distraction_hide_bottom_bar", False) or \
                          config.get("custom_answer_buttons", True) or \
                          (config.get("hide_answer_buttons", False) and config.get("bar_position", "bottom") == "top")
            if should_hide:
                mw.bottomWeb.setVisible(False)
            else:
                mw.bottomWeb.setVisible(True)

def restore_normal_layout_deferred():
    from aqt.qt import sip
    if sip.isdeleted(mw) or mw.state == "review":
        return
    set_distraction_free(True)
    if mw.isFullScreen():
        mw.showNormal()

def on_state_did_change(new_state, old_state):
    """
    Triggers automatically when the main Anki state changes.
    Enters fullscreen/distraction-free mode when entering the 'review' state,
    and returns to normal layout when leaving it.
    """
    global session_stats
    config = get_config()
    if not config:
        return

    # When entering review mode
    if new_state == "review":
        # Reset session stats for new session
        session_stats = {"cards": 0, "correct": 0, "max_streak": 0, "shields": 0, "study_time": 0}
        
        if config.get("no_distraction_enabled", True):
            set_distraction_free(False)
            if config.get("no_distraction_auto_fullscreen", True):
                mw.showFullScreen()
    
    # When leaving review mode or entering deck browser / overview
    if new_state in ("deckBrowser", "overview") or old_state == "review":
        # Unconditionally restore standard window chrome and normal screen state deferred
        QTimer.singleShot(150, restore_normal_layout_deferred)
        
        # Show session stats dialog if we are transitioning out of review and cards were studied
        if old_state == "review" and session_stats.get("cards", 0) > 0:
            QTimer.singleShot(400, show_session_stats_dialog)

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

    _df_shortcut = QShortcut(QKeySequence("Ctrl+Shift+D"), mw)
    _df_shortcut.activated.connect(toggle_distraction_free)

# Register hooks
gui_hooks.state_did_change.append(on_state_did_change)

# Hook into Web Content Loading to inject JS and CSS
def on_webview_will_set_content(web_content, context):
    from aqt.qt import sip
    try:
        if sip.isdeleted(context):
            return
    except Exception:
        pass

    import aqt.reviewer
    import aqt.deckbrowser
    import aqt.overview
    allowed_contexts = [aqt.reviewer.Reviewer]
    if hasattr(aqt.reviewer, "ReviewerBottomBar"):
        allowed_contexts.append(aqt.reviewer.ReviewerBottomBar)
    if hasattr(aqt.deckbrowser, "DeckBrowser"):
        allowed_contexts.append(aqt.deckbrowser.DeckBrowser)
    if hasattr(aqt.overview, "Overview"):
        allowed_contexts.append(aqt.overview.Overview)
        
    if not isinstance(context, tuple(allowed_contexts)):
        return

    addon_package = mw.addonManager.addonFromModule(__name__)
    config = get_config()
    
    # Calculate real-time elapsed since last sync if not paused
    current_time = time.time()
    state = pomo_state.get("state", "study")
    time_left = pomo_state.get("time_left", -1)
    last_sync = pomo_state.get("last_sync_timestamp", 0.0)
    
    if time_left > 0 and last_sync > 0.0 and state != "paused":
        elapsed = current_time - last_sync
        if elapsed > 0:
            study_duration = config.get("pomodoro_study_duration", 25) * 60
            break_duration = config.get("pomodoro_break_duration", 5) * 60
            
            # Simulate pomodoro transitions over the elapsed period
            if state == "study":
                if time_left - elapsed > 0:
                    time_left -= elapsed
                else:
                    remaining = elapsed - time_left
                    state = "break"
                    time_left = max(0.0, break_duration - remaining)
            elif state == "break":
                if time_left - elapsed > 0:
                    time_left -= elapsed
                else:
                    remaining = elapsed - time_left
                    state = "study"
                    time_left = max(0.0, study_duration - remaining)
            
            pomo_state["state"] = state
            pomo_state["time_left"] = time_left
            pomo_state["last_sync_timestamp"] = current_time

    # Determine the webview kind from Python context
    if hasattr(aqt.reviewer, "ReviewerBottomBar") and isinstance(context, aqt.reviewer.ReviewerBottomBar):
        webview_kind = "bottom"
    elif hasattr(aqt.deckbrowser, "DeckBrowser") and isinstance(context, aqt.deckbrowser.DeckBrowser):
        webview_kind = "deckBrowser"
    elif hasattr(aqt.overview, "Overview") and isinstance(context, aqt.overview.Overview):
        webview_kind = "overview"
    else:
        webview_kind = "main"

    # Inject current configuration and persisted Pomodoro state into global JS variables
    config_json = json.dumps(config)
    pomo_json = json.dumps(pomo_state)
    web_content.head += f"<script>window.AnkiLockInConfig = {config_json}; window.AnkiLockInPomoState = {pomo_json}; window.AnkiLockInWebviewKind = '{webview_kind}';</script>"

    # Append custom stylesheet and script to the reviewer content.
    # Version query strings bust Anki's web cache after addon updates.
    web_content.css.append(f"/_addons/{addon_package}/web/lockin.css?v=4")
    web_content.js.append(f"/_addons/{addon_package}/web/lockin.js?v=4")

gui_hooks.webview_will_set_content.append(on_webview_will_set_content)

def apply_distraction_free_if_reviewing():
    if mw.state == "review":
        config = get_config()
        if config and config.get("no_distraction_enabled", True):
            set_distraction_free(False)
            if config.get("no_distraction_auto_fullscreen", True):
                if not mw.isFullScreen():
                    mw.showFullScreen()

# Hook into Reviewer Lifecycle events to sync state to JavaScript
import logging
_log = logging.getLogger("anki-lockin-focus")
_log.setLevel(logging.DEBUG)
if not _log.handlers:
    _fh = logging.FileHandler(_log_file_path, mode='w', encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    _log.addHandler(_fh)

def on_reviewer_did_show_question(card):
    _log.info("HOOK: reviewer_did_show_question fired")
    if mw.reviewer and mw.reviewer.web:
        # Track that we're in question mode so JS can restore state after webview re-renders
        mw.reviewer.web.eval("window.AnkiLockInAnswerMode = false; if (window.AnkiLockIn) { window.AnkiLockIn.onShowQuestion(); }")
    # Enforce distraction-free window state deferred by 100ms to override any Anki layout resets (e.g. during deck switching)
    QTimer.singleShot(100, apply_distraction_free_if_reviewing)

def on_reviewer_did_show_answer(card):
    _log.info("HOOK: reviewer_did_show_answer fired")
    global _current_button_count
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

    # Inject the custom answer button row (label + interval) for ONLY the
    # number of buttons the current card actually supports.
    try:
        if card is None:
            return
        config = get_config()
        if not config.get("custom_answer_buttons", True):
            _log.info("  custom_answer_buttons disabled, skipping interval injection")
            return

        # Determine the active button count for this card.
        try:
            button_count = mw.col.sched.answerButtons(card)
        except Exception:
            button_count = 4
        _current_button_count = button_count

        # Map the count to the canonical Anki label set (matches Anki's
        # internal _answerButtonList).
        if button_count == 2:
            button_list = [(1, "Again"), (2, "Good")]
        elif button_count == 3:
            button_list = [(1, "Again"), (2, "Good"), (3, "Easy")]
        else:
            button_count = 4
            button_list = [(1, "Again"), (2, "Hard"), (3, "Good"), (4, "Easy")]
        _current_button_count = button_count

        # Build the buttons payload. Prefer the V3 scheduling path which
        # honours learning steps / presets; fall back to legacy nextIvlStr.
        try:
            states = mw.reviewer.get_scheduling_states()
            v3_labels = mw.col.sched.describe_next_states(states)
        except Exception:
            v3_labels = None

        buttons_data = {}
        for ease, label in button_list:
            interval = ""
            try:
                if v3_labels:
                    raw = mw.reviewer._buttonTime(ease, v3_labels=v3_labels)
                    interval = re.sub(r"<[^>]+>", "", raw).strip()
                else:
                    raise RuntimeError("no v3_labels")
            except Exception:
                try:
                    interval = mw.col.sched.nextIvlStr(card.id, ease)
                except Exception:
                    interval = ""
            buttons_data[str(ease)] = {"label": label, "interval": interval}

        _log.info("  buttons_data: %s", buttons_data)
        buttons_json = json.dumps(buttons_data)
        mw.reviewer.web.eval(
            f"if (window.AnkiLockIn) {{ window.AnkiLockIn.updateButtons({buttons_json}); }}"
        )
    except Exception as e:
        _log.exception("Error in on_reviewer_did_show_answer")
        try:
            from aqt.utils import showWarning
            showWarning(f"Anki Lock-In: failed to inject answer button data: {e}")
        except Exception:
            print("Anki Lock-In: error injecting answer button data:", e)

def on_reviewer_did_answer_card(reviewer, card, ease):
    _log.info("HOOK: reviewer_did_answer_card fired (ease=%s)", ease)
    if mw.reviewer and mw.reviewer.web:
        # Map numeric ease to semantic label so JS can pick the right health
        # delta regardless of how many answer buttons the card shows.
        if _current_button_count == 2:
            label = {1: "again", 2: "good"}.get(ease, "good")
        elif _current_button_count == 3:
            label = {1: "again", 2: "good", 3: "easy"}.get(ease, "good")
        else:
            label = {1: "again", 2: "hard", 3: "good", 4: "easy"}.get(ease, "good")
        mw.reviewer.web.eval(
            f"if (window.AnkiLockIn) {{ window.AnkiLockIn.onAnswerCard({ease}, '{label}'); }}"
        )

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

# Hook into undo event to revert health/stats gains
if hasattr(gui_hooks, "state_did_undo"):
    def on_state_did_undo(changes):
        if mw.state == "review" and mw.reviewer and mw.reviewer.web:
            mw.reviewer.web.eval("if (window.AnkiLockIn) { window.AnkiLockIn.onUndo(); }")
    gui_hooks.state_did_undo.append(on_state_did_undo)

# Bridge communication from JavaScript back to Python
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

    if message == "anki-lockin-exit":
        # Exit review mode and return to deck browser (Stapelansicht) deferred to avoid unloading conflicts
        QTimer.singleShot(100, lambda: mw.moveToState("deckBrowser"))
        return (True, None)

    # NOTE: The following three handlers were removed because they
    # duplicate state we now push from Python proactively:
    #   - "lockin-request-intervals"   -> on_reviewer_did_show_answer pushes updateButtons
    #   - "anki-lockin-answer:"        -> buttons now call Anki's native pycmd("easeN")
    #   - "lockin-animate:"            -> reviewer_will_answer_card hook fires animateButton

    if message.startswith("anki-lockin-stats:"):
        try:
            stats_json = message[len("anki-lockin-stats:"):]
            session_stats = json.loads(stats_json)
        except Exception as e:
            print("Error parsing stats message:", e)
        return (True, None)

    if message.startswith("lockin-pomo-sync:"):
        try:
            data_json = message[len("lockin-pomo-sync:"):]
            data = json.loads(data_json)
            pomo_state["state"] = data.get("state")
            pomo_state["time_left"] = data.get("time_left")
            pomo_state["last_sync_timestamp"] = time.time()
        except Exception as e:
            print("Error syncing pomo state:", e)
        return (True, None)

    if message.startswith("lockin-debug:"):
        try:
            data_json = message[len("lockin-debug:"):]
            with open(_log_file_path, 'a', encoding='utf-8') as f:
                f.write("JS: " + data_json + "\n")
        except Exception as e:
            print("Error writing JS debug:", e)
        return (True, None)

    return handled

gui_hooks.webview_did_receive_js_message.append(on_js_message)


# ----------------------------------------------------
# Session Stats Dialog
# ----------------------------------------------------

class SessionStatsDialog(QDialog):
    def __init__(self, stats, parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle("Session Performance Summary")
        self.setMinimumWidth(380)
        self.stats = stats
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("Focus Session Complete! ⚡")
        title.setAlignment(ALIGN_CENTER)
        title.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px; color: #1e90ff;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(ALIGN_RIGHT)
        
        cards = self.stats.get("cards", 0)
        correct = self.stats.get("correct", 0)
        accuracy = (correct / cards * 100) if cards > 0 else 0
        
        form.addRow("Cards Studied:", QLabel(str(cards)))
        form.addRow("Accuracy Rate:", QLabel(f"{accuracy:.1f}%"))
        form.addRow("Max Focus Streak:", QLabel(str(self.stats.get("max_streak", 0))))
        form.addRow("Shields Activated:", QLabel(str(self.stats.get("shields", 0))))
        
        study_time = self.stats.get("study_time", 0)
        mins = int(study_time // 60)
        secs = int(study_time % 60)
        form.addRow("Total Focus Time:", QLabel(f"{mins:02d}:{secs:02d}"))
        
        layout.addLayout(form)

        quote = QLabel("Excellent work staying locked in! Keep the focus sharp.")
        quote.setWordWrap(True)
        quote.setAlignment(ALIGN_CENTER)
        quote.setStyleSheet("font-style: italic; color: #777; margin-top: 10px; margin-bottom: 10px;")
        layout.addWidget(quote)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def show_session_stats_dialog():
    dialog = SessionStatsDialog(session_stats)
    dialog.exec()


# ----------------------------------------------------
# Settings Dialog Implementation (PyQt)
# ----------------------------------------------------

class LockInSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle("Anki Lock-In & ADHD Focus Booster Settings")
        self.setMinimumWidth(550)
        self.config = get_config()
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # Tab Widget to organize groups of configurations
        self.tabs = QTabWidget()

        # --- Tab 0: Presets Mode ---
        self.tab_preset = QWidget()
        layout_preset = QFormLayout(self.tab_preset)
        
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["Relaxed", "Balanced", "Hardcore"])
        
        # Select current preset in combobox
        current_preset = self.config.get("preset_mode", "balanced").capitalize()
        index = self.preset_combo.findText(current_preset)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
            
        self.preset_combo.currentTextChanged.connect(self.on_preset_changed)
        layout_preset.addRow("Select Profile Preset:", self.preset_combo)
        
        desc = QLabel(
          "Changing the profile preset automatically fills out the focus difficulty settings "
          "in the tabs below. You can still manually customize them afterwards!"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #777; margin-top: 15px; font-style: italic;")
        layout_preset.addRow("", desc)
        
        self.tabs.addTab(self.tab_preset, "Profile Presets")

        # --- Tab 1: Health Bar Settings ---
        self.tab_health = QWidget()
        layout_health = QFormLayout(self.tab_health)
        
        self.health_max = QSpinBox()
        self.health_max.setRange(50, 500)
        self.health_max.setValue(self.config.get("health_max", 100))
        layout_health.addRow("Maximum Health:", self.health_max)

        self.health_drain = QDoubleSpinBox()
        self.health_drain.setRange(0.1, 15.0)
        self.health_drain.setSingleStep(0.1)
        self.health_drain.setValue(self.config.get("health_drain_rate", 2.0))
        layout_health.addRow("Health Drain Rate (per sec):", self.health_drain)

        self.heal_easy = QSpinBox()
        self.heal_easy.setRange(0, 100)
        self.heal_easy.setValue(self.config.get("health_heal_easy", 15))
        layout_health.addRow("Heal Amount (Easy Answer):", self.heal_easy)

        self.heal_good = QSpinBox()
        self.heal_good.setRange(0, 100)
        self.heal_good.setValue(self.config.get("health_heal_good", 10))
        layout_health.addRow("Heal Amount (Good Answer):", self.heal_good)

        self.heal_hard = QSpinBox()
        self.heal_hard.setRange(0, 100)
        self.heal_hard.setValue(self.config.get("health_heal_hard", 5))
        layout_health.addRow("Heal Amount (Hard Answer):", self.heal_hard)

        self.heal_again = QSpinBox()
        self.heal_again.setRange(-100, 100)
        self.heal_again.setValue(self.config.get("health_heal_again", -10))
        layout_health.addRow("Heal/Drain Amount (Again Answer):", self.heal_again)

        self.tabs.addTab(self.tab_health, "Life Drain")

        # --- Tab 2: Speed Focus (Silent) ---
        self.tab_speed = QWidget()
        layout_speed = QFormLayout(self.tab_speed)

        self.alert_delay = QSpinBox()
        self.alert_delay.setRange(0, 120)
        self.alert_delay.setValue(self.config.get("speed_focus_alert_delay", 8))
        layout_speed.addRow("Warning Sound Delay (seconds, 0 to disable):", self.alert_delay)

        self.reveal_delay = QSpinBox()
        self.reveal_delay.setRange(0, 120)
        self.reveal_delay.setValue(self.config.get("speed_focus_reveal_delay", 0))
        layout_speed.addRow("Auto-Reveal Answer Delay (seconds, 0 to disable):", self.reveal_delay)

        self.fail_delay = QSpinBox()
        self.fail_delay.setRange(0, 120)
        self.fail_delay.setValue(self.config.get("speed_focus_fail_delay", 0))
        layout_speed.addRow("Auto-Grade 'Again' Delay (seconds, 0 to disable):", self.fail_delay)

        self.speed_volume = QDoubleSpinBox()
        self.speed_volume.setRange(0.0, 1.0)
        self.speed_volume.setSingleStep(0.1)
        self.speed_volume.setValue(self.config.get("speed_focus_volume", 0.5))
        layout_speed.addRow("Audio Alert Volume (0.0 to 1.0):", self.speed_volume)

        self.tabs.addTab(self.tab_speed, "Speed Focus")

        # --- Tab 3: Pomodoro Timer ---
        self.tab_pomo = QWidget()
        layout_pomo = QFormLayout(self.tab_pomo)

        self.study_dur = QSpinBox()
        self.study_dur.setRange(1, 180)
        self.study_dur.setValue(self.config.get("pomodoro_study_duration", 25))
        layout_pomo.addRow("Study Session Length (minutes):", self.study_dur)

        self.break_dur = QSpinBox()
        self.break_dur.setRange(1, 60)
        self.break_dur.setValue(self.config.get("pomodoro_break_duration", 5))
        layout_pomo.addRow("Break Length (minutes):", self.break_dur)

        self.pomo_sound = QCheckBox()
        self.pomo_sound.setChecked(self.config.get("pomodoro_enable_sound", True))
        layout_pomo.addRow("Enable Pomodoro Interval Chimes:", self.pomo_sound)

        self.pomo_visible = QCheckBox()
        self.pomo_visible.setChecked(self.config.get("pomodoro_visible", True))
        layout_pomo.addRow("Show Pomodoro Timer in Bar:", self.pomo_visible)

        self.breath_pattern = QComboBox()
        self.breath_pattern.addItems([
            "Box Breathing (4-4-4-4)",
            "4-7-8 Relaxation (4-7-8)",
            "Coherent Breathing (5-5)",
            "Physiological Sigh (4-2-8)",
            "Equal Breathing (4-4)"
        ])
        current_pattern = self.config.get("breathing_pattern", "box")
        pattern_map = {"box": 0, "478": 1, "coherent": 2, "sigh": 3, "simple": 4}
        self.breath_pattern.setCurrentIndex(pattern_map.get(current_pattern, 0))
        layout_pomo.addRow("Breathing Exercise Pattern:", self.breath_pattern)

        self.tabs.addTab(self.tab_pomo, "Pomodoro")

        # --- Tab 4: No Distractions Full Screen ---
        self.tab_dist = QWidget()
        layout_dist = QFormLayout(self.tab_dist)

        self.dist_enabled = QCheckBox()
        self.dist_enabled.setChecked(self.config.get("no_distraction_enabled", True))
        layout_dist.addRow("Enable Distraction-Free Layout Modifications:", self.dist_enabled)

        self.auto_fs = QCheckBox()
        self.auto_fs.setChecked(self.config.get("no_distraction_auto_fullscreen", True))
        layout_dist.addRow("Auto Full Screen in Reviewer Mode:", self.auto_fs)

        self.hide_menu = QCheckBox()
        self.hide_menu.setChecked(self.config.get("no_distraction_hide_menu_bar", True))
        layout_dist.addRow("Hide Top Menu Bar (File, Edit, etc.):", self.hide_menu)

        self.hide_toolbar = QCheckBox()
        self.hide_toolbar.setChecked(self.config.get("no_distraction_hide_top_toolbar", True))
        layout_dist.addRow("Hide Main Navigation Tabs (Decks, Add, Sync):", self.hide_toolbar)

        self.hide_bottom = QCheckBox()
        self.hide_bottom.setChecked(self.config.get("no_distraction_hide_bottom_bar", False))
        layout_dist.addRow("Hide Bottom Bar (Warning: hides answer buttons in some Anki versions):", self.hide_bottom)

        self.hide_buttons = QCheckBox()
        self.hide_buttons.setChecked(self.config.get("hide_answer_buttons", False))
        layout_dist.addRow("Hide Review Answer Buttons (Shortcut input only):", self.hide_buttons)

        self.custom_buttons = QCheckBox()
        self.custom_buttons.setChecked(self.config.get("custom_answer_buttons", True))
        layout_dist.addRow("Render Custom Answer Buttons (requires Hide Bottom Bar):", self.custom_buttons)

        self.bar_pos = QComboBox()
        self.bar_pos.addItems(["Top", "Bottom"])
        current_pos = self.config.get("bar_position", "bottom").capitalize()
        index_pos = self.bar_pos.findText(current_pos)
        if index_pos >= 0:
            self.bar_pos.setCurrentIndex(index_pos)
        layout_dist.addRow("Bar Position (Top / Bottom):", self.bar_pos)

        self.tabs.addTab(self.tab_dist, "Full Screen")

        # --- Tab 5: ADHD Boosters ---
        self.tab_adhd = QWidget()
        layout_adhd = QFormLayout(self.tab_adhd)

        self.streak_enabled = QCheckBox()
        self.streak_enabled.setChecked(self.config.get("adhd_boosters_streak_enabled", True))
        layout_adhd.addRow("Enable Focus Streak Combo Multiplier (Slowing Drain):", self.streak_enabled)

        self.shield_enabled = QCheckBox()
        self.shield_enabled.setChecked(self.config.get("adhd_boosters_shield_enabled", True))
        layout_adhd.addRow("Enable Focus Shield (Invulnerability after fast answers):", self.shield_enabled)

        self.autopause_enabled = QCheckBox()
        self.autopause_enabled.setChecked(self.config.get("adhd_boosters_autopause_enabled", True))
        layout_adhd.addRow("Auto-Pause Timer on Window Focus Loss (Alt-Tab):", self.autopause_enabled)

        self.heartbeat_enabled = QCheckBox()
        self.heartbeat_enabled.setChecked(self.config.get("adhd_boosters_low_health_heartbeat", True))
        layout_adhd.addRow("Rhythmic Auditory Heartbeat Tick when Health <25%:", self.heartbeat_enabled)

        self.grace_period = QSpinBox()
        self.grace_period.setRange(0, 600)
        self.grace_period.setValue(self.config.get("grace_period_duration", 180))
        layout_adhd.addRow("Warm-Up Grace Period (seconds, no drain at start):", self.grace_period)

        self.sigmoid_enabled = QCheckBox()
        self.sigmoid_enabled.setChecked(self.config.get("sigmoid_drain_enabled", True))
        layout_adhd.addRow("Use Sigmoid Drain (slow start, accelerates stall):", self.sigmoid_enabled)

        self.streak_reset_time = QSpinBox()
        self.streak_reset_time.setRange(5, 300)
        self.streak_reset_time.setValue(self.config.get("streak_reset_idle_time", 30))
        layout_adhd.addRow("Streak Idle Reset Threshold (seconds):", self.streak_reset_time)

        self.streak_speed = QDoubleSpinBox()
        self.streak_speed.setRange(1.0, 30.0)
        self.streak_speed.setSingleStep(0.5)
        self.streak_speed.setValue(self.config.get("streak_speed_threshold", 5.0))
        layout_adhd.addRow("Streak Answer Speed Threshold (seconds):", self.streak_speed)

        self.tabs.addTab(self.tab_adhd, "ADHD Lock-In")

        main_layout.addWidget(self.tabs)

        # Standard dialog save/cancel button box
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.save_settings)
        self.buttons.rejected.connect(self.reject)
        main_layout.addWidget(self.buttons)

    def on_preset_changed(self, text):
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
        self.fail_delay.blockSignals(True)
        self.grace_period.blockSignals(True)
        self.sigmoid_enabled.blockSignals(True)
        self.streak_speed.blockSignals(True)

        if text == "Relaxed":
            self.health_max.setValue(100)
            self.health_drain.setValue(1.0)
            self.heal_easy.setValue(20)
            self.heal_good.setValue(15)
            self.heal_hard.setValue(10)
            self.heal_again.setValue(-5)
            self.alert_delay.setValue(15)
            self.reveal_delay.setValue(30)
            self.fail_delay.setValue(0)
            self.grace_period.setValue(300)
            self.sigmoid_enabled.setChecked(False)
            self.streak_speed.setValue(8.0)
        elif text == "Balanced":
            self.health_max.setValue(100)
            self.health_drain.setValue(2.0)
            self.heal_easy.setValue(15)
            self.heal_good.setValue(10)
            self.heal_hard.setValue(5)
            self.heal_again.setValue(-10)
            self.alert_delay.setValue(8)
            self.reveal_delay.setValue(0)
            self.fail_delay.setValue(0)
            self.grace_period.setValue(180)
            self.sigmoid_enabled.setChecked(True)
            self.streak_speed.setValue(5.0)
        elif text == "Hardcore":
            self.health_max.setValue(100)
            self.health_drain.setValue(4.0)
            self.heal_easy.setValue(10)
            self.heal_good.setValue(5)
            self.heal_hard.setValue(2)
            self.heal_again.setValue(-20)
            self.alert_delay.setValue(5)
            self.reveal_delay.setValue(10)
            self.fail_delay.setValue(15)
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
        self.fail_delay.blockSignals(False)
        self.grace_period.blockSignals(False)
        self.sigmoid_enabled.blockSignals(False)
        self.streak_speed.blockSignals(False)

    def save_settings(self):
        new_config = {
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
            "speed_focus_fail_delay": self.fail_delay.value(),
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
            "preset_mode": self.preset_combo.currentText().lower(),
            "grace_period_duration": self.grace_period.value(),
            "sigmoid_drain_enabled": self.sigmoid_enabled.isChecked(),
            "streak_reset_idle_time": self.streak_reset_time.value(),
            "streak_speed_threshold": self.streak_speed.value(),
            "breathing_pattern": ["box", "478", "coherent", "sigh", "simple"][self.breath_pattern.currentIndex()],
            "bar_position": self.bar_pos.currentText().lower(),
        }

        # Write to Anki configuration system
        mw.addonManager.writeConfig(__name__, new_config)

        # Defer live reviewer layout update to ensure the dialog modal loop closes
        self.accept()
        QTimer.singleShot(200, lambda: update_live_reviewer_layout(new_config))
        showInfo("Settings saved successfully! Focus parameters updated live.")

def update_live_reviewer_layout(new_config):
    if mw.state == "review":
        config_json = json.dumps(new_config)
        if mw.reviewer and mw.reviewer.web:
            mw.reviewer.web.eval(f"if (window.AnkiLockIn) {{ window.AnkiLockIn.updateConfig({config_json}); }}")
        
        bottom_web = None
        if hasattr(mw, "bottomWeb") and mw.bottomWeb:
            bottom_web = mw.bottomWeb
        elif mw.reviewer and hasattr(mw.reviewer, "bottom") and hasattr(mw.reviewer.bottom, "web") and mw.reviewer.bottom.web:
            bottom_web = mw.reviewer.bottom.web
            
        if bottom_web:
            bottom_web.eval(f"if (window.AnkiLockIn) {{ window.AnkiLockIn.updateConfig({config_json}); }}")
        
        # Instantly update distraction-free window layout
        if new_config.get("no_distraction_enabled", True):
            set_distraction_free(False)
            if new_config.get("no_distraction_auto_fullscreen", True):
                if not mw.isFullScreen():
                    mw.showFullScreen()
            else:
                if mw.isFullScreen():
                    mw.showNormal()
        else:
            set_distraction_free(True)
            if mw.isFullScreen():
                mw.showNormal()

def show_settings_dialog():
    dialog = LockInSettingsDialog(mw)
    dialog.exec()

# Hook the Config Action in the Anki Add-ons list
if mw and getattr(mw, "addonManager", None):
    mw.addonManager.setConfigAction(__name__, show_settings_dialog)

# Set up shortcuts on startup
if mw:
    setup_shortcuts()

try:
    with open(_config_debug_path, 'w', encoding='utf-8') as f:
        json.dump(get_config(), f)
    with open(_js_debug_log_path, 'w', encoding='utf-8') as f:
        f.write("")
except Exception as e:
    print("Error saving config debug:", e)
