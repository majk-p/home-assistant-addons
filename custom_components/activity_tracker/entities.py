"""Entities for the Activity Tracker integration.

Per child (prefix ``P``, display name ``N``) and activity ``A`` in
(feeding, pee, poo_small, poo_large):

- ``button.P_A`` / ``button.P_A_undo`` — press = event happened now / undo
- ``sensor.P_A_total``   — lifetime count, ``total_increasing`` (statistics)
- ``sensor.P_A_today``   — count since local midnight
- ``sensor.P_A_last``    — timestamp of the last event
- ``sensor.P_A_daily_avg`` — total per completed day since the first event

Feeding only:

- ``number.P_feeding_interval_hours`` — editable target interval (hours)
- ``binary_sensor.P_feeding_due``     — ON once interval + grace elapsed

All entities are dumb views over the per-child storage document owned by
``ChildStore`` (see coordinator.py for why). Entity ids are pinned to the
prefix via the ``suggested_object_id`` property override, so they do not
depend on the (diacritics-friendly) display name.

Friendly names are localized: every entity carries a ``translation_key``
and a ``child_name`` placeholder (the display name), resolved against the
integration's ``entity`` translations (see ``strings.json`` and
``translations/``), e.g. ``"%{child_name} feeding"`` -> "Robert feeding" /
"Robert karmienie". Entity ids never depend on the language.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVITIES,
    ACTIVITY_ICONS,
    ACTIVITY_LABELS,
    DOMAIN,
    FEEDING_INTERVAL_STEP,
    MAX_FEEDING_INTERVAL,
    MIN_FEEDING_INTERVAL,
    UNDO_ICON,
)
from .coordinator import ActivityState, ChildRuntime, TrackerData


def _platform_translations(platform: Any) -> Mapping[str, str]:
    """Return the entity-name translations of an ``EntityPlatform``.

    Home Assistant keeps them on the platform object up to the 2025.x
    releases and on its ``PlatformData`` view from 2026.x on; support
    both shapes so the integration works across versions.
    """
    data = getattr(platform, "platform_data", None)
    if data is not None:
        return data.platform_translations
    return getattr(platform, "platform_translations", {})


class ActivityTrackerEntity(Entity):
    """Base class: ties an entity to its child's runtime and device.

    Names localize through ``translation_key`` + the ``child_name``
    placeholder. Home Assistant resolves ``translation_key`` names only
    for entities with ``has_entity_name = True`` — which would prepend
    the device (child) name to a name that already contains it, giving
    "Robert Robert feeding". We therefore keep ``has_entity_name = False``
    and resolve the name ourselves from the same platform translations
    HA loads (identically on every HA version), substituting the
    placeholder. The English fallback covers setups where translations
    are unavailable.
    """

    _attr_has_entity_name = False

    def __init__(
        self,
        runtime: ChildRuntime,
        unique_id: str,
        translation_key: str,
        fallback_name: str,
    ) -> None:
        """Initialize the entity.

        ``translation_key`` is the per-platform key under ``entity`` in
        strings.json (e.g. "feeding" for ``button.feeding``);
        ``fallback_name`` is the English name used when translations are
        not loaded.
        """
        self._runtime = runtime
        self._object_id = unique_id
        self._fallback_name = fallback_name
        self._attr_unique_id = unique_id
        self._attr_translation_key = translation_key
        self._attr_translation_placeholders = {"child_name": runtime.name}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.prefix)},
            name=runtime.name,
        )

    @property
    def name(self) -> str | None:
        """Return the localized friendly name, e.g. "Robert karmienie"."""
        if (platform := self.platform) is None:
            # Not attached to a platform yet (unit tests, pre-registration).
            return self._fallback_name
        translations = _platform_translations(platform)
        key = self._name_translation_key
        if key and (template := translations.get(key)):
            try:
                return template.format(**self.translation_placeholders)
            except (KeyError, IndexError):
                # A translated template with placeholders other than
                # child_name; fall back rather than render a broken name.
                return self._fallback_name
        return self._fallback_name

    @property
    def suggested_object_id(self) -> str | None:
        """Return the exact, prefix-based object id.

        Overriding this property pins entity ids (``button.rob_feeding``
        & co.) to the prefix instead of the slugified display name or the
        (language-dependent) friendly name, which matters when the two
        differ. Home Assistant's entity platform consults this property
        on first registration only; afterwards the registry entry keeps
        its id.
        """
        return self._object_id

    def _activity_state(self, activity: str) -> ActivityState:
        return self._runtime.store.tracker.activities[activity]

    def _tracker(self) -> TrackerData:
        return self._runtime.store.tracker


class StoreWatchedEntity(ActivityTrackerEntity):
    """Re-render whenever the child's storage document changes."""

    async def async_added_to_hass(self) -> None:
        """Subscribe to store changes."""
        self.async_on_remove(
            self._runtime.store.async_add_listener(self._async_store_changed)
        )

    @callback
    def _async_store_changed(self) -> None:
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


class ActivityButton(ActivityTrackerEntity, ButtonEntity):
    """Press = "this activity happened now"."""

    def __init__(self, runtime: ChildRuntime, activity: str) -> None:
        """Initialize the event button."""
        super().__init__(
            runtime,
            f"{runtime.prefix}_{activity}",
            activity,
            f"{runtime.name} {ACTIVITY_LABELS[activity]}",
        )
        self._attr_icon = ACTIVITY_ICONS[activity]
        self._activity = activity

    async def async_press(self) -> None:
        """Log an event; counters, timestamps and charts update."""
        await self._runtime.store.async_log_event(self._activity)


class ActivityUndoButton(ActivityTrackerEntity, ButtonEntity):
    """Press = "undo the last event of this activity"."""

    def __init__(self, runtime: ChildRuntime, activity: str) -> None:
        """Initialize the undo button."""
        super().__init__(
            runtime,
            f"{runtime.prefix}_{activity}_undo",
            f"{activity}_undo",
            f"{runtime.name} {ACTIVITY_LABELS[activity]} undo",
        )
        self._attr_icon = UNDO_ICON
        self._activity = activity

    async def async_press(self) -> None:
        """Undo the last event (counters only; history is kept)."""
        await self._runtime.store.async_undo_event(self._activity)


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------


class ActivityTotalSensor(StoreWatchedEntity, SensorEntity):
    """Lifetime total; ``total_increasing`` feeds the daily bar charts."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, runtime: ChildRuntime, activity: str) -> None:
        """Initialize the total sensor."""
        super().__init__(
            runtime,
            f"{runtime.prefix}_{activity}_total",
            f"{activity}_total",
            f"{runtime.name} {ACTIVITY_LABELS[activity]} total",
        )
        self._attr_icon = ACTIVITY_ICONS[activity]
        self._activity = activity

    @property
    def native_value(self) -> int:
        """Return the lifetime count (never reset)."""
        return self._activity_state(self._activity).total


class ActivityTodaySensor(StoreWatchedEntity, SensorEntity):
    """Count since local midnight; resets at local midnight."""

    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, runtime: ChildRuntime, activity: str) -> None:
        """Initialize the today sensor."""
        super().__init__(
            runtime,
            f"{runtime.prefix}_{activity}_today",
            f"{activity}_today",
            f"{runtime.name} {ACTIVITY_LABELS[activity]} today",
        )
        self._attr_icon = ACTIVITY_ICONS[activity]
        self._activity = activity

    @property
    def native_value(self) -> int:
        """Return the count since local midnight."""
        return self._activity_state(self._activity).today

    @property
    def last_reset(self) -> datetime:
        """Return local midnight of the tracked day."""
        return dt_util.start_of_local_day(dt_util.now())


class ActivityLastSensor(StoreWatchedEntity, SensorEntity):
    """Timestamp of the last event of this activity."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, runtime: ChildRuntime, activity: str) -> None:
        """Initialize the last-event sensor."""
        super().__init__(
            runtime,
            f"{runtime.prefix}_{activity}_last",
            f"{activity}_last",
            f"{runtime.name} {ACTIVITY_LABELS[activity]} last",
        )
        self._attr_icon = ACTIVITY_ICONS[activity]
        self._activity = activity

    @property
    def native_value(self) -> datetime | None:
        """Return the last event time (aware, local) or None.

        Timestamp-device-class sensors return datetime objects; Home
        Assistant serializes them to ISO strings for the state machine.
        """
        return self._activity_state(self._activity).last


class ActivityDailyAvgSensor(StoreWatchedEntity, SensorEntity):
    """Lifetime total per completed day since the first event ever."""

    def __init__(self, runtime: ChildRuntime, activity: str) -> None:
        """Initialize the daily-average sensor."""
        super().__init__(
            runtime,
            f"{runtime.prefix}_{activity}_daily_avg",
            f"{activity}_daily_avg",
            f"{runtime.name} {ACTIVITY_LABELS[activity]} daily avg",
        )
        self._attr_icon = ACTIVITY_ICONS[activity]
        self._activity = activity

    @property
    def native_value(self) -> float:
        """Return total / max(1, completed days), rounded to 1 decimal."""
        return self._tracker().daily_avg(self._activity, dt_util.now().date())


# ---------------------------------------------------------------------------
# Feeding-only entities
# ---------------------------------------------------------------------------


class FeedingIntervalNumber(StoreWatchedEntity, NumberEntity):
    """Feeding interval in hours; the source of truth once created."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = MIN_FEEDING_INTERVAL
    _attr_native_max_value = MAX_FEEDING_INTERVAL
    _attr_native_step = FEEDING_INTERVAL_STEP
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:clock-outline"

    def __init__(self, runtime: ChildRuntime) -> None:
        """Initialize the interval number entity."""
        super().__init__(
            runtime,
            f"{runtime.prefix}_feeding_interval_hours",
            "feeding_interval_hours",
            f"{runtime.name} feeding interval",
        )

    @property
    def native_value(self) -> float | None:
        """Return the current interval."""
        return self._tracker().interval_hours

    async def async_set_native_value(self, value: float) -> None:
        """Persist the new interval; feeding-due updates immediately."""
        await self._runtime.store.async_set_interval(float(value))


class FeedingDueBinarySensor(ActivityTrackerEntity, BinarySensorEntity):
    """ON once now >= last feeding + interval + 10 min grace."""

    _attr_icon = "mdi:baby-bottle"

    def __init__(self, runtime: ChildRuntime) -> None:
        """Initialize the due binary sensor."""
        super().__init__(
            runtime,
            f"{runtime.prefix}_feeding_due",
            "feeding_due",
            f"{runtime.name} feeding due",
        )

    @property
    def is_on(self) -> bool | None:
        """Return the last computed due state."""
        return self._runtime.monitor.is_due

    @property
    def extra_state_attributes(self) -> dict[str, str | float | None]:
        """Expose the inputs of the computation for templates."""
        tracker = self._tracker()
        last = tracker.activities["feeding"].last
        due_at = tracker.feeding_due_at()
        return {
            "last_feeding": last.isoformat() if last else None,
            "due_at": due_at.isoformat() if due_at else None,
            "interval_hours": tracker.interval_hours,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to due-state changes."""
        self.async_on_remove(
            self._runtime.monitor.async_add_listener(self._async_due_changed)
        )

    @callback
    def _async_due_changed(self) -> None:
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


def _runtime(hass: HomeAssistant, entry: ConfigEntry) -> ChildRuntime:
    runtime: ChildRuntime = hass.data[DOMAIN][entry.entry_id]
    return runtime


async def async_setup_button(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the log/undo buttons for all activities."""
    runtime = _runtime(hass, entry)
    async_add_entities(
        [
            *(ActivityButton(runtime, activity) for activity in ACTIVITIES),
            *(ActivityUndoButton(runtime, activity) for activity in ACTIVITIES),
        ]
    )


async def async_setup_sensor(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the total/today/last/daily-avg sensors."""
    runtime = _runtime(hass, entry)
    async_add_entities(
        [
            *(ActivityTotalSensor(runtime, activity) for activity in ACTIVITIES),
            *(ActivityTodaySensor(runtime, activity) for activity in ACTIVITIES),
            *(ActivityLastSensor(runtime, activity) for activity in ACTIVITIES),
            *(ActivityDailyAvgSensor(runtime, activity) for activity in ACTIVITIES),
        ]
    )


async def async_setup_number(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the feeding interval number entity."""
    async_add_entities([FeedingIntervalNumber(_runtime(hass, entry))])


async def async_setup_binary_sensor(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the feeding-due binary sensor."""
    async_add_entities([FeedingDueBinarySensor(_runtime(hass, entry))])
