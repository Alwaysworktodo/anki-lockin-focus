"""Reviewer JavaScript, asset, and Python/JS integration invariants."""

from __future__ import annotations

import re
import struct
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_PATH = PROJECT_ROOT / "__init__.py"
JAVASCRIPT_PATH = PROJECT_ROOT / "web" / "lockin.js"
CSS_PATH = PROJECT_ROOT / "web" / "lockin.css"
CAT_DIRECTORY = PROJECT_ROOT / "web" / "cats"


def _javascript_source() -> str:
    return JAVASCRIPT_PATH.read_text(encoding="utf-8")


def _css_source() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def _without_js_comments(source: str) -> str:
    """Remove JavaScript comments while retaining strings and line structure."""
    output: list[str] = []
    index = 0
    quote: str | None = None

    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if quote is not None:
            output.append(char)
            if char == "\\" and index + 1 < len(source):
                index += 1
                output.append(source[index])
            elif char == quote:
                quote = None
            index += 1
            continue

        if char in {"'", '"', "`"}:
            quote = char
            output.append(char)
            index += 1
            continue

        if char == "/" and following == "/":
            output.extend("  ")
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue

        if char == "/" and following == "*":
            output.extend("  ")
            index += 2
            while index < len(source):
                if source[index : index + 2] == "*/":
                    output.extend("  ")
                    index += 2
                    break
                output.append("\n" if source[index] == "\n" else " ")
                index += 1
            continue

        output.append(char)
        index += 1

    return "".join(output)


def _balanced_block(source: str, opening_brace: int) -> str:
    """Return a brace-delimited JavaScript block, ignoring strings/comments."""
    assert source[opening_brace] == "{"
    depth = 0
    index = opening_brace
    quote: str | None = None
    line_comment = False
    block_comment = False

    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char in "\r\n":
                line_comment = False
            index += 1
            continue

        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue

        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue

        if char in {"'", '"', "`"}:
            quote = char
        elif char == "/" and following == "/":
            line_comment = True
            index += 2
            continue
        elif char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace : index + 1]
        index += 1

    raise AssertionError("unterminated JavaScript block")


def _function_block(name: str) -> str:
    source = _javascript_source()
    patterns = (
        rf"\bfunction\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        rf"\b{re.escape(name)}\s*:\s*function\s*\([^)]*\)\s*\{{",
    )
    match = next((match for pattern in patterns if (match := re.search(pattern, source))), None)
    assert match is not None, f"JavaScript function {name} is missing"
    opening_brace = source.find("{", match.start())
    return _balanced_block(source, opening_brace)


def _function_parameters(name: str) -> list[str]:
    source = _javascript_source()
    patterns = (
        rf"\bfunction\s+{re.escape(name)}\s*\((?P<parameters>[^)]*)\)\s*\{{",
        rf"\b{re.escape(name)}\s*:\s*function\s*\((?P<parameters>[^)]*)\)\s*\{{",
    )
    match = next((match for pattern in patterns if (match := re.search(pattern, source))), None)
    assert match is not None, f"JavaScript function {name} is missing"
    return [parameter.strip() for parameter in match.group("parameters").split(",") if parameter.strip()]


def test_injected_webview_kind_wins_over_dom_heuristics() -> None:
    block = _function_block("determineWebviewKind")
    marker = "if (window.AnkiLockInWebviewKind)"
    assert marker in block
    explicit_start = block.index(marker)
    fallback_start = block.find("else", explicit_start)
    assert fallback_start > explicit_start
    explicit_branch = block[explicit_start:fallback_start]

    for kind in ("bottom", "main", "deckBrowser", "overview"):
        assert f"window.AnkiLockInWebviewKind === '{kind}'" in explicit_branch
    assert "hasBottomElements" not in explicit_branch


def test_custom_buttons_use_native_answer_commands() -> None:
    block = _without_js_comments(_function_block("updateButtons"))
    native_command = re.compile(
        r"\b(?:safePycmd|pycmd)\s*\(\s*(['\"])ease\1\s*\+\s*easeKey\s*\)"
    )
    assert native_command.search(block), "custom buttons must follow Anki's native ease<N> path"
    assert "anki-lockin-answer" not in block


def test_missing_interval_never_disables_an_answer_button() -> None:
    block = _without_js_comments(_function_block("updateButtons"))

    interval_disables = (
        r"if\s*\([^)]*data\.interval[^)]*\)\s*\{[^}]*\.disabled\s*=\s*true",
        r"\.disabled\s*=\s*!+\s*data\.interval",
        r"aria-disabled[^\n;]*data\.interval",
    )
    assert not any(re.search(pattern, block, re.DOTALL) for pattern in interval_disables)

    if ".disabled" in block:
        assert "data.enabled" in block, (
            "only an explicit backend enabled=false value may disable an answer button"
        )


def test_answer_buttons_render_visible_labels_and_semantic_ratings() -> None:
    block = _without_js_comments(_function_block("updateButtons"))

    assert "lockin-ans-label" in block
    assert "data.label" in block
    assert "data-rating" in block
    assert "lockin-ans-interval" in block
    assert re.search(r"\bnormalizeRating\s*\(\s*data\s*,", block)

    rating_block = _without_js_comments(_function_block("normalizeRating"))
    assert "data.rating" in rating_block

    answer_block = _without_js_comments(_function_block("onAnswerCard"))
    for rating in ("again", "hard", "good", "easy"):
        assert re.search(rf"(['\"]){rating}\1", answer_block)


def test_no_timer_or_game_over_path_automatically_grades_again() -> None:
    source = _without_js_comments(_javascript_source())
    literal_again = re.compile(
        r"\b(?:safePycmd|pycmd)\s*\(\s*(['\"])ease1\1\s*\)"
    )
    assert not literal_again.search(source), (
        "timeouts and game-over recovery must never write an Again rating to Anki"
    )

    python_source = PYTHON_PATH.read_text(encoding="utf-8")
    assert not re.search(r"pycmd\s*\(\s*(['\"])ease1\1\s*\)", python_source)


def test_answer_shortcut_fallback_is_visual_only() -> None:
    source = _javascript_source()
    marker = "document.addEventListener('keydown'"
    assert marker in source
    opening_brace = source.find("{", source.index(marker))
    block = _without_js_comments(_balanced_block(source, opening_brace))

    assert not re.search(r"\b(?:safePycmd|pycmd)\s*\([^)]*ease", block)
    assert "lockin-ans-picked" in block


def test_collection_frontend_contract_is_exposed() -> None:
    source = _javascript_source()

    assert re.search(r"\bupdateCollection\s*:\s*function\s*\(", source)
    assert re.search(r"\bshowCatUnlock\s*:\s*function\s*\(", source)
    assert "lockin-cat-chip" in source
    assert "lockin-cat-toast-region" in source
    assert "prefers-reduced-motion" in source


def test_exactly_fifty_well_formed_local_cat_pngs() -> None:
    cat_paths = sorted(CAT_DIRECTORY.glob("*.png"))
    expected_names = [f"cat-{number:02d}.png" for number in range(1, 51)]

    assert [path.name for path in cat_paths] == expected_names
    assert len(cat_paths) == 50

    for path in cat_paths:
        data = path.read_bytes()
        assert len(data) > 32, f"{path.name} is unexpectedly small"
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} lacks a PNG signature"
        assert data[12:16] == b"IHDR", f"{path.name} lacks an IHDR chunk"
        width, height = struct.unpack(">II", data[16:24])
        assert width > 0 and height > 0, f"{path.name} has invalid dimensions"


def test_undo_snapshots_are_exact_counter_scoped_lifo_and_complete() -> None:
    source = _without_js_comments(_javascript_source())
    state_fields = (
        "health",
        "streak",
        "statsCards",
        "statsCorrect",
        "statsMaxStreak",
        "shieldTimeLeft",
        "statsShields",
    )

    map_declaration = re.search(
        r"\b(?:const|let)\s+(?P<name>[A-Za-z_$][\w$]*undo[\w$]*)"
        r"\s*=\s*new\s+Map\s*\(",
        source,
        re.IGNORECASE,
    )
    assert map_declaration, "answer snapshots need a counter-keyed Map"
    action_map = map_declaration.group("name")

    order_declarations = [
        match
        for match in re.finditer(
            r"\b(?:const|let)\s+(?P<name>[A-Za-z_$][\w$]*undo[\w$]*)\s*=\s*\[\s*\]",
            source,
            re.IGNORECASE,
        )
        if match.group("name") != action_map
    ]
    assert order_declarations, "answer snapshots need a separate LIFO counter order"
    undo_order = order_declarations[0].group("name")

    answer_parameters = _function_parameters("onAnswerCard")
    assert len(answer_parameters) >= 3
    answer_counter = answer_parameters[-1].split("=", 1)[0].strip()
    assert "counter" in answer_counter.lower()

    answer_block = _without_js_comments(_function_block("onAnswerCard"))
    assert re.search(rf"\b{re.escape(answer_counter)}\b", answer_block)

    snapshot_name = None
    snapshot_start = None
    for declaration in re.finditer(
        r"\b(?:const|let)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*\{",
        answer_block,
    ):
        object_block = _balanced_block(answer_block, answer_block.find("{", declaration.start()))
        if all(re.search(rf"\b{re.escape(field)}\b", object_block) for field in state_fields):
            snapshot_name = declaration.group("name")
            snapshot_start = declaration.start()
            break
    assert snapshot_name is not None and snapshot_start is not None, (
        "onAnswerCard must capture every mutable answer field before changing it"
    )

    first_mutation = min(
        match.start()
        for field in state_fields
        if (
            match := re.search(
                rf"\b{re.escape(field)}\s*(?:\+\+|--|[+*/-]?=(?!=))",
                answer_block,
            )
        )
    )
    assert snapshot_start < first_mutation

    store_call = re.search(
        rf"\b(?P<helper>[A-Za-z_$][\w$]*)\s*\(\s*{re.escape(answer_counter)}\s*,"
        rf"\s*{re.escape(snapshot_name)}\s*\)",
        answer_block,
    )
    assert store_call, "onAnswerCard must store its counter and pre-answer snapshot"
    store_helper = store_call.group("helper")
    store_parameters = _function_parameters(store_helper)
    assert len(store_parameters) >= 2
    store_counter, store_snapshot = store_parameters[:2]
    store_block = _without_js_comments(_function_block(store_helper))

    map_store = re.search(
        rf"\b{re.escape(action_map)}\.set\s*\((?P<arguments>[^)]*)\)",
        store_block,
    )
    assert map_store
    for argument in (store_counter, store_snapshot):
        assert re.search(rf"\b{re.escape(argument)}\b", map_store.group("arguments"))
    assert re.search(
        rf"\b{re.escape(undo_order)}\.push\s*\(\s*{re.escape(store_counter)}\s*\)",
        store_block,
    )

    undo_parameters = _function_parameters("onUndo")
    assert undo_parameters
    undo_counter = undo_parameters[0].split("=", 1)[0].strip()
    assert "counter" in undo_counter.lower()

    undo_block = _without_js_comments(_function_block("onUndo"))
    take_call = re.search(
        rf"\b(?:const|let)\s+(?P<snapshot>[A-Za-z_$][\w$]*)\s*=\s*"
        rf"(?P<helper>[A-Za-z_$][\w$]*)\s*\(\s*{re.escape(undo_counter)}\s*\)",
        undo_block,
    )
    assert take_call, "onUndo must retrieve state using the supplied exact counter"
    restored_snapshot = take_call.group("snapshot")
    take_helper = take_call.group("helper")
    take_parameters = _function_parameters(take_helper)
    assert take_parameters
    take_counter = take_parameters[0]
    take_block = _without_js_comments(_function_block(take_helper))

    snapshot_load = re.search(
        rf"\b(?:const|let)\s+(?P<snapshot>[A-Za-z_$][\w$]*)\s*=\s*"
        rf"{re.escape(action_map)}\.get\s*\(\s*{re.escape(take_counter)}\s*\)",
        take_block,
    )
    assert snapshot_load, "the undo helper must load the exact counter snapshot"

    guard = take_block[: snapshot_load.start()]
    lifo_read = (
        re.search(
            rf"\b{re.escape(undo_order)}\s*\[\s*{re.escape(undo_order)}\.length\s*-\s*1\s*\]",
            guard,
        )
        or re.search(rf"\b{re.escape(undo_order)}\.at\s*\(\s*-1\s*\)", guard)
    )
    assert lifo_read, "onUndo must compare against the newest answer counter"
    assert re.search(rf"\b{re.escape(take_counter)}\b", guard)
    assert re.search(
        rf"\b{re.escape(action_map)}\.has\s*\(\s*{re.escape(take_counter)}\s*\)",
        guard,
    )
    counter_mismatch_guard = re.search(r"!==|!=|!\s*Object\.is\s*\(", guard)
    assert "return" in guard and counter_mismatch_guard, (
        "missing, unrelated, or out-of-order counters must return before state changes"
    )

    for field in state_fields:
        assert re.search(
            rf"\b{re.escape(field)}\s*=\s*{re.escape(restored_snapshot)}\s*\.\s*{re.escape(field)}\b",
            undo_block,
        ), f"onUndo does not restore {field} from the pre-answer snapshot"

    assert re.search(
        rf"\b{re.escape(action_map)}\.delete\s*\(\s*{re.escape(take_counter)}\s*\)",
        take_block,
    )
    assert re.search(rf"\b{re.escape(undo_order)}\.pop\s*\(\s*\)", take_block)

    reset_block = _without_js_comments(_function_block("resetSession"))
    clear_helper = None
    clear_block = None
    for declaration in re.finditer(
        r"\bfunction\s+(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
        source,
    ):
        candidate = declaration.group("name")
        candidate_block = _without_js_comments(_function_block(candidate))
        if re.search(rf"\b{re.escape(action_map)}\.clear\s*\(\s*\)", candidate_block):
            clear_helper = candidate
            clear_block = candidate_block
            break
    assert clear_helper is not None and clear_block is not None
    assert re.search(rf"\b{re.escape(clear_helper)}\s*\(\s*\)", reset_block)
    clears_order = (
        re.search(rf"\b{re.escape(undo_order)}\.length\s*=\s*0\b", clear_block)
        or re.search(rf"\b{re.escape(undo_order)}\.splice\s*\(\s*0", clear_block)
    )
    assert clears_order, "resetSession must discard the pending undo order"


def test_avatar_and_toast_fallbacks_and_hidden_css_rules() -> None:
    css = _css_source()
    assert "#lockin-cat-avatar[hidden]" in css
    assert ".lockin-cat-avatar-fallback[hidden]" in css
    assert ".lockin-cat-toast-thumb img[hidden]" in css
    assert ".lockin-cat-toast-thumb-fallback[hidden]" in css
    assert "display: none !important;" in css

    js = _without_js_comments(_function_block("setCatImage"))
    assert "img.onload" in js
    assert "img.onerror" in js
    assert "img.hidden = false" in js
    assert "fallback.hidden = true" in js
    assert "img.hidden = true" in js
    assert "fallback.hidden = false" in js


def test_disabling_collectibles_hides_chip_and_clears_notifications() -> None:
    ui_block = _without_js_comments(_function_block("updateCollectionUI"))
    assert "config.collectibles_enabled" in ui_block
    assert "chip.hidden = !enabled" in ui_block
    assert "dismissUnlockToast" in ui_block
    assert "unlockToastQueue.length = 0" in ui_block

    toast_block = _without_js_comments(_function_block("renderNextUnlockToast"))
    assert "config.collectibles_enabled" in toast_block


def test_mouse_only_reviewing_and_custom_show_answer_button() -> None:
    controls_block = _without_js_comments(_function_block("renderQuestionControls"))
    assert "lockin-show-ans-btn" in controls_block
    assert re.search(r"safePycmd\s*\(\s*['\"]ans['\"]\s*\)", controls_block)

    show_q_block = _without_js_comments(_function_block("onShowQuestion"))
    assert "renderQuestionControls" in show_q_block


def test_settings_rebuild_preserves_buttons_intervals_and_question_controls() -> None:
    config_block = _without_js_comments(_function_block("updateConfig"))
    assert "renderQuestionControls" in config_block
    assert "updateButtons" in config_block
    assert "latestButtonsPayload" in config_block
    assert "setAnswerButtonsVisible" in config_block


def test_pomodoro_reviewer_controls_and_break_overlay_derivation() -> None:
    create_ui_block = _without_js_comments(_function_block("createUI"))
    assert "lockin-pomo-pause-btn" in create_ui_block
    assert "lockin-pomo-resume-btn" in create_ui_block
    assert "lockin-pomo-stop-btn" in create_ui_block
    assert "pausePomodoro" in create_ui_block
    assert "resumePomodoro" in create_ui_block
    assert "stopPomodoro" in create_ui_block

    update_ui_block = _without_js_comments(_function_block("updateUI"))
    assert "pomoState === 'break'" in update_ui_block
    assert "pomoPhaseBeforePause === 'break'" in update_ui_block
    assert "overlay.classList.add('overlay-open')" in update_ui_block


def test_autopause_active_time_measurement_and_sixty_seconds_unfocused() -> None:
    tick_block = _without_js_comments(_function_block("tick"))
    assert "reviewPhase === 'question'" in tick_block
    assert "focusStateActive" in tick_block
    assert "activeQuestionTime += dt" in tick_block
    assert "activeQuestionTime" in tick_block
    assert "streak_reset_idle_time" in tick_block


def test_unlock_notification_view_opens_selected_cat_and_acks_delivery() -> None:
    toast_block = _without_js_comments(_function_block("renderNextUnlockToast"))
    assert "lockin-open-cat-collection:" in toast_block
    assert "lockin-unlock-delivered:" in toast_block
    assert "scheduleUnlockDismiss" in toast_block
    assert "mouseenter" in toast_block
    assert "mouseleave" in toast_block
    assert "focusin" in toast_block
    assert "focusout" in toast_block

    dismiss_block = _without_js_comments(_function_block("scheduleUnlockDismiss"))
    assert "8000" in dismiss_block
