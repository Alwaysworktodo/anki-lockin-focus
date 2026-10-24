"""Source-level compatibility tests for the Anki Lock-In Python backend.

These tests intentionally inspect the checkout under test.  They do not import
the add-on (which requires Anki's ``aqt`` runtime), and they never read an
installed copy from a user's Anki data directory.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_PATH = PROJECT_ROOT / "__init__.py"
CONFIG_PATH = PROJECT_ROOT / "config.json"
MANIFEST_PATH = PROJECT_ROOT / "manifest.json"
INTEGRATION_TEST_PATH = PROJECT_ROOT / "tests" / "test_integration.py"


def _python_source() -> str:
    return PYTHON_PATH.read_text(encoding="utf-8")


def _python_tree() -> ast.Module:
    return ast.parse(_python_source(), filename=str(PYTHON_PATH))


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _registered_callback(tree: ast.Module, hook_name: str) -> ast.FunctionDef:
    callback_name: str | None = None
    expected_target = f"gui_hooks.{hook_name}.append"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _dotted_name(node.func) != expected_target:
            continue
        if node.args and isinstance(node.args[0], ast.Name):
            callback_name = node.args[0].id
            break

    assert callback_name, f"{hook_name} must be registered with a named callback"
    callbacks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == callback_name
    ]
    assert len(callbacks) == 1, f"could not resolve callback for {hook_name}"
    callback = callbacks[0]
    assert isinstance(callback, ast.FunctionDef)
    return callback


def _called_attributes(node: ast.AST) -> set[str]:
    return {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }


def _strict_json(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r} in {path.name}")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> Any:
        raise ValueError(f"non-finite JSON value {value!r} in {path.name}")

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
    )
    assert isinstance(value, dict), f"{path.name} must contain a JSON object"
    return value


def _module_int_constants(tree: ast.Module) -> dict[str, int]:
    constants: dict[str, int] = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, int):
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value
    return constants


def _resolved_int(node: ast.AST, constants: dict[str, int]) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def test_source_parses_and_registers_supported_reviewer_hooks() -> None:
    tree = _python_tree()
    registered = {
        dotted
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (dotted := _dotted_name(node.func))
        and dotted.startswith("gui_hooks.")
        and dotted.endswith(".append")
    }

    required = {
        "gui_hooks.reviewer_will_init_answer_buttons.append",
        "gui_hooks.reviewer_will_answer_card.append",
        "gui_hooks.reviewer_did_answer_card.append",
        "gui_hooks.state_did_undo.append",
        "gui_hooks.webview_will_set_content.append",
        "gui_hooks.webview_did_receive_js_message.append",
    }
    assert required <= registered, f"missing hook registrations: {sorted(required - registered)}"


def test_answer_button_data_uses_public_scheduler_apis() -> None:
    tree = _python_tree()
    callback = _registered_callback(tree, "reviewer_will_init_answer_buttons")
    calls = _called_attributes(callback)

    assert "get_scheduling_states" in calls
    assert "describe_next_states" in calls

    first_argument = callback.args.args[0].arg
    assert any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Name)
        and node.value.id == first_argument
        for node in ast.walk(callback)
    ), "the answer-button filter must return Anki's button tuple unchanged"


def test_private_and_legacy_interval_helpers_are_not_used() -> None:
    attributes = {
        node.attr for node in ast.walk(_python_tree()) if isinstance(node, ast.Attribute)
    }
    forbidden = {"_buttonTime", "nextIvlStr"}
    assert not (attributes & forbidden), (
        "custom buttons must use scheduling states, not private/legacy interval helpers"
    )


def test_answer_payload_contains_localized_label_interval_and_semantic_rating() -> None:
    tree = _python_tree()
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert {"label", "interval", "rating"} <= string_literals
    assert {"again", "hard", "good", "easy"} <= string_literals


def test_confirmed_answers_and_undo_are_tied_to_anki_undo_counters() -> None:
    tree = _python_tree()
    answer_callback = _registered_callback(tree, "reviewer_did_answer_card")
    undo_callback = _registered_callback(tree, "state_did_undo")

    answer_attributes = {
        node.attr for node in ast.walk(answer_callback) if isinstance(node, ast.Attribute)
    }
    assert "undo_status" in answer_attributes
    assert "last_step" in answer_attributes

    undo_strings = {
        node.value
        for node in ast.walk(undo_callback)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    undo_names = {
        node.id for node in ast.walk(undo_callback) if isinstance(node, ast.Name)
    }
    undo_calls = _called_attributes(undo_callback)
    assert "counter" in undo_strings
    assert "_answer_undo_counters" in undo_names
    assert "pop" not in undo_calls, "answer identities must survive repeated undo/redo"
    assert "_pending_redo_counters" in undo_names


def test_bridge_payloads_are_bounded_and_validated_when_present() -> None:
    tree = _python_tree()
    handler = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "on_js_message"
        ),
        None,
    )
    assert handler is not None, "the JavaScript bridge handler is missing"

    payload_prefixes = [
        node.args[0].value
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "startswith"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]
    if not payload_prefixes:
        return

    local_functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reachable: list[ast.AST] = [handler]
    queued = [handler]
    seen = {handler.name}
    while queued:
        current = queued.pop()
        for call in ast.walk(current):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            helper = local_functions.get(call.func.id)
            if helper is not None and helper.name not in seen:
                seen.add(helper.name)
                reachable.append(helper)
                queued.append(helper)

    constants = _module_int_constants(tree)
    bounds: list[int] = []
    has_message_type_check = False
    has_mapping_validation = False

    for node in (child for scope in reachable for child in ast.walk(scope)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "isinstance" and len(node.args) >= 2:
                expected_type = _dotted_name(node.args[1])
                if expected_type == "str":
                    has_message_type_check = True
                if expected_type in {"dict", "Mapping", "collections.abc.Mapping"}:
                    has_mapping_validation = True

        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        has_message_len = any(
            isinstance(descendant, ast.Call)
            and isinstance(descendant.func, ast.Name)
            and descendant.func.id == "len"
            for operand in operands
            for descendant in ast.walk(operand)
        )
        if has_message_len:
            bounds.extend(
                value
                for operand in operands
                if (value := _resolved_int(operand, constants)) is not None
            )

    assert has_message_type_check
    assert any(256 <= bound <= 262_144 for bound in bounds), (
        f"payload bridges {payload_prefixes} need a finite message-size limit"
    )
    assert has_mapping_validation, "JSON bridge payloads must be mappings after decoding"


def test_web_export_pattern_exposes_only_required_asset_types() -> None:
    tree = _python_tree()
    export_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setWebExports"
    ]
    assert len(export_calls) == 1
    call = export_calls[0]
    assert len(call.args) >= 2 and isinstance(call.args[1], ast.Constant)
    pattern = call.args[1].value
    assert isinstance(pattern, str)

    compiled = re.compile(pattern)
    for asset in (
        "web/lockin.css",
        "web/lockin.js",
        "web/cats/cat-01.png",
    ):
        assert compiled.fullmatch(asset), f"web export pattern does not expose {asset}"


def test_config_is_strict_json_with_safe_localized_defaults() -> None:
    config = _strict_json(CONFIG_PATH)

    assert config.get("ui_language") in {"de", "en"}
    assert isinstance(config.get("collectibles_enabled"), bool)
    assert isinstance(config.get("custom_answer_buttons"), bool)
    assert isinstance(config.get("health_max"), (int, float)) and config["health_max"] > 0

    for key in (
        "health_drain_rate",
        "pomodoro_study_duration",
        "pomodoro_break_duration",
        "speed_focus_alert_delay",
        "speed_focus_reveal_delay",
        "speed_focus_fail_delay",
        "grace_period_duration",
        "streak_reset_idle_time",
        "streak_speed_threshold",
    ):
        assert isinstance(config.get(key), (int, float)), f"{key} must be numeric"
        assert config[key] >= 0, f"{key} must not be negative"


def test_manifest_targets_current_stable_anki_schema() -> None:
    manifest = _strict_json(MANIFEST_PATH)

    assert isinstance(manifest.get("name"), str) and manifest["name"].strip()
    assert isinstance(manifest.get("package"), str) and manifest["package"].strip()
    assert not ({"/", "\\"} & set(manifest["package"]))
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", manifest.get("human_version", ""))
    assert manifest.get("min_point_version") == 260800
    assert isinstance(manifest.get("max_point_version"), int)
    assert manifest["max_point_version"] >= manifest["min_point_version"]
    assert "branch_index" not in manifest, "branch_index is assigned by AnkiWeb"
    assert not ({"anki_version", "qtmajor"} & manifest.keys())


def test_retry_queue_is_bounded_and_drain_checks_undo() -> None:
    tree = _python_tree()
    constants = _module_int_constants(tree)
    assert "_MAX_RETRY_QUEUE_SIZE" in constants
    assert 10 <= constants["_MAX_RETRY_QUEUE_SIZE"] <= 1000

    drain_fn = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_drain_retry_queue"),
        None,
    )
    assert drain_fn is not None, "_drain_retry_queue function is required"
    strings = {
        node.value
        for node in ast.walk(drain_fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "desired_status" in strings


def test_pomodoro_progression_freezes_outside_review_and_operation_hooked() -> None:
    tree = _python_tree()
    content_callback = _registered_callback(tree, "webview_will_set_content")
    content_strings = {
        node.value
        for node in ast.walk(content_callback)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "review" in content_strings
    assert "last_sync_timestamp" in content_strings

    op_callback = _registered_callback(tree, "operation_did_execute")
    assert op_callback is not None
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and "reconcile" in node.func.id
        for node in ast.walk(op_callback)
    )


def test_profile_switching_and_summary_renders_pending_rewards() -> None:
    tree = _python_tree()
    profile_open = _registered_callback(tree, "profile_did_open")
    profile_calls = _called_attributes(profile_open)
    assert "clear" in profile_calls

    dialog_class = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SessionStatsDialog"),
        None,
    )
    assert dialog_class is not None
    init_fn = next(
        (node for node in dialog_class.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"),
        None,
    )
    assert init_fn is not None
    arg_names = [arg.arg for arg in init_fn.args.args]
    assert "pending_rewards" in arg_names


def test_rewritten_tests_are_checkout_relative_and_have_no_empty_tests() -> None:
    forbidden_fragments = (
        "addons" + "21",
        ".local" + "/share/Anki2",
        "expand" + "user(",
    )

    for path in (Path(__file__).resolve(), INTEGRATION_TEST_PATH):
        source = path.read_text(encoding="utf-8")
        assert not any(fragment in source for fragment in forbidden_fragments)

        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            executable = [
                statement
                for statement in node.body
                if not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                )
            ]
            assert executable and not all(isinstance(statement, ast.Pass) for statement in executable), (
                f"{path.name}:{node.lineno} is a pass-only test"
            )
