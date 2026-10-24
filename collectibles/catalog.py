"""Validated, local-only catalog for Lock-In cat collectibles.

The catalog intentionally contains no remote URLs for image loading.  Artwork is
shipped with the add-on and the optional source field is attribution metadata,
not a runtime dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


SUPPORTED_LOCALES = ("de", "en")
DEFAULT_LOCALE = "de"
CATALOG_SCHEMA_VERSION = 1
DAILY_GOAL = 20

EXPECTED_THRESHOLDS: dict[str, tuple[int, ...]] = {
    "resolved_cards_total": (
        1,
        5,
        10,
        25,
        50,
        100,
        200,
        350,
        500,
        750,
        1000,
        1500,
        2500,
        4000,
        7000,
        10000,
    ),
    "session_resolved_max": (3, 5, 8, 10, 15, 20, 25, 30, 40, 50, 75, 100),
    "recoveries_total": (1, 3, 5, 10, 20, 35, 50, 100),
    "daily_goal_streak_max": (2, 3, 5, 7, 14, 30, 60, 100),
    "qualified_days_total": (1, 5, 10, 25, 50, 100),
}


class CatalogError(ValueError):
    """Raised when the bundled catalog is malformed or incomplete."""


def normalize_locale(locale: str | None) -> str:
    """Return one of the two supported locale keys, defaulting to German."""

    if not locale:
        return DEFAULT_LOCALE
    language = str(locale).strip().lower().replace("_", "-").split("-", 1)[0]
    return language if language in SUPPORTED_LOCALES else DEFAULT_LOCALE


def _translated(values: Mapping[str, str], locale: str) -> str:
    language = normalize_locale(locale)
    return values.get(language) or values.get(DEFAULT_LOCALE) or values.get("en") or ""


@dataclass(frozen=True)
class CatEntry:
    id: str
    sort_order: int
    rarity: str
    name: Mapping[str, str]
    description: Mapping[str, str]
    alt: Mapping[str, str]
    asset: str
    metric: str
    threshold: int
    credit_id: str

    def localized(self, locale: str, asset_url_prefix: str = "") -> dict[str, Any]:
        prefix = asset_url_prefix.rstrip("/")
        asset = self.asset.lstrip("/")
        asset_url = f"{prefix}/{asset}" if prefix else asset
        return {
            "id": self.id,
            "sort_order": self.sort_order,
            "rarity": self.rarity,
            "localized_name": _translated(self.name, locale),
            "localized_description": _translated(self.description, locale),
            "localized_alt": _translated(self.alt, locale),
            "asset": self.asset,
            "asset_url": asset_url,
            "unlock": {"metric": self.metric, "threshold": self.threshold},
            "credit_id": self.credit_id,
        }


class Catalog:
    """Immutable, validated view of ``catalog.json``."""

    def __init__(
        self,
        entries: Iterable[CatEntry],
        credits: Mapping[str, Mapping[str, str]],
        *,
        schema_version: int = CATALOG_SCHEMA_VERSION,
        daily_goal: int = DAILY_GOAL,
    ) -> None:
        self.schema_version = schema_version
        self.daily_goal = daily_goal
        self.entries = tuple(sorted(entries, key=lambda entry: entry.sort_order))
        self.credits = {key: dict(value) for key, value in credits.items()}
        self.by_id = {entry.id: entry for entry in self.entries}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Catalog":
        catalog_path = Path(path) if path is not None else Path(__file__).with_name("catalog.json")
        try:
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogError(f"Unable to load collectible catalog: {exc}") from exc

        cls._validate_raw(raw)
        entries = [
            CatEntry(
                id=item["id"],
                sort_order=item["sort_order"],
                rarity=item["rarity"],
                name=dict(item["name"]),
                description=dict(item["description"]),
                alt=dict(item["alt"]),
                asset=item["asset"],
                metric=item["unlock"]["metric"],
                threshold=item["unlock"]["threshold"],
                credit_id=item["credit_id"],
            )
            for item in raw["cats"]
        ]
        return cls(
            entries,
            raw["credits"],
            schema_version=raw["schema_version"],
            daily_goal=raw["daily_goal"],
        )

    @staticmethod
    def _validate_raw(raw: Any) -> None:
        if not isinstance(raw, dict):
            raise CatalogError("Catalog root must be an object")
        if raw.get("schema_version") != CATALOG_SCHEMA_VERSION:
            raise CatalogError("Unsupported catalog schema version")
        if raw.get("daily_goal") != DAILY_GOAL:
            raise CatalogError(f"daily_goal must be {DAILY_GOAL}")
        credits = raw.get("credits")
        if not isinstance(credits, dict) or not credits:
            raise CatalogError("Catalog must define credits")
        cats = raw.get("cats")
        if not isinstance(cats, list) or len(cats) != 50:
            raise CatalogError("Catalog must contain exactly 50 cats")

        seen_ids: set[str] = set()
        seen_orders: set[int] = set()
        seen_assets: set[str] = set()
        expected_assets = {f"web/cats/cat-{number:02d}.png" for number in range(1, 51)}
        thresholds: dict[str, list[int]] = {metric: [] for metric in EXPECTED_THRESHOLDS}
        allowed_rarities = {"common", "uncommon", "rare", "epic", "legendary"}

        for index, item in enumerate(cats, start=1):
            if not isinstance(item, dict):
                raise CatalogError(f"Cat {index} must be an object")
            cat_id = item.get("id")
            if not isinstance(cat_id, str) or not cat_id:
                raise CatalogError(f"Cat {index} has an invalid id")
            if cat_id in seen_ids:
                raise CatalogError(f"Duplicate cat id: {cat_id}")
            seen_ids.add(cat_id)

            order = item.get("sort_order")
            if not isinstance(order, int) or order < 1 or order in seen_orders:
                raise CatalogError(f"Invalid or duplicate sort_order for {cat_id}")
            seen_orders.add(order)
            if item.get("rarity") not in allowed_rarities:
                raise CatalogError(f"Invalid rarity for {cat_id}")

            for field in ("name", "description", "alt"):
                translations = item.get(field)
                if not isinstance(translations, dict):
                    raise CatalogError(f"{cat_id}.{field} must be an object")
                for locale in SUPPORTED_LOCALES:
                    value = translations.get(locale)
                    if not isinstance(value, str) or not value.strip():
                        raise CatalogError(f"{cat_id}.{field}.{locale} is required")

            asset = item.get("asset")
            if asset not in expected_assets or asset in seen_assets:
                raise CatalogError(f"Invalid or duplicate asset for {cat_id}: {asset!r}")
            seen_assets.add(asset)
            credit_id = item.get("credit_id")
            if credit_id not in credits:
                raise CatalogError(f"Unknown credit_id for {cat_id}")

            unlock = item.get("unlock")
            if not isinstance(unlock, dict):
                raise CatalogError(f"{cat_id}.unlock must be an object")
            metric = unlock.get("metric")
            threshold = unlock.get("threshold")
            if metric not in EXPECTED_THRESHOLDS or not isinstance(threshold, int):
                raise CatalogError(f"Invalid unlock rule for {cat_id}")
            thresholds[metric].append(threshold)

        if seen_orders != set(range(1, 51)):
            raise CatalogError("sort_order values must be exactly 1 through 50")
        if seen_assets != expected_assets:
            raise CatalogError("Catalog must use every cat-01.png through cat-50.png exactly once")
        for metric, expected in EXPECTED_THRESHOLDS.items():
            actual = tuple(thresholds[metric])
            if actual != expected:
                raise CatalogError(f"Unexpected thresholds for {metric}: {actual}")

    def localized_entry(
        self, cat_id: str, locale: str = DEFAULT_LOCALE, asset_url_prefix: str = ""
    ) -> dict[str, Any] | None:
        entry = self.by_id.get(cat_id)
        if entry is None:
            return None
        localized = entry.localized(locale, asset_url_prefix)
        localized["credit"] = dict(self.credits[entry.credit_id])
        return localized


def load_catalog(path: str | Path | None = None) -> Catalog:
    return Catalog.load(path)


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "DAILY_GOAL",
    "DEFAULT_LOCALE",
    "EXPECTED_THRESHOLDS",
    "SUPPORTED_LOCALES",
    "CatEntry",
    "Catalog",
    "CatalogError",
    "load_catalog",
    "normalize_locale",
]
