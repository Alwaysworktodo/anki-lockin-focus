"""Native Qt collection browser with an import-safe non-Anki fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import normalize_locale
from .store import CollectiblesStore


try:  # pragma: no cover - the real Qt branch is exercised inside Anki.
    from aqt.qt import (
        QDialog,
        QDialogButtonBox,
        QGridLayout,
        QHBoxLayout,
        QIcon,
        QLabel,
        QPixmap,
        QPushButton,
        QScrollArea,
        QSize,
        QToolButton,
        QVBoxLayout,
        QWidget,
        Qt,
    )

    QT_AVAILABLE = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover - tested without Anki.
    QT_AVAILABLE = False


_UI = {
    "de": {
        "title": "Katzensammlung",
        "summary": "{unlocked} von {total} Katzen freigeschaltet",
        "locked": "Noch gesperrt",
        "progress": "Fortschritt: {current} / {target}",
        "equip": "Katze ausrüsten",
        "equipped": "Ausgerüstet",
        "close": "Schließen",
        "select": "Wähle eine Katze aus, um Details zu sehen.",
    },
    "en": {
        "title": "Cat Collection",
        "summary": "{unlocked} of {total} cats unlocked",
        "locked": "Still locked",
        "progress": "Progress: {current} / {target}",
        "equip": "Equip cat",
        "equipped": "Equipped",
        "close": "Close",
        "select": "Select a cat to see its details.",
    },
}


def qt_available() -> bool:
    return QT_AVAILABLE


if QT_AVAILABLE:  # pragma: no branch

    class CollectiblesDialog(QDialog):
        """Accessible five-column gallery backed directly by Python state."""

        def __init__(
            self,
            store: CollectiblesStore,
            *,
            locale: str = "de",
            parent: QWidget | None = None,
            asset_root: str | Path | None = None,
            selected_cat_id: str | None = None,
        ) -> None:
            super().__init__(parent)
            self.store = store
            self.locale = normalize_locale(locale)
            self.text = _UI[self.locale]
            self.asset_root = (
                Path(asset_root)
                if asset_root is not None
                else Path(__file__).resolve().parent.parent
            )
            self.selected_cat_id: str | None = selected_cat_id
            self.setWindowTitle(self.text["title"])
            self.setMinimumSize(820, 680)
            self.setAccessibleName(self.text["title"])
            self._build_ui()
            self.store.mark_seen()
            self.refresh()

        def _build_ui(self) -> None:
            root = QVBoxLayout(self)

            self.summary = QLabel()
            self.summary.setObjectName("lockin-cat-summary")
            self.summary.setAccessibleName(self.text["summary"].format(unlocked=0, total=50))
            root.addWidget(self.summary)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setAccessibleName(self.text["title"])
            self.grid_widget = QWidget()
            self.grid = QGridLayout(self.grid_widget)
            self.grid.setSpacing(10)
            scroll.setWidget(self.grid_widget)
            root.addWidget(scroll, 1)

            details = QHBoxLayout()

            self.preview_image = QLabel()
            self.preview_image.setObjectName("lockin-cat-preview")
            align_center = (
                Qt.AlignmentFlag.AlignCenter
                if hasattr(Qt, "AlignmentFlag")
                else Qt.AlignCenter
            )
            self.preview_image.setAlignment(align_center)
            self.preview_image.setMaximumSize(512, 512)
            self.preview_image.setVisible(False)
            details.addWidget(self.preview_image)

            detail_text = QVBoxLayout()
            self.detail_name = QLabel(self.text["select"])
            self.detail_name.setWordWrap(True)
            self.detail_description = QLabel("")
            self.detail_description.setWordWrap(True)
            self.detail_progress = QLabel("")
            detail_text.addWidget(self.detail_name)
            detail_text.addWidget(self.detail_description)
            detail_text.addWidget(self.detail_progress)
            details.addLayout(detail_text, 1)

            self.equip_button = QPushButton(self.text["equip"])
            self.equip_button.setEnabled(False)
            self.equip_button.clicked.connect(self._equip_selected)
            details.addWidget(self.equip_button)
            root.addLayout(details)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
            if close_button is not None:
                close_button.setText(self.text["close"])
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)

        def _clear_grid(self) -> None:
            while self.grid.count():
                item = self.grid.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

        def refresh(self) -> None:
            snapshot = self.store.snapshot(self.locale, "")
            self._snapshot = snapshot
            self.summary.setText(
                self.text["summary"].format(
                    unlocked=snapshot["unlocked_count"], total=snapshot["total_count"]
                )
            )
            self.summary.setAccessibleName(self.summary.text())
            self._clear_grid()

            for index, cat in enumerate(snapshot["cats"]):
                button = QToolButton()
                button.setObjectName(f"lockin-cat-{cat['id']}")
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                button.setIconSize(QSize(96, 96))
                if cat["unlocked"]:
                    image_path = self.asset_root / cat["asset"]
                    if image_path.is_file():
                        button.setIcon(QIcon(str(image_path)))
                    button.setText(cat["localized_name"])
                    accessible = cat["localized_alt"]
                else:
                    button.setText("🔒 ???")
                    accessible = (
                        f"{self.text['locked']}. "
                        f"{cat['localized_description']}"
                    )
                button.setAccessibleName(accessible)
                button.setAccessibleDescription(cat["localized_description"])
                button.setMinimumSize(128, 132)
                button.clicked.connect(
                    lambda _checked=False, cat_id=cat["id"]: self._select_cat(cat_id)
                )
                self.grid.addWidget(button, index // 5, index % 5)

            if self.selected_cat_id:
                self._select_cat(self.selected_cat_id)

        def _cat(self, cat_id: str) -> dict[str, Any] | None:
            return next(
                (cat for cat in self._snapshot["cats"] if cat["id"] == cat_id), None
            )

        def _select_cat(self, cat_id: str) -> None:
            cat = self._cat(cat_id)
            if cat is None:
                return
            self.selected_cat_id = cat_id
            self.detail_name.setText(
                cat["localized_name"] if cat["unlocked"] else self.text["locked"]
            )
            self.detail_description.setText(cat["localized_description"])
            self.detail_progress.setText(
                self.text["progress"].format(
                    current=cat["progress"]["current"],
                    target=cat["progress"]["target"],
                )
            )
            equipped = self._snapshot.get("equipped")
            is_equipped = bool(equipped and equipped["id"] == cat_id)
            self.equip_button.setText(
                self.text["equipped"] if is_equipped else self.text["equip"]
            )
            self.equip_button.setEnabled(bool(cat["unlocked"] and not is_equipped))

            if cat["unlocked"]:
                image_path = self.asset_root / cat["asset"]
                if image_path.is_file():
                    pixmap = QPixmap(str(image_path))
                    if not pixmap.isNull():
                        aspect_mode = (
                            Qt.AspectRatioMode.KeepAspectRatio
                            if hasattr(Qt, "AspectRatioMode")
                            else Qt.KeepAspectRatio
                        )
                        transform_mode = (
                            Qt.TransformationMode.SmoothTransformation
                            if hasattr(Qt, "TransformationMode")
                            else Qt.SmoothTransformation
                        )
                        scaled = pixmap.scaled(512, 512, aspect_mode, transform_mode)
                        self.preview_image.setPixmap(scaled)
                        self.preview_image.setVisible(True)
                    else:
                        self.preview_image.clear()
                        self.preview_image.setVisible(False)
                else:
                    self.preview_image.clear()
                    self.preview_image.setVisible(False)
            else:
                self.preview_image.clear()
                self.preview_image.setVisible(False)

        def _equip_selected(self) -> None:
            if self.selected_cat_id is None:
                return
            self.store.equip(self.selected_cat_id)
            self.refresh()


else:

    class CollectiblesDialog:  # type: ignore[no-redef]
        """Placeholder that keeps imports and non-Qt unit tests safe."""

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("The collectibles dialog requires Anki's Qt runtime")


def show_collectibles_dialog(
    store: CollectiblesStore,
    *,
    locale: str = "de",
    parent: Any = None,
    asset_root: str | Path | None = None,
    selected_cat_id: str | None = None,
) -> Any:
    if not QT_AVAILABLE:
        raise RuntimeError("The collectibles dialog requires Anki's Qt runtime")
    dialog = CollectiblesDialog(
        store,
        locale=locale,
        parent=parent,
        asset_root=asset_root,
        selected_cat_id=selected_cat_id,
    )
    dialog.exec()
    return dialog


__all__ = ["CollectiblesDialog", "qt_available", "show_collectibles_dialog"]
