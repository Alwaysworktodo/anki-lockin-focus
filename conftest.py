"""
Conftest for pytest in the anki-lockin-focus project.

The addon's __init__.py imports from `aqt` (Anki's runtime) which is not
available in CI / non-interactive test environments. Pytest's auto-test
discovery sees __init__.py and tries to import it, which fails.

This conftest pre-installs a mock `aqt` module and marks the addon's
__init__.py as `pytest.importorskip`-friendly via path resolution so
pytest's rootdir-conftest setup doesn't try to import it as a test
module. It also ensures pytest treats the project root as a non-package
directory by adding the project root (not the parent containing
__init__.py) to sys.path.
"""
import sys
import os
import types
from unittest.mock import MagicMock

# Ensure the project root is on sys.path (not the addon package itself)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Pre-install a mock `aqt` module so addon's __init__.py can be parsed
# without raising ModuleNotFoundError if it ever gets imported.
if "aqt" not in sys.modules:
    aqt_mock = types.ModuleType("aqt")
    aqt_mock.mw = MagicMock()
    aqt_mock.gui_hooks = MagicMock()
    sys.modules["aqt"] = aqt_mock

    # Mock aqt.qt submodule
    aqt_qt = types.ModuleType("aqt.qt")
    aqt_qt.Qt = MagicMock()
    aqt_qt.QTimer = MagicMock()
    aqt_qt.QDialog = MagicMock()
    aqt_qt.QVBoxLayout = MagicMock()
    aqt_qt.QHBoxLayout = MagicMock()
    aqt_qt.QFormLayout = MagicMock()
    aqt_qt.QLabel = MagicMock()
    aqt_qt.QSpinBox = MagicMock()
    aqt_qt.QDoubleSpinBox = MagicMock()
    aqt_qt.QCheckBox = MagicMock()
    aqt_qt.QComboBox = MagicMock()
    aqt_qt.QTabWidget = MagicMock()
    aqt_qt.QWidget = MagicMock()
    aqt_qt.QDialogButtonBox = MagicMock()
    aqt_qt.QShortcut = MagicMock()
    aqt_qt.QKeySequence = MagicMock()
    aqt_qt.sip = MagicMock()
    sys.modules["aqt.qt"] = aqt_qt

    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.showInfo = MagicMock()
    aqt_utils.showWarning = MagicMock()
    sys.modules["aqt.utils"] = aqt_utils

    aqt_reviewer = types.ModuleType("aqt.reviewer")
    aqt_reviewer.Reviewer = type("Reviewer", (), {})
    sys.modules["aqt.reviewer"] = aqt_reviewer

    aqt_deckbrowser = types.ModuleType("aqt.deckbrowser")
    aqt_deckbrowser.DeckBrowser = type("DeckBrowser", (), {})
    sys.modules["aqt.deckbrowser"] = aqt_deckbrowser

    aqt_overview = types.ModuleType("aqt.overview")
    aqt_overview.Overview = type("Overview", (), {})
    sys.modules["aqt.overview"] = aqt_overview


def pytest_collectstart(collector):
    """Skip the addon's __init__.py during pytest collection."""
    try:
        path = str(getattr(collector, "fspath", "") or getattr(collector, "path", ""))
    except Exception:
        return
    if path.endswith("__init__.py") and "anki-lockin-focus" in path:
        collector.collect = lambda: []
