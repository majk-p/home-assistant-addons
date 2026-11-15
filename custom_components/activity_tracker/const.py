"""Constants for the Activity Tracker integration.

This module is intentionally free of Home Assistant imports so the pure
logic in ``coordinator.py`` can be unit-tested without a Home Assistant
instance.
"""

from __future__ import annotations

from datetime import timedelta
import re

DOMAIN = "activity_tracker"

# Tracked activities, in display order.
ACTIVITIES: tuple[str, ...] = ("feeding", "pee", "poo_small", "poo_large")

# Human-facing labels used in entity friendly names.
#
# This is the English fallback used when entity-name translations are not
# available (``entities.py`` resolves names through ``translation_key``;
# see ``strings.json`` / ``translations/`` for the localized templates).
ACTIVITY_LABELS: dict[str, str] = {
    "feeding": "feeding",
    "pee": "pee",
    "poo_small": "poo (small)",
    "poo_large": "poo (large)",
}

ACTIVITY_ICONS: dict[str, str] = {
    "feeding": "mdi:baby-bottle-outline",
    "pee": "mdi:water",
    "poo_small": "mdi:emoticon-poo",
    "poo_large": "mdi:emoticon-poo-outline",
}
UNDO_ICON = "mdi:undo-variant"

# Config entry data keys.
CONF_NAME = "name"
CONF_PREFIX = "prefix"
CONF_FEEDING_INTERVAL = "feeding_interval_hours"
CONF_NOTIFY_TARGETS = "notify_targets"

# Feeding interval defaults and limits (mirrors the YAML-package helper).
DEFAULT_FEEDING_INTERVAL = 3.0
MIN_FEEDING_INTERVAL = 1.0
MAX_FEEDING_INTERVAL = 6.0
FEEDING_INTERVAL_STEP = 0.25

# Feeding-due behaviour.
FEEDING_GRACE = timedelta(minutes=10)
"""A feeding only counts as overdue interval + grace after the last one."""

NOTIFY_REPEAT = timedelta(hours=1)
"""While a feeding stays overdue, remind at most once per hour."""

DUE_CHECK_INTERVAL = timedelta(seconds=60)
"""Periodic feeding-due re-check while HA runs (also recomputed on press)."""

# Overdue-feeding notification templates, keyed by HA language code.
# Placeholders: {name} = the child's display name, {ago} = humanized time
# since the last feeding, {interval} = the configured interval in hours.
NOTIFICATION_MESSAGES: dict[str, str] = {
    "en": "{name} feeding overdue — last fed {ago} ago (interval {interval}h)",
    "pl": "{name}: karmienie po terminie — ostatnio {ago} temu (odstęp {interval}h)",
}
DEFAULT_NOTIFICATION_LANGUAGE = "en"


def notification_language(language: str | None) -> str:
    """Map a Home Assistant language tag to a NOTIFICATION_MESSAGES key.

    Exact match first ("pl"), then the primary subtag before a
    hyphen/underscore ("pl-PL" / "pl_PL" -> "pl"), else English.
    """
    if not language:
        return DEFAULT_NOTIFICATION_LANGUAGE
    if language in NOTIFICATION_MESSAGES:
        return language
    primary = re.split(r"[-_]", language, maxsplit=1)[0]
    if primary in NOTIFICATION_MESSAGES:
        return primary
    return DEFAULT_NOTIFICATION_LANGUAGE


# Config entry versions. Bump and handle in async_migrate_entry if the
# stored shape ever changes.
CONFIG_ENTRY_VERSION = 1
CONFIG_ENTRY_MINOR_VERSION = 1

# Per-child storage document versions (.storage/activity_tracker_<prefix>).
# The document is written by helpers.storage.Store, which transparently
# loads documents with a different *minor* version. A future *major* bump
# must load with the old version, transform, and re-save (see ChildStore).
STORAGE_VERSION = 1
STORAGE_MINOR_VERSION = 1


def entity_unique_ids(prefix: str) -> set[str]:
    """Return every entity unique id a child with this prefix owns.

    Used to clean up registry leftovers when the prefix is renamed in the
    options flow (new unique ids are created; old ones would linger).
    """
    ids: set[str] = set()
    for activity in ACTIVITIES:
        ids.update(
            {
                activity,
                f"{activity}_undo",
                f"{activity}_total",
                f"{activity}_today",
                f"{activity}_last",
                f"{activity}_daily_avg",
            }
        )
    ids.update({"feeding_interval_hours", "feeding_due"})
    return {f"{prefix}_{unique_id}" for unique_id in ids}
