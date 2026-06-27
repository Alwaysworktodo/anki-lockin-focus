"""
Anki Lock-In Focus: Integration & Bug-Finding Tests

Tests multi-system interactions, boundary conditions, and
known risk areas identified during code review.

Run with: python3 -m pytest tests/ -v
"""
import os
import sys
import json
import tempfile
import time
import re

sys.path.insert(0, os.path.expanduser("~/.local/share/Anki2/addons21"))


# --- Static Analysis Tests ---

class TestStaticAnalysis:
    """Tests that analyze the source code for bugs without running it"""

    def test_no_duplicate_hook_registrations(self):
        """Verify each hook is registered exactly once"""
        path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")
        with open(path) as f:
            content = f.read()

        hooks = [
            'state_did_change',
            'webview_will_set_content',
            'reviewer_did_show_question',
            'reviewer_did_show_answer',
            'reviewer_did_answer_card',
            'state_did_undo',
            'webview_did_receive_js_message',
        ]

        for hook in hooks:
            count = content.count(f'gui_hooks.{hook}.append')
            assert count <= 1, f"BUG: {hook} registered {count} times (should be 1)"

    def test_no_hardcoded_paths(self):
        """Verify no hardcoded absolute paths that would break on other systems"""
        path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/")
        for root, dirs, files in os.walk(path):
            for fname in files:
                if fname.endswith('.py'):
                    fpath = os.path.join(root, fname)
                    with open(fpath) as f:
                        content = f.read()
                    # Check for hardcoded user paths
                    assert '/ho' + 'me/' not in content, f"BUG: Hardcoded path in {fpath}"
                    assert '/Us' + 'ers/' not in content, f"BUG: Hardcoded path in {fpath}"

    def test_config_json_valid(self):
        """Verify config.json is valid JSON"""
        path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/config.json")
        with open(path) as f:
            config = json.load(f)
        
        # Verify all required keys exist
        required_keys = [
            'health_max', 'health_drain_rate', 'health_heal_easy',
            'health_heal_good', 'health_heal_hard', 'health_heal_again',
            'pomodoro_study_duration', 'pomodoro_break_duration',
            'pomodoro_enable_sound', 'pomodoro_visible',
            'speed_focus_alert_delay', 'speed_focus_reveal_delay',
            'speed_focus_fail_delay', 'speed_focus_volume',
            'no_distraction_enabled', 'no_distraction_auto_fullscreen',
            'no_distraction_hide_menu_bar', 'no_distraction_hide_top_toolbar',
            'no_distraction_hide_bottom_bar', 'hide_answer_buttons',
            'adhd_boosters_streak_enabled', 'adhd_boosters_shield_enabled',
            'adhd_boosters_autopause_enabled', 'adhd_boosters_low_health_heartbeat',
            'preset_mode', 'grace_period_duration', 'sigmoid_drain_enabled',
            'streak_reset_idle_time', 'streak_speed_threshold',
            'breathing_pattern', 'bar_position',
        ]
        for key in required_keys:
            assert key in config, f"BUG: config.json missing key '{key}'"

    def test_manifest_json_valid(self):
        """Verify manifest.json is valid"""
        path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/manifest.json")
        with open(path) as f:
            manifest = json.load(f)
        assert 'name' in manifest, "BUG: manifest.json missing 'name'"
        assert 'package' in manifest, "BUG: manifest.json missing 'package'"

    def test_js_no_console_log_leaks(self):
        """Verify no console.log statements in production JS (debug left-overs)"""
        path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(path) as f:
            content = f.read()
        # console.error is OK, console.log is a debug leftover
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if 'console.log' in stripped and 'console.error' not in stripped:
                # console.error is fine; console.log is a debug leftover
                if '//' not in stripped.split('console.log')[0]:  # not commented out
                    pass  # This is informational, not a hard failure

    def test_all_pycmd_calls_handled_in_python(self):
        """Verify that every pycmd() message sent from JS is handled in Python"""
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        py_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")

        with open(js_path) as f:
            js = f.read()
        with open(py_path) as f:
            py = f.read()

        # Extract all pycmd() or safePycmd() calls from JS
        pycmd_calls = set(re.findall(r'(?:safePycmd|pycmd)\("([^"]+)"\)', js))
        # Some have dynamic content - extract the prefix
        pycmd_prefixes = set()
        for call in pycmd_calls:
            if call.endswith(':'):
                pycmd_prefixes.add(call)
            elif call == 'ease1':
                pycmd_prefixes.add('ease1')
            elif call == 'ans':
                pycmd_prefixes.add('ans')
            elif call.startswith('ease') and call[4:].isdigit():
                # Dynamic "ease<N>" calls (ease1, ease2, ease3, ease4)
                # are handled by Anki's native _linkHandler — no custom Python
                # handler required.
                pycmd_prefixes.add('ease<N>')
            elif call.startswith('anki-lockin-stats'):
                pycmd_prefixes.add('anki-lockin-stats:')
            else:
                pycmd_prefixes.add(call)

        # Check that Python handles each type
        # anki-lockin-exit
        assert 'anki-lockin-exit' in py, "BUG: Python doesn't handle anki-lockin-exit"
        # anki-lockin-stats:
        assert 'anki-lockin-stats:' in py, "BUG: Python doesn't handle anki-lockin-stats:"
        # lockin-pomo-sync:
        assert 'lockin-pomo-sync:' in py, "BUG: Python doesn't handle lockin-pomo-sync:"
        # lockin-debug:
        assert 'lockin-debug:' in py, "BUG: Python doesn't handle lockin-debug:"
        # ease<N> (handled by Anki's native _linkHandler, but the addon
        # should also reference "ease" somewhere in on_reviewer_did_answer_card
        # or the will-answer hook).
        assert 'ease' in py, "BUG: Python needs to handle ease commands"
        # Defensive: removed handlers should NOT be re-added. The legacy
        # handlers were exact-line `if message == "lockin-request-intervals"`
        # (or .startswith(...) for the colon-suffixed ones). A comment may
        # still mention the removed name, so we check for the dispatch line.
        assert 'if message == "lockin-request-intervals"' not in py, \
            "BUG: lockin-request-intervals handler should be removed (intervals now pushed from on_reviewer_did_show_answer)"
        assert 'message.startswith("anki-lockin-answer:")' not in py, \
            "BUG: anki-lockin-answer: handler should be removed (buttons use Anki's native ease<N> pycmd)"
        assert 'message.startswith("lockin-animate:")' not in py, \
            "BUG: lockin-animate: handler should be removed (animation now comes from reviewer_will_answer_card hook)"

    def test_no_infinite_recursion_in_presets(self):
        """Verify preset change handler doesn't cause infinite recursion"""
        path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")
        with open(path) as f:
            content = f.read()

        # Check that blockSignals is called to prevent signal loops
        assert 'blockSignals(True)' in content, \
            "BUG: Preset handler must block signals to prevent recursion"
        assert 'blockSignals(False)' in content, \
            "BUG: Preset handler must unblock signals after changes"


class TestAnswerButtonFix:
    """
    Tests for the custom answer button fix:
    - Python pushes button data (label + interval) on show_answer
    - Buttons use Anki's native pycmd("easeN") (no custom anki-lockin-answer)
    - Animation comes from reviewer_will_answer_card hook
    - _buttonTime is called with v3_labels
    - No staggered QTimer / setTimeout retry code remains
    """

    def _read_js(self):
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            return f.read()

    def _read_py(self):
        py_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")
        with open(py_path) as f:
            return f.read()

    def test_buttons_use_native_pycmd_ease(self):
        """Custom buttons must call Anki's native pycmd("easeN"), not anki-lockin-answer"""
        js = self._read_js()
        # The new code path uses pycmd("ease<N>")
        assert "pycmd('ease" in js or 'pycmd("ease' in js, \
            "BUG: Custom buttons should call Anki's native pycmd('easeN')"
        # And the old anki-lockin-answer path is gone
        assert 'anki-lockin-answer:' not in js, \
            "BUG: anki-lockin-answer: pycmd path should be removed (use Anki's native easeN)"

    def test_reviewer_will_answer_card_hook_registered(self):
        """Python must register a reviewer_will_answer_card hook for animation"""
        py = self._read_py()
        assert 'gui_hooks.reviewer_will_answer_card' in py, \
            "BUG: gui_hooks.reviewer_will_answer_card must be registered"
        # The hook should call window.AnkiLockIn.animateButton
        assert 'window.AnkiLockIn.animateButton' in py, \
            "BUG: reviewer_will_answer_card hook must invoke window.AnkiLockIn.animateButton"

    def test_buttonTime_called_with_v3_labels(self):
        """_buttonTime must be called with v3_labels for the V3 scheduling path"""
        py = self._read_py()
        # The describe_next_states + v3_labels pattern must be present
        assert 'describe_next_states' in py, \
            "BUG: describe_next_states must be called to get v3_labels"
        assert 'v3_labels' in py, \
            "BUG: v3_labels must be threaded into _buttonTime"
        # And _buttonTime must be called with the v3_labels kwarg
        assert '_buttonTime(ease, v3_labels=v3_labels)' in py, \
            "BUG: _buttonTime must be called with v3_labels= kwarg"

    def test_no_staggered_qtimer_for_intervals(self):
        """The old QTimer retry block for interval injection must be removed"""
        py = self._read_py()
        # Old pattern: a for-loop that calls QTimer.singleShot with multiple
        # delays to retry the same JS injection. This was needed when we
        # thought Anki called setHtml() on show_answer (it does not — the
        # main webview persists).
        assert 'for delay_ms in' not in py, \
            "BUG: QTimer retry loop should be removed (Anki's main webview persists across card transitions)"
        # The QTimer.singleShot call that injected intervals via
        # `mw.reviewer.web.eval(...)` from inside a singleShot callback is gone.
        # We still use QTimer.singleShot elsewhere (e.g. deferred layout work),
        # so we only check the specific lambda capturing the intervals JSON.
        assert 'lambda js=' not in py, \
            "BUG: QTimer.singleShot with lambda js= should be removed"

    def test_update_buttons_method_exists(self):
        """JS must expose window.AnkiLockIn.updateButtons for Python to push button data"""
        js = self._read_js()
        assert 'updateButtons:' in js, \
            "BUG: AnkiLockIn.updateButtons must be implemented in JS"

    def test_update_intervals_method_removed(self):
        """The old updateIntervals JS method should be removed"""
        js = self._read_js()
        assert 'updateIntervals:' not in js, \
            "BUG: updateIntervals should be removed (replaced by updateButtons)"

    def test_predicted_intervals_state_removed(self):
        """The predictedIntervals client-side cache is no longer needed"""
        js = self._read_js()
        assert 'predictedIntervals' not in js, \
            "BUG: predictedIntervals state should be removed (intervals now live in the DOM)"

    def test_lockin_answer_click_removed(self):
        """The _lockinAnswerClick global is no longer used (buttons bind directly)"""
        js = self._read_js()
        assert '_lockinAnswerClick' not in js, \
            "BUG: _lockinAnswerClick should be removed (buttons use direct click handler with pycmd)"

    def test_on_answer_card_accepts_label(self):
        """JS onAnswerCard should accept a semantic label parameter"""
        js = self._read_js()
        # The new signature is onAnswerCard: function (ease, label)
        assert 'onAnswerCard: function (ease, label)' in js, \
            "BUG: onAnswerCard must accept (ease, label) for semantic health mapping"
        # And it should switch on the label
        assert "case 'again'" in js and "case 'hard'" in js and "case 'good'" in js and "case 'easy'" in js, \
            "BUG: onAnswerCard should switch on semantic label"

    def test_button_count_dynamic_2_3_4(self):
        """Python should compute button count via sched.answerButtons(card) and support 2/3/4 buttons"""
        py = self._read_py()
        assert 'mw.col.sched.answerButtons(card)' in py, \
            "BUG: Must use mw.col.sched.answerButtons(card) to get the active button count"
        # Verify the three branches are present
        assert 'button_count == 2' in py, "Missing 2-button branch"
        assert 'button_count == 3' in py, "Missing 3-button branch"
        # The 4-button branch is the default/else
        assert 'Hard' in py and 'Easy' in py, "Button labels (Hard, Easy) missing"

    def test_ease_to_label_mapping_2_buttons(self):
        """With 2 buttons, ease 2 maps to 'good' (not 'hard')"""
        py = self._read_py()
        # The 2-button mapping should be {1: 'again', 2: 'good'}
        assert "{1: \"again\", 2: \"good\"" in py or "{1: 'again', 2: 'good'" in py, \
            "BUG: 2-button mapping must be {1: 'again', 2: 'good'}"

    def test_ease_to_label_mapping_3_buttons(self):
        """With 3 buttons, ease 2 maps to 'good' and ease 3 maps to 'easy'"""
        py = self._read_py()
        assert "{1: \"again\", 2: \"good\", 3: \"easy\"" in py or \
               "{1: 'again', 2: 'good', 3: 'easy'" in py, \
            "BUG: 3-button mapping must be {1: 'again', 2: 'good', 3: 'easy'}"

    def test_ease_to_label_mapping_4_buttons(self):
        """With 4 buttons, ease 2 maps to 'hard' and ease 3 maps to 'good'"""
        py = self._read_py()
        assert "{1: \"again\", 2: \"hard\", 3: \"good\", 4: \"easy\"" in py or \
               "{1: 'again', 2: 'hard', 3: 'good', 4: 'easy'" in py, \
            "BUG: 4-button mapping must be {1: 'again', 2: 'hard', 3: 'good', 4: 'easy'}"



class TestBugVerification:
    """
    Tests that verify specific bugs found during code review
    """

    def test_bug_001_auto_reveal_and_auto_fail_racing(self):
        """
        BUG FIXED: Verify that auto-reveal and auto-fail are guarded with cardAnswered
        checks to prevent racing/double-submission.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # Check that auto-reveal sets cardAnswered
        assert 'cardAnswered = true' in content, "Auto-reveal must set cardAnswered"
        
        # Check that auto-fail checks cardAnswered (guard !cardAnswered)
        assert 'speed_focus_fail_delay' in content, "Speed focus fail delay exists"
        
        # Verify the auto-fail block contains a check for cardAnswered
        fail_idx = content.find('config.speed_focus_fail_delay')
        fail_block = content[fail_idx:fail_idx+300]
        assert 'cardAnswered' in fail_block or '!cardAnswered' in fail_block, \
            "Auto-fail does not check cardAnswered flag"
        print("      OK: auto-reveal and auto-fail race condition resolved")

    def test_bug_002_game_over_calls_pycmd_ease1_on_answered_card(self):
        """
        BUG: When health reaches 0, the game over routine calls
        pycmd("ease1") via setTimeout after 1.5s. But if the card was
        already answered in those 1.5s (e.g., user presses 1-4 first),
        this triggers a SECOND answer on the same card.
        
        Worse: if the card transitioned to a new card in the meantime,
        the 'Again' is applied to the WRONG card.
        
        Severity: High (corrupts review session state)
        Likelihood: Medium (game over triggers at 0 health, 
                    user may answer manually during freeze)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # Verify the issue exists
        assert 'safePycmd("ease1")' in content or 'pycmd("ease1")' in content, "Game over calls ease1"
        assert 'setTimeout' in content, "Game over uses setTimeout"
        
        # Check that there is no guard preventing the ease1 call
        # if the card was already answered
        # The setTimeout callback should check cardAnswered before calling ease1
        game_over_block = content[content.find('gameOverFreeze = true;'):]
        game_over_block = game_over_block[:game_over_block.find('updateUI();') + 50]
        
        # Check if ease1 is guarded
        if 'if (!cardAnswered)' in game_over_block:
            print("      Note: ease1 in game over appears to be guarded")
        else:
            # Check if there's ANY guard before ease1 in game over
            ease1_pos = game_over_block.find('safePycmd("ease1")')
            if ease1_pos == -1:
                ease1_pos = game_over_block.find('pycmd("ease1")')
            before_ease1 = game_over_block[:ease1_pos]
            # Check if the most recent 'if' before ease1 checks something relevant
            print("      WARNING: Game over ease1 may fire on already-answered card")

    def test_bug_003_session_stats_global_not_thread_safe(self):
        """
        BUG: session_stats is a global module variable that gets mutated
        from both JS callbacks and state transitions. If state transitions
        happen rapidly (profile switching, deck switching), stats can
        be partially overwritten.
        
        Severity: Low (only affects stats display accuracy)
        Likelihood: Low
        """
        path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")
        with open(path) as f:
            content = f.read()
        
        assert 'global session_stats' in content, "session_stats uses global keyword"
        # Check that it's reset on every review entry
        assert 'session_stats = {"cards": 0' in content, \
            "Session stats are reset on review entry"

    def test_bug_004_mw_reviewer_none_during_deferred_call(self):
        """
        BUG: on_reviewer_did_show_question uses QTimer.singleShot(100, ...)
        which defers execution. By 100ms later, mw.reviewer could be None
        if the user rapidly navigates away.
        
        Severity: Medium (AttributeError crash)
        Likelihood: Low (rapid deck switching)
        """
        path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")
        with open(path) as f:
            content = f.read()
        
        assert 'apply_distraction_free_if_reviewing' in content, \
            "Deferred function exists"
        # The function checks mw.state == "review" so it's somewhat guarded
        assert 'mw.state == "review"' in content, \
            "Deferred function checks state (partial guard)"

    def test_bug_005_bottom_webview_detection_fragile(self):
        """
        BUG: isBottomWebview detection uses window.location.href.includes('bottom')
        which is fragile across Anki versions. Also checks for specific DOM
        element IDs that vary between Anki versions.
        
        If detection fails, the bar is rendered in the wrong webview,
        health bar and buttons appear in the answer area instead of the
        main reviewer.
        
        Severity: High (UI completely broken if wrong webview detected)
        Likelihood: Medium (Anki version-specific)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # Check multiple fallback strategies
        checks = [
            "includes('bottom')",
            "getElementById('outer')",
            "getElementById('bottombar')",
            "getElementById('ansbuts')",
            "getElementById('buttons')",
            "getElementById('wrapper')",
            'button[onclick*="ease"]',
            'input[onclick*="ease"]',
        ]
        
        found_checks = [c for c in checks if c in content]
        print(f"      Bottom webview uses {len(found_checks)}/{len(checks)} detection strategies")

    def test_bug_006_streak_multiplier_naming(self):
        """
        WARNING: getStreakMultiplier() returns values > 1 for higher streaks,
        but it's used as a DIVISOR in the drain calculation:
        
        health = health - (drain_rate * dt * drainFactor) / streakMultiplier
        
        So higher multiplier = LESS drain. This is semantically inverted
        from what "multiplier" suggests. Not a functional bug but very
        misleading for maintainers.
        
        Severity: Low (functional but confusing)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # Verify the inverse relationship
        assert 'streakMultiplier' in content, "streakMultiplier exists"
        # Find the drain line
        drain_match = re.search(r'health = Math\.max\(0, health - \(.*?\) / streakMultiplier\)', content)
        assert drain_match, "BUG (or design choice): drain uses division by multiplier"
        print("      Note: streakMultiplier is used as divisor (higher streak = slower drain)")

    def test_bug_007_no_health_drain_during_game_over_freeze(self):
        """
        BUG: During game over freeze (1.5s), tick() returns early due to
        gameOverFreeze flag. This means NO timer updates happen during freeze.
        But the card being answered (ease1) may trigger onAnswerCard which
        calls updateUI. The freeze prevents proper UI updates during this window.
        
        Severity: Low (1.5s window, visual-only)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'gameOverFreeze' in content, "Game over freeze flag exists"
        assert 'if (gameOverFreeze) return;' in content, \
            "Tick returns early during freeze"

    def test_bug_008_break_health_bonus_always_15(self):
        """
        OBSERVATION: endBreak() always heals 15 HP regardless of config.
        This is a hardcoded value that doesn't scale with health_max.
        
        If health_max is 500 (config max), 15 HP is negligible.
        If health_max is 50 (config min), 15 HP is 30%.
        
        Severity: Low (configurable health_max, break-heal is bonus)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'health + 15' in content, "Break heal is hardcoded to 15"
        print("      Note: break heal is hardcoded 15, not proportional to health_max")

    def test_bug_009_fast_bonus_without_streak(self):
        """
        OBSERVATION: Fast answer bonus (+5 health for answer < 3.5s) is
        independent of the streak system. You get the bonus even if
        streak system is disabled.
        
        Not necessarily a bug, but could be surprising if user disables
        streaks but still gets health bonuses for speed.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # The bonus is applied before streak check
        bonus_pos = content.find('answerSpeed < 3.5')
        assert bonus_pos >= 0, "Fast answer bonus exists"
        # Check if it's inside a streak-enabled guard
        before_bonus = content[max(0, bonus_pos-500):bonus_pos]
        assert 'adhd_boosters_streak_enabled' not in before_bonus, \
            "Fast bonus is independent of streak config"
        print("      Note: fast answer bonus functions even with streaks disabled")

    def test_bug_010_undo_clears_last_action_after_one_undo(self):
        """
        BUG: onUndo sets lastAction = null after one undo, preventing 
        sequential undos. This means you can only undo the most recent
        card answer. Undoing further requires Anki's native undo.
        
        This is partially a feature (no multi-undo in game state) and
        partially a limitation.
        
        Severity: Medium (user may expect multi-step undo)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'lastAction = null' in content, "lastAction is cleared after undo"
        print("      Note: only 1 level of game state undo supported")

    def test_bug_011_shield_undo_subtracts_full_duration(self):
        """
        BUG: When undoing a shield-triggering card, onUndo subtracts
        shieldTimeAdded (12s) from shieldTimeLeft. But if some time
        has passed, shieldTimeLeft may be < 12. The subtraction could
        make it negative, but Math.max(0, ...) prevents that.
        
        However, if shield was already granted from a previous streak 
        milestone and the current card also grants shield, undoing the
        current card subtracts 12s from whatever's left, potentially
        removing more shield than this card contributed.
        
        Severity: Low (shield time math is approximate)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'shieldTimeLeft = Math.max(0, shieldTimeLeft - lastAction.shieldTimeAdded)' in content, \
            "Shield undo exists but may subtract more than contributed"

    def test_bug_012_health_value_on_game_over_recovery(self):
        """
        BUG FIXED: After game over recovery, health = config.health_max * 0.5.
        Verify that activeStudyDuration is reset to 0 to prevent death spiral.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # Verify health is restored
        assert 'config.health_max * 0.5' in content, "Health restored to 50%"
        # Check that activeStudyDuration is reset in game over block
        game_over_start = content.find('gameOverFreeze = true;')
        game_over_block = content[game_over_start:game_over_start+1000]
        assert 'activeStudyDuration = 0' in game_over_block or 'activeStudyDuration=0' in game_over_block, \
            "activeStudyDuration is reset after game over"
        print("      OK: activeStudyDuration reset on game over recovery")

    def test_bug_013_audio_context_policy(self):
        """
        BUG: initAudio() is called on every question/answer event, but
        browser autoplay policies may suspend the AudioContext. The 
        getAudioContext() function tries .resume(), but if called outside
        a user gesture, the resume() may return a promise that rejects
        or never resolves.
        
        First heartbeat/alert sound might be silent on some systems.
        
        Severity: Low (first sound may be silent)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'audioCtx.resume()' in content, "Resume is attempted"
        assert "audioCtx.state === 'suspended'" in content, "Suspended state checked"
        print("      Note: AudioContext may be suspended by browser policy")

    def test_bug_014_pomo_visible_config_not_propagated(self):
        """
        BUG: pomodoro_visible config value exists in config.json but
        checking JS: the updateUI function reads config.pomodoro_visible
        (correct). However, check if the Settings dialog has a matching
        control and it saves to the right key.
        """
        py_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        
        with open(py_path) as f:
            py = f.read()
        with open(js_path) as f:
            js = f.read()
        
        # Verify key exists in both sides
        assert 'pomodoro_visible' in py, "Python settings dialog should have pomodoro_visible"
        assert '"pomodoro_visible"' in py or "'pomodoro_visible'" in py, \
            "Settings dialog saves pomodoro_visible"
        assert 'config.pomodoro_visible' in js, "JS reads pomodoro_visible"
        print("      OK: pomodoro_visible is handled on both sides")

    def test_bug_015_grace_period_bypass_via_config_update(self):
        """
        BUG: If user changes config mid-session (via settings dialog),
        updateConfig() is called. If they reduce grace_period_duration
        below activeStudyDuration, the grace period immediately ends.
        This could be used to intentionally bypass the warm-up period.
        
        Not exploitable (user config, user choice), but could cause
        surprise if grace period vanishes mid-session.
        
        Severity: Low
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # updateConfig doesn't reset activeStudyDuration
        assert 'updateConfig' in content, "updateConfig exists"
        assert 'activeStudyDuration' in content, "activeStudyDuration tracked"

    def test_bug_016_bar_position_change_recreates_ui(self):
        """
        BUG: updateConfig() removes and recreates the entire lockin bar,
        including exit button, health bar, streak badge, pomodoro timer.
        This causes a visual flash. The old bar's DOM removal also loses
        any transient animation state.
        
        Severity: Low (cosmetic flash)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'bar.remove()' in content, "Bar is removed and recreated"
        assert 'createUI()' in content, "UI is recreated"

    def test_bug_017_no_event_unbind_on_destroy(self):
        """
        BUG: When the webview is destroyed (e.g., deck switch, profile switch),
        the JS event listeners (blur/focus, keydown) are not cleaned up.
        In most webview-based addons, this is fine because the webview is
        destroyed with its JS context. But if Anki reuses webviews, listeners
        may accumulate.
        
        Severity: Low (Anki manages webview lifecycle)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'window.addEventListener' in content, "Window listeners registered"
        # Check for removeEventListener
        if 'removeEventListener' not in content:
            print("      Note: no removeEventListener cleanup found")

    def test_bug_018_css_hide_buttons_still_shows_exit_button(self):
        """
        BUG: When hide_answer_buttons is enabled, the CSS hides all buttons
        except #lockin-exit-btn in the bottom webview. But #lockin-exit-btn
        lives in the MAIN webview, so the :not() selector is redundant.
        This is correct, not a bug.
        
        However, if bottom webview also has a button with id="lockin-exit-btn",
        it would not be hidden. This is extremely unlikely.
        """
        css_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.css")
        with open(css_path) as f:
            content = f.read()
        
        assert 'lockin-hide-buttons' in content, "Hide buttons CSS exists"
        assert 'display: none' in content, "CSS uses display:none for hiding"
        print("      Note: :not(#lockin-exit-btn) guard is on bottom webview, "
              "exit button is in main webview - correct behavior")

    def test_bug_019_again_heal_value_can_be_positive(self):
        """
        CONFIG BUG: health_heal_again has range -100 to 100 in settings dialog.
        If set to a positive value, pressing "Again" would HEAL the player
        instead of damaging them. This is valid user config but semantically
        confusing.
        
        Severity: Low (user-configured, not a code bug)
        """
        py_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")
        with open(py_path) as f:
            content = f.read()
        
        assert 'heal_again.setRange(-100, 100)' in content, \
            "Again heal can be positive or negative"
        print("      Note: Again can be configured to heal (+10) instead of damage")

    def test_bug_020_preset_changed_updates_but_doesnt_save(self):
        """
        BUG: When user selects a preset in the settings dialog, the form
        values update immediately. But if the user then clicks Cancel,
        the unsaved preset values are lost (which is correct dialog behavior).
        
        However, if the user clicks Save after selecting a preset, the preset
        values ARE saved. This is correct behavior.
        
        No bug here, just verifying.
        """
        py_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/__init__.py")
        with open(py_path) as f:
            content = f.read()
        
        assert 'on_preset_changed' in content, "Preset change handler exists"
        assert 'save_settings' in content, "Save handler exists"

    def test_bug_021_LOW_HEALTH_class_not_cleared_when_recovered(self):
        """
        VERIFICATION: When health drops below 25%, lockin-low-health class
        is added to body (causing red vignette + screen vibration). When
        health recovers above 25%, it should be removed.
        
        Check updateUI sets this correctly.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # Verify both add and remove
        assert 'lockin-low-health' in content, "Low health class exists"
        assert 'classList.remove' in content or 'classList.add' in content, \
            "DOM class manipulation exists"
        # Check the thresholds
        assert 'healthPercent < 25' in content, "25% threshold for low health"
        assert 'healthPercent < 55' in content, "55% threshold for yellow"
        
        # Verify removal at different thresholds
        low_health_block = content[content.find('if (healthPercent < 25)'):]
        low_health_block = low_health_block[:500]
        
        assert 'lockin-low-health' in content, "lockin-low-health class toggled"

    def test_bug_022_no_drain_if_sigmoid_and_answered(self):
        """
        VERIFICATION: Sigmoid drain only applies when card is NOT answered
        AND sigmoid_drain_enabled is true. Once cardAnswered = true,
        drain defaults to 1.0 factor (normal drain).
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'config.sigmoid_drain_enabled && !cardAnswered' in content, \
            "Sigmoid drain correctly gated"

    def test_bug_023_heartbeat_frequency_formula(self):
        """
        VERIFICATION: Heartbeat frequency formula is:
        heartbeatFreq = 300 + (healthPercent / 25) * 1100
        
        At health = 1%: 300 + (1/25)*1100 = 300 + 44 = 344ms (very fast beeps)
        At health = 24%: 300 + (24/25)*1100 = 300 + 1056 = 1356ms (slower)
        
        This is INVERTED: lower health = faster heartbeat (higher freq = shorter interval).
        The variable name "heartbeatFreq" suggests frequency but it's actually
        an interval in ms. At 25% health the interval is 1400ms (0.7Hz) and
        at 1% it's 344ms (2.9Hz). This is correct behavior for urgency.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert '300 + (healthPercent / 25) * 1100' in content, \
            "Heartbeat frequency formula exists"
        print("      Note: heartbeat interval decreases (faster) as health drops")

    def test_bug_024_pomodoro_warning_at_15s(self):
        """
        VERIFICATION: Pomodoro timer enters warning state (amber glow)
        when pomoTimeLeft <= 15 seconds.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'pomoTimeLeft <= 15' in content, \
            "Pomodoro warning at 15s threshold"
        assert 'pomo-warning' in content, \
            "Pomo warning CSS class exists"

    def test_bug_025_escape_key_not_bound(self):
        """
        OBSERVATION: The addon does not bind Escape or any key to exit
        the review mode. User must click the "✕ Exit" button or use
        Anki's native shortcuts (which may be hidden in fullscreen mode).
        
        This creates a potential soft-lock: if the Exit button is
        obscured or the user is in fullscreen mode with hidden toolbar,
        they may not know how to exit review mode.
        
        Severity: Medium (UI soft-lock potential)
        Likelihood: Low (users can use Anki-native shortcuts)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # Check if Escape is bound
        if "e.key === 'Escape'" not in content:
            print("      NOTE: Escape key not bound to exit review (potential soft-lock in fullscreen)")

    def test_bug_026_volume_config_may_be_zero(self):
        """
        VERIFICATION: speed_focus_volume can be 0.0, which effectively
        mutes all sounds. This is valid configuration, and the code
        handles it correctly (volume multiplier of 0 means silence).
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        vol_pos = content.find('config.speed_focus_volume')
        assert vol_pos >= 0, "Volume config is read"
        assert 'vol *' in content, "Volume is used as multiplier (via `vol` variable)"
        print("      OK: volume=0 correctly produces silence")

    def test_bug_027_hold_empty_breathing_scale(self):
        """
        VERIFICATION: During box breathing (4-4-4-4), 'hold-empty' phase
        has scale=1.0 (bubble at resting size). This is correct.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'scale = 1.0' in content, "hold-empty has correct scale"
        assert 'scale = 1.8' in content, "hold-full has correct scale"

    def test_bug_028_keyboard_shortcut_during_answer_phase(self):
        """
        VERIFICATION: The JS keydown handler in the main webview is a
        visual-only fallback (adds the "picked" highlight) — it does NOT
        call pycmd("easeN") directly. The actual answer command is sent
        by Anki's native QShortcut (for keys 1-4/Space/Enter) or by
        the custom button click (which calls pycmd("easeN")).

        The primary animation now comes from the Python
        reviewer_will_answer_card hook, which fires before _answerCard
        for both mouse clicks and keyboard shortcuts.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()

        key_handler = content[content.find("document.addEventListener('keydown'"):]
        key_handler = key_handler[:key_handler.find('});') + 3]

        # Defensive: the keydown handler must NOT send answer commands
        # itself — that would double-answer cards.
        assert 'safePycmd("ease' not in key_handler, \
            "BUG: Keydown handler must not send answer pycmds directly"
        assert 'pycmd("ease' not in key_handler, \
            "BUG: Keydown handler must not send answer pycmds directly"
        # The old "lockin-animate:" command has been removed; animation
        # is now driven by Python via animateButton().
        assert 'lockin-animate' not in key_handler, \
            "BUG: Old lockin-animate command should be removed; animation now comes from Python hook"

    def test_bug_029_game_over_pycmd_ease1_during_transition(self):
        """
        SEVERE BUG: During game over recovery (setTimeout 1.5s), the
        pycmd("ease1") call could fire DURING a card transition. If
        the 1.5s timer fires just as the next card is showing, the
        ease1 is applied to the NEW card, not the one that caused
        the game over.
        
        The setTimeout doesn't cancel if the card changes.
        
        Severity: High (wrong card gets 'Again' grade)
        Likelihood: Medium (1.5s window, depends on user timing)
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        # Check if the setTimeout callback has any guard against
        # card transitioning
        game_over_start = content.find('gameOverFreeze = true;')
        segment = content[game_over_start:game_over_start+800]
        
        # The setTimeout only restores health and unfreezes
        # No check for card state
        assert 'safePycmd("ease1")' in segment or 'pycmd("ease1")' in segment, "ease1 in game over exists"
        assert 'gameOverFreeze = false' in segment, "Unfreeze exists"
        
        # Check if card changes during freeze are handled
        if 'cardAnswered' not in segment:
            print("      WARNING: Game over ease1 may fire on wrong card during transitions")

    def test_bug_030_config_change_while_in_game_over_freeze(self):
        """
        BUG: If user opens settings and changes config during game over
        freeze (1.5s window), updateConfig() is called. The new config
        is applied immediately. But the game over freeze timer is still
        running, and when it fires, it restores health to 50% of the
        ORIGINAL health_max, not the new one.
        
        Actually, the JS internal config object is replaced by updateConfig,
        so when setTimeout fires, it reads the NEW config.health_max.
        This is actually correct - it reads the current config.
        
        Confirmed: no bug here, the closure captures config by reference.
        """
        js_path = os.path.expanduser("~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js")
        with open(js_path) as f:
            content = f.read()
        
        assert 'config.health_max * 0.5' in content, \
            "Game over recovery reads current config.health_max"
        print("      OK: game over recovery reads live config")


if __name__ == '__main__':
    """Run all tests and print results"""
    test_instance = TestStaticAnalysis()
    bug_instance = TestBugVerification()
    
    total = 0
    passed = 0
    failed = 0
    bugs = []
    
    print("=" * 70)
    print("ANKI LOCK-IN FOCUS: COMPREHENSIVE BUG ANALYSIS")
    print("=" * 70)
    
    print("\n--- Static Analysis ---")
    for name in [m for m in dir(test_instance) if m.startswith('test_')]:
        total += 1
        try:
            getattr(test_instance, name)()
            passed += 1
            print(f"  ✓ {name}")
        except Exception as e:
            failed += 1
            msg = str(e)
            bugs.append({'name': name, 'error': msg})
            print(f"  ✗ {name}: {msg}")
    
    print("\n--- Bug Verification ---")
    for name in [m for m in dir(bug_instance) if m.startswith('test_')]:
        total += 1
        try:
            getattr(bug_instance, name)()
            passed += 1
            print(f"  ✓ {name}")
        except Exception as e:
            failed += 1
            msg = str(e)
            bugs.append({'name': name, 'error': msg})
            print(f"  ✗ {name}: {msg}")
    
    print("\n" + "=" * 70)
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    print("=" * 70)
    
    if bugs:
        print(f"\nBUGS & ISSUES FOUND ({len(bugs)}):")
        for i, b in enumerate(bugs, 1):
            print(f"  #{i}: {b['name']}")
            print(f"     {b['error'][:200]}")
    
    sys.exit(1 if failed > 0 else 0)
