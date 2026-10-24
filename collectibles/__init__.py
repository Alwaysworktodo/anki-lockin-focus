"""Local cat collectibles for Anki Lock-In.

This package is intentionally importable without Anki so its catalog and state
logic can be tested directly from the source checkout.
"""

from .catalog import (
    DAILY_GOAL,
    DEFAULT_LOCALE,
    EXPECTED_THRESHOLDS,
    CatEntry,
    Catalog,
    CatalogError,
    load_catalog,
    normalize_locale,
)
from .dialog import CollectiblesDialog, qt_available, show_collectibles_dialog
from .store import (
    DEFAULT_PROFILE,
    STATE_SCHEMA_VERSION,
    CollectiblesStore,
    StateError,
    default_state_path,
)


__all__ = [
    "DAILY_GOAL",
    "DEFAULT_LOCALE",
    "DEFAULT_PROFILE",
    "EXPECTED_THRESHOLDS",
    "STATE_SCHEMA_VERSION",
    "CatEntry",
    "Catalog",
    "CatalogError",
    "CollectiblesDialog",
    "CollectiblesStore",
    "StateError",
    "default_state_path",
    "load_catalog",
    "normalize_locale",
    "qt_available",
    "show_collectibles_dialog",
]
