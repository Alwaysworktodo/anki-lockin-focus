"""Source-relative tests for the Python-authoritative collectible system."""

from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import time

import pytest


ADDON_ROOT = Path(__file__).resolve().parents[1]
if str(ADDON_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDON_ROOT))

from collectibles import (  # noqa: E402
    DAILY_GOAL,
    EXPECTED_THRESHOLDS,
    CollectiblesDialog,
    CollectiblesStore,
    StateError,
    default_state_path,
    load_catalog,
    qt_available,
)


def make_store(tmp_path: Path, profile: str = "User 1") -> CollectiblesStore:
    return CollectiblesStore(
        tmp_path / "user_files" / "collectibles.sqlite3",
        profile_name=profile,
    )


def add_passes(
    store: CollectiblesStore,
    count: int,
    *,
    day: date,
    session: str = "session-a",
    start: int = 0,
    deck_id: int = 1,
) -> None:
    for offset in range(start, start + count):
        store.record_answer(
            event_id=f"{session}-{day}-{offset}",
            revlog_id=1_000_000 + offset + (day.toordinal() * 1000),
            card_id=offset,
            rating="good",
            session_id=session,
            local_day=day,
            deck_id=deck_id,
        )


def test_catalog_has_exact_50_bilingual_png_entries_and_thresholds() -> None:
    catalog = load_catalog()
    assert len(catalog.entries) == 50
    assert catalog.daily_goal == DAILY_GOAL == 20
    assert [entry.sort_order for entry in catalog.entries] == list(range(1, 51))
    assert len({entry.id for entry in catalog.entries}) == 50

    by_metric: dict[str, list[int]] = {metric: [] for metric in EXPECTED_THRESHOLDS}
    for entry in catalog.entries:
        assert entry.name["de"] and entry.name["en"]
        assert entry.description["de"] and entry.description["en"]
        assert entry.alt["de"] and entry.alt["en"]
        assert (ADDON_ROOT / entry.asset).is_file()
        assert entry.credit_id in catalog.credits
        by_metric[entry.metric].append(entry.threshold)

    assert {metric: tuple(values) for metric, values in by_metric.items()} == EXPECTED_THRESHOLDS
    assert {entry.asset for entry in catalog.entries} == {
        f"web/cats/cat-{number:02d}.png" for number in range(1, 51)
    }
    expected_assets = {
        "resolved_cards_total": [*range(1, 11), *range(31, 37)],
        "session_resolved_max": list(range(37, 49)),
        "recoveries_total": list(range(21, 29)),
        "daily_goal_streak_max": list(range(11, 19)),
        "qualified_days_total": [19, 20, 29, 30, 49, 50],
    }
    assert {
        metric: [int(entry.asset[-6:-4]) for entry in catalog.entries if entry.metric == metric]
        for metric in expected_assets
    } == expected_assets
    assert all(
        credit.get("creator") and credit.get("source") and credit.get("license")
        for credit in catalog.credits.values()
    )
    names_en = {entry.name["en"].lower() for entry in catalog.entries}
    assert any("smudge" in name for name in names_en)
    assert any("popcat" in name for name in names_en)
    assert any("maxwell" in name for name in names_en)
    assert any("crying" in name for name in names_en)
    assert any("polite" in name for name in names_en)
    assert any("huh" in name for name in names_en)


def test_state_uses_versioned_profiles_mapping_and_default_fallback(tmp_path: Path) -> None:
    path = tmp_path / "user_files" / "collectibles.sqlite3"
    store = CollectiblesStore(path, profile_name="")
    store.begin_session("s")

    raw = store.raw_state()
    assert raw["schema_version"] == 1
    assert set(raw["profiles"]) == {"default"}
    assert raw["profiles"]["default"]["current_session_id"] == "s"
    assert default_state_path(tmp_path).name == "collectibles.sqlite3"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_duplicate_event_and_revlog_ids_are_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.record_answer(
        event_id="answer-1", revlog_id=101, card_id=1, rating=3, session_id="s"
    )
    duplicate_revlog = store.record_answer(
        event_id="answer-2", revlog_id=101, card_id=2, rating=3, session_id="s"
    )
    duplicate_event = store.record_answer(
        event_id="answer-1", revlog_id=102, card_id=3, rating=3, session_id="s"
    )

    assert first["changed"] is True
    assert duplicate_revlog["duplicate"] is True
    assert duplicate_event["duplicate"] is True
    assert store.snapshot()["metrics"]["resolved_cards_total"] == 1
    assert len(store.raw_state()["profiles"]["User 1"]["events"]) == 1


def test_pass_counts_once_per_card_day_and_once_per_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    today = date.today()
    for index, rating in enumerate(("hard", "good", "easy"), start=1):
        store.record_answer(
            event_id=f"e{index}",
            card_id=44,
            rating=rating,
            session_id="same-session",
            local_day=today,
        )

    metrics = store.snapshot(today=today)["metrics"]
    assert metrics["resolved_cards_total"] == 1
    assert metrics["session_resolved_current"] == 1
    assert metrics["session_resolved_max"] == 1

    store.record_answer(
        event_id="tomorrow",
        card_id=44,
        rating="hard",
        session_id="next-session",
        local_day=today + timedelta(days=1),
    )
    metrics = store.snapshot(today=today)["metrics"]
    assert metrics["resolved_cards_total"] == 2
    assert metrics["session_resolved_current"] == 1


def test_again_then_later_pass_awards_one_recovery_without_rating_bonus(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    today = date.today()
    store.record_answer(
        event_id="again", card_id=1, rating="again", session_id="s", local_day=today
    )
    assert store.snapshot(today=today)["metrics"]["recoveries_total"] == 0

    store.record_answer(
        event_id="hard", card_id=1, rating="hard", session_id="s", local_day=today
    )
    store.record_answer(
        event_id="easy", card_id=1, rating="easy", session_id="s", local_day=today
    )
    metrics = store.snapshot(today=today)["metrics"]
    assert metrics["recoveries_total"] == 1
    assert metrics["resolved_cards_total"] == 1


def test_pass_before_again_needs_a_later_pass_for_recovery(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    today = date.today()
    store.record_answer(
        event_id="pass-first", card_id=9, rating=3, session_id="s", local_day=today
    )
    store.record_answer(
        event_id="again-later", card_id=9, rating=1, session_id="s", local_day=today
    )
    assert store.snapshot(today=today)["metrics"]["recoveries_total"] == 0
    store.record_answer(
        event_id="pass-after", card_id=9, rating=2, session_id="s", local_day=today
    )
    assert store.snapshot(today=today)["metrics"]["recoveries_total"] == 1


def test_session_current_and_max_reset_only_at_session_boundary(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    today = date.today()
    store.begin_session("first")
    add_passes(store, 4, day=today, session="first")
    assert store.snapshot(today=today)["metrics"]["session_resolved_current"] == 4

    store.end_session("first")
    metrics = store.snapshot(today=today)["metrics"]
    assert metrics["session_resolved_current"] == 0
    assert metrics["session_resolved_max"] == 4

    store.begin_session("second")
    add_passes(store, 2, day=today, session="second", start=100)
    metrics = store.snapshot(today=today)["metrics"]
    assert metrics["session_resolved_current"] == 2
    assert metrics["session_resolved_max"] == 4


def test_daily_goal_and_calendar_streak_metrics(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    today = date.today()
    for index, day in enumerate((today - timedelta(days=2), today - timedelta(days=1), today)):
        add_passes(store, DAILY_GOAL, day=day, session=f"day-{index}", start=index * 100)

    metrics = store.snapshot(today=today)["metrics"]
    assert metrics["resolved_cards_total"] == DAILY_GOAL * 3
    assert metrics["qualified_days_total"] == 3
    assert metrics["daily_goal_streak_current"] == 3
    assert metrics["daily_goal_streak_max"] == 3


def test_yesterdays_streak_remains_current_until_today_is_over(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    today = date.today()
    add_passes(store, DAILY_GOAL, day=today - timedelta(days=1), session="yesterday")
    metrics = store.snapshot(today=today)["metrics"]
    assert metrics["daily_goal_streak_current"] == 1


def test_all_decks_count_without_filtering(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    today = date.today()
    store.record_answer(
        event_id="deck-a", card_id=1, rating=3, session_id="s", local_day=today, deck_id=1
    )
    store.record_answer(
        event_id="deck-b", card_id=2, rating=3, session_id="s", local_day=today, deck_id=999
    )
    assert store.snapshot(today=today)["metrics"]["resolved_cards_total"] == 2


def test_unlock_thresholds_and_undo_never_relock_owned_cat(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    result = store.record_answer(
        event_id="first",
        revlog_id=500,
        card_id=1,
        rating="good",
        session_id="s",
        undo_counter=77,
    )
    first_cat = "cat.resolved_cards_total.00001"
    assert first_cat in result["new_unlock_ids"]
    before = store.snapshot()
    assert before["unlocked_count"] == 1
    assert before["equipped"]["id"] == first_cat

    undone = store.undo(77)
    after = store.snapshot()
    assert undone["changed"] is True
    assert after["metrics"]["resolved_cards_total"] == 0
    assert after["unlocked_count"] == 1
    assert after["equipped"]["id"] == first_cat
    assert store.undo(999)["changed"] is False

    redone = store.redo(77)
    restored = store.snapshot()
    assert redone["changed"] is True
    assert restored["metrics"]["resolved_cards_total"] == 1
    assert restored["unlocked_count"] == 1
    assert redone["new_unlock_ids"] == []
    assert store.redo(999)["changed"] is False


def test_undo_counter_is_scoped_to_current_anki_runtime(tmp_path: Path) -> None:
    path = tmp_path / "user_files" / "collectibles.sqlite3"
    old_runtime = CollectiblesStore(path, profile_name="User", runtime_id="old")
    old_runtime.record_answer(
        event_id="old-answer", card_id=1, rating=3, session_id="old", undo_counter=1
    )

    new_runtime = CollectiblesStore(path, profile_name="User", runtime_id="new")
    new_runtime.record_answer(
        event_id="new-answer", card_id=2, rating=3, session_id="new", undo_counter=1
    )
    result = new_runtime.undo(1)

    assert result["reversed_event_keys"] == ["event:new-answer"]
    assert new_runtime.snapshot()["metrics"]["resolved_cards_total"] == 1
    events = new_runtime.raw_state()["profiles"]["User"]["events"]
    assert events["event:old-answer"]["active"] is True


def test_profile_progress_is_isolated_in_shared_file(tmp_path: Path) -> None:
    path = tmp_path / "user_files" / "collectibles.sqlite3"
    first = CollectiblesStore(path, profile_name="Alice")
    first.record_answer(event_id="a", card_id=1, rating=3, session_id="s")

    second = CollectiblesStore(path, profile_name="Bob")
    assert second.snapshot()["metrics"]["resolved_cards_total"] == 0
    second.record_answer(event_id="b", card_id=2, rating=3, session_id="s")

    reloaded = CollectiblesStore(path, profile_name="Alice")
    assert reloaded.snapshot()["metrics"]["resolved_cards_total"] == 1
    assert set(reloaded.raw_state()["profiles"]) == {"Alice", "Bob"}


def test_snapshot_localization_assets_equip_seen_and_json_serializability(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    result = store.record_answer(event_id="one", card_id=1, rating=3, session_id="s")
    cat_id = result["new_unlock_ids"][0]

    german = store.snapshot("de", "/_addons/anki-lockin-focus")
    english = store.snapshot("en", "/_addons/anki-lockin-focus/")
    assert german["locale"] == "de"
    assert english["locale"] == "en"
    assert german["equipped"]["localized_name"] != english["equipped"]["localized_name"]
    assert german["equipped"]["asset_url"].endswith("/web/cats/cat-01.png")
    assert german["unseen_count"] == 1
    assert german["next_milestone"]["current"] >= 0
    json.dumps(german, ensure_ascii=False, allow_nan=False)

    seen = store.mark_seen([cat_id])
    assert seen["changed"] is True
    assert store.snapshot()["unseen_count"] == 0
    assert store.equip(None)["equipped_cat_id"] is None
    with pytest.raises(ValueError):
        store.equip("cat.resolved_cards_total.00005")


def test_unlock_event_payload_is_localized_and_bounded_to_requested_ids(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    result = store.record_answer(event_id="one", card_id=1, rating=3, session_id="s")
    events = store.unlock_events(
        [*result["new_unlock_ids"], "not-a-cat"],
        "de",
        "/_addons/anki-lockin-focus",
    )
    assert len(events) == 1
    assert set(events[0]) == {"id", "localized_name", "localized_reason", "asset_url"}
    assert events[0]["asset_url"].endswith("cat-01.png")


def test_failed_answer_transaction_rolls_back_event_and_aggregates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.record_answer(event_id="safe", card_id=1, rating=3, session_id="s")
    before_state = store.raw_state()

    def fail_refresh(*_args: object) -> None:
        raise RuntimeError("simulated aggregate failure")

    monkeypatch.setattr(store, "_refresh_affected", fail_refresh)
    with pytest.raises(RuntimeError, match="simulated"):
        store.record_answer(event_id="rolled-back", card_id=2, rating=3, session_id="s")

    assert store.raw_state() == before_state
    with sqlite3.connect(store.state_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_id = 'rolled-back'"
        ).fetchone()[0] == 0


def test_corrupt_or_future_database_is_not_silently_overwritten(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"{broken")
    with pytest.raises(StateError):
        CollectiblesStore(corrupt)
    assert corrupt.read_bytes() == b"{broken"

    future = tmp_path / "future.sqlite3"
    with sqlite3.connect(future) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(StateError):
        CollectiblesStore(future)


def test_legacy_json_is_imported_once_without_deleting_source(tmp_path: Path) -> None:
    legacy_path = tmp_path / "user_files" / "state.json"
    legacy_path.parent.mkdir(parents=True)
    today = date.today().isoformat()
    first_cat = "cat.resolved_cards_total.00001"
    legacy = {
        "schema_version": 1,
        "profiles": {
            "Legacy User": {
                "next_sequence": 2,
                "current_session_id": "legacy-session",
                "events": {
                    "event:legacy-answer": {
                        "event_id": "legacy-answer",
                        "revlog_id": "901",
                        "runtime_id": "legacy-runtime",
                        "sequence": 1,
                        "card_id": "42",
                        "deck_id": "7",
                        "rating": "good",
                        "passed": True,
                        "session_id": "legacy-session",
                        "undo_counter": 3,
                        "answered_at_ms": 1_700_000_000_000,
                        "local_day": today,
                        "active": True,
                    }
                },
                "unlocked_cats": {
                    first_cat: {
                        "unlocked_at": "2026-01-01T00:00:00+00:00",
                        "source_event": "event:legacy-answer",
                        "seen_at": None,
                    }
                },
                "equipped_cat_id": first_cat,
            }
        },
    }
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")

    store = CollectiblesStore(legacy_path, profile_name="Legacy User")
    assert store.state_path == legacy_path.with_name("collectibles.sqlite3")
    assert legacy_path.is_file()
    assert store.snapshot()["metrics"]["resolved_cards_total"] == 1
    assert store.snapshot()["equipped"]["id"] == first_cat
    store.close()

    reloaded = CollectiblesStore(legacy_path, profile_name="Legacy User")
    assert len(reloaded.raw_state()["profiles"]["Legacy User"]["events"]) == 1


def test_500_sequential_answers_complete_under_two_seconds(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    today = date.today()
    started = time.perf_counter()
    for number in range(500):
        result = store.record_answer(
            event_id=f"performance-{number}",
            revlog_id=10_000_000 + number,
            card_id=number,
            rating="good",
            session_id="performance-session",
            local_day=today,
        )
        assert result["changed"] is True
    elapsed = time.perf_counter() - started

    assert store.snapshot(today=today)["metrics"]["resolved_cards_total"] == 500
    assert elapsed < 2.0, f"500 answers took {elapsed:.3f}s"


def test_dialog_module_is_import_safe_without_anki() -> None:
    assert isinstance(qt_available(), bool)
    assert CollectiblesDialog is not None
    if not qt_available():
        with pytest.raises(RuntimeError, match="Anki"):
            CollectiblesDialog(None)
