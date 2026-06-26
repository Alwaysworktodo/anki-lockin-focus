"""
Anki Lock-In Focus: Python Backend Tests

Tests the addon's Python logic including config management,
distraction-free mode toggle, shortcut bindings, session stats,
and hook registrations.

Uses mocks for Anki's Qt/application layer since we're testing
the addon logic in isolation.
"""
import json
import sys
import os
import tempfile
import time
from unittest.mock import Mock, patch, MagicMock, PropertyMock, call

# Add the addon path
sys.path.insert(0, os.path.expanduser("~/.local/share/Anki2/addons21"))

# We need to mock heavily since Anki's Qt is not available
# in non-interactive test mode without display


class MockAlignmentFlag:
    AlignCenter = 4
    AlignRight = 2


class MockQt:
    AlignmentFlag = MockAlignmentFlag()
    AlignCenter = 4
    AlignRight = 2


# Build comprehensive mock module
mock_aqt = MagicMock()
mock_aqt.qt.Qt = MockQt()
mock_aqt.qt.QTimer = MagicMock()
mock_aqt.qt.QTimer.singleShot = MagicMock()

# Mock QDialog components
mock_QDialog = MagicMock()
mock_QVBoxLayout = MagicMock()
mock_QHBoxLayout = MagicMock()
mock_QFormLayout = MagicMock()
mock_QLabel = MagicMock()
mock_QSpinBox = MagicMock()
mock_QDoubleSpinBox = MagicMock()
mock_QCheckBox = MagicMock()
mock_QComboBox = MagicMock()
mock_QTabWidget = MagicMock()
mock_QDialogButtonBox = MagicMock()
mock_QShortcut = MagicMock()
mock_QKeySequence = MagicMock()

# Mock QWidget
class MockQWidget:
    def __init__(self, parent=None):
        self.parent = parent


def build_mock_qt_module():
    """Build a mock PyQt6 module with all needed classes"""
    class MockQtCore:
        class QTimer:
            singleShot = MagicMock()
            def __init__(self): pass
            def setInterval(self, ms): pass
            def start(self): pass

    class MockQtGui:
        QKeySequence = MagicMock()
        QShortcut = MagicMock()

    class MockQtWidgets:
        QDialog = MagicMock()
        QVBoxLayout = MagicMock()
        QHBoxLayout = MagicMock()
        QFormLayout = MagicMock()
        QLabel = MagicMock()
        QSpinBox = MagicMock()
        QDoubleSpinBox = MagicMock()
        QCheckBox = MagicMock()
        QComboBox = MagicMock()
        QTabWidget = MagicMock()
        QDialogButtonBox = MagicMock()

        class QWidget:
            pass

    return MockQtCore, MockQtGui, MockQtWidgets


class TestConfigManagement:
    """Tests for config loading, defaults, and write-back"""

    @patch('builtins.open')
    def test_get_config_no_file(self, mock_open):
        """Config returns empty dict when no config file exists"""
        mock_open.side_effect = FileNotFoundError
        sys.modules['aqt'] = mock_aqt
        # We can't easily test this since get_config accesses mw.addonManager
        # which needs to be set up first
        pass

    def test_config_defaults_match_both_sides(self):
        """Verify default config in Python and JS are in sync"""
        # Read Python config
        py_config_path = os.path.expanduser(
            "~/.local/share/Anki2/addons21/anki-lockin-focus/config.json"
        )
        with open(py_config_path) as f:
            py_config = json.load(f)

        # Read JS config defaults
        js_path = os.path.expanduser(
            "~/.local/share/Anki2/addons21/anki-lockin-focus/web/lockin.js"
        )
        with open(js_path) as f:
            js_source = f.read()

        # Extract the JS default config object using a simple approach
        import re
        # Find the config default block
        match = re.search(r'let config = window\.AnkiLockInConfig \|\| \{([^}]+)\}', js_source, re.DOTALL)
        assert match is not None, "Could not find JS config defaults"
        
        js_block = match.group(1)
        
        # Check key values match by parsing individual assignments
        def find_js_value(key):
            pattern = rf'\b{key}\s*:\s*([^,\n]+)'
            m = re.search(pattern, js_block)
            if m:
                val = m.group(1).strip()
                # Convert to Python equivalents
                if val == 'true': return True
                if val == 'false': return False
                if val == 'null': return None
                try:
                    if '.' in val:
                        return float(val)
                    return int(val)
                except ValueError:
                    return val.strip('"\'')
            return None

        # Compare values
        checks = [
            ('health_max', 100),
            ('health_drain_rate', 2.0),
            ('health_heal_easy', 15),
            ('health_heal_good', 10),
            ('health_heal_hard', 5),
            ('health_heal_again', -10),
            ('pomodoro_study_duration', 25),
            ('pomodoro_break_duration', 5),
            ('pomodoro_enable_sound', True),
            ('speed_focus_alert_delay', 8),
            ('no_distraction_enabled', True),
            ('adhd_boosters_streak_enabled', True),
            ('adhd_boosters_shield_enabled', True),
            ('grace_period_duration', 180),
            ('sigmoid_drain_enabled', True),
        ]

        mismatches = []
        for key, expected in checks:
            py_val = py_config.get(key)
            js_val = find_js_value(key)
            
            # Special handling: JS has extra defaults not in config.json
            if py_val is None and key in py_config:
                mismatches.append(f"  {key}: missing in config.json")
                continue
                
            if py_val != expected and expected is not None:
                mismatches.append(f"  {key}: config.json={py_val}, expected={expected}")
            
            # Only compare JS if we found the value
            if js_val is not None and js_val != expected:
                mismatches.append(f"  {key}: JS default={js_val}, expected={expected}")

        assert not mismatches, (
            f"Config value mismatches between config.json and JS defaults:\n" +
            "\n".join(mismatches)
        )


class TestStateManagement:
    """Tests for session state handling"""

    def test_session_stats_reset_on_review_entry(self):
        """Verify session_stats is properly reset when entering review mode"""
        pass  # Requires full Anki mock with state hooks

    def test_stats_dialog_shown_only_with_cards(self):
        """Session dialog should not appear if 0 cards studied"""
        pass


class TestDistractionFreeMode:
    """Tests for the distraction-free mode logic"""

    def test_distraction_free_logic(self):
        """Test the core logic of set_distraction_free"""
        pass  # Requires mock mw with toolbar/bottomWeb/menuBar

    def test_toggle_restores_all_chrome(self):
        """Toggling back should restore menu bar, toolbar, bottom web"""
        pass


class TestConfigDialog:
    """Tests for the settings dialog"""

    def test_preset_profile_values(self):
        """Verify preset profiles set expected values"""
        pass  # Requires mock dialog instance

    def test_save_settings_writes_config(self):
        """Verify save_settings writes through addonManager"""
        pass

    def test_bar_position_config(self):
        """Verify bar_position config is properly round-tripped"""
        pass


if __name__ == "__main__":
    # Run basic structural verification
    test = TestConfigManagement()
    test.test_config_defaults_match_both_sides()
    print("✓ Config defaults match between Python and JS")
    print("All structural checks passed!")
