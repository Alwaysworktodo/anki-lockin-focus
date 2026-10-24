"""Pytest collection support for source-inspection tests.

The repository root is an Anki add-on package.  These tests inspect its source
without importing the add-on, because importing it requires a running Anki
application.  Pytest otherwise imports the root ``__init__.py`` as part of
package setup before collecting tests.
"""

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep pytest from importing the add-on package during root setup."""

    for item in items:
        collector = item.parent
        while collector is not None:
            collector_path = getattr(collector, "path", None)
            if isinstance(collector, pytest.Package) and collector_path is not None:
                if Path(str(collector_path)).resolve() == PROJECT_ROOT:
                    collector.setup = lambda: None
            collector = collector.parent
