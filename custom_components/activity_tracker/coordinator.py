"""Per-child state: pure data model, storage, and the feeding monitor.

Persistence pattern
-------------------
Per-child state lives in one Home Assistant storage document
(``.storage/activity_tracker_<prefix>``) managed by ``helpers.storage.Store``.
The alternatives were deliberately not taken:

- ``RestoreEntity`` extra states: the state of one child is spread across
  ~26 entities; totals, today counters, first/last timestamps and
  notification bookkeeping would restore independently and could drift
  after crashes. There is also no natural entity to own cross-cutting
  values such as "first event ever" or "last overdue notification sent".
- ``DataUpdateCoordinator``: nothing here is polled. Every change is a
  local push (a button press), so a coordinator would only ever act as a
  change bus with polling semantics nobody needs.

A single storage document makes the data model one plain dict: pressing a
button, undoing, the midnight rollover, restart-restore and future
migrations are simple, testable mutations of that dict, and the entities
are dumb views that re-render on change. ``Store`` debounces writes and
flushes them on shutdown; the file is small (a few hundred bytes) so a
save is triggered after every event.

Entity ids derive from the child prefix, so long-term statistics (keyed
by entity id) survive even if the config entry is deleted and re-created
with the same prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import storage
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVITIES,
    CONF_FEEDING_INTERVAL,
    CONF_NAME,
    CONF_NOTIFY_TARGETS,
    CONF_PREFIX,
    DEFAULT_FEEDING_INTERVAL,
    DOMAIN,
    FEEDING_GRACE,
    NOTIFICATION_MESSAGES,
    NOTIFY_REPEAT,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
    notification_language,
)

_LOGGER = logging.getLogger(__name__)

NOTIFY_SERVICE = ("notify", "send_message")


# ---------------------------------------------------------------------------
# Pure data model (no Home Assistant objects needed to use or test it)
# ---------------------------------------------------------------------------


@dataclass
class ActivityState:
    """Counters and timestamps for a single activity."""

    total: int = 0
    today: int = 0
    last: datetime | None = None
    first: datetime | None = None


@dataclass
class TrackerData:
    """All persisted state of one child."""

    activities: dict[str, ActivityState] = field(
        default_factory=lambda: {name: ActivityState() for name in ACTIVITIES}
    )
    interval_hours: float = DEFAULT_FEEDING_INTERVAL
    notify_last_sent: datetime | None = None
    today_date: date | None = None

    def log_event(self, activity: str, now: datetime) -> None:
        """Record "this happened now"."""
        state = self.activities[activity]
        state.total += 1
        state.today += 1
        state.last = now
        if state.first is None:
            state.first = now
        self.today_date = now.date()

    def undo_event(self, activity: str) -> None:
        """Undo the last event: decrement both counters, floored at 0.

        The last/first timestamps intentionally stay as they are (the undo
        of a mis-press does not move history); a documented side effect is
        the total-increasing statistics blip described in the README.
        """
        state = self.activities[activity]
        state.total = max(0, state.total - 1)
        state.today = max(0, state.today - 1)

    def reset_today(self, today: date) -> None:
        """Zero the today counters (local midnight rollover)."""
        self.today_date = today
        for state in self.activities.values():
            state.today = 0

    def completed_days(self, activity: str, today: date) -> int:
        """Completed days since the first event ever, at least 1."""
        state = self.activities[activity]
        if state.first is None:
            return 1
        return max(1, (today - state.first.date()).days)

    def daily_avg(self, activity: str, today: date) -> float:
        """Lifetime total per completed day, rounded to 1 decimal."""
        state = self.activities[activity]
        if state.total == 0:
            return 0.0
        return round(state.total / self.completed_days(activity, today), 1)

    def feeding_due_at(self) -> datetime | None:
        """When the next feeding becomes overdue, or None if never fed."""
        state = self.activities["feeding"]
        if state.last is None:
            return None
        return state.last + timedelta(hours=self.interval_hours) + FEEDING_GRACE

    def feeding_due(self, now: datetime) -> bool:
        """True once now >= last feeding + interval + grace.

        With no feeding logged yet there is nothing to be overdue against,
        so the sensor stays OFF until the first press (see README).
        """
        due_at = self.feeding_due_at()
        return due_at is not None and now >= due_at

    # -- (de)serialization -------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialize for the storage helper (datetimes as ISO strings)."""

        def _dt(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "activities": {
                name: {
                    "total": state.total,
                    "today": state.today,
                    "last": _dt(state.last),
                    "first": _dt(state.first),
                }
                for name, state in self.activities.items()
            },
            "interval_hours": self.interval_hours,
            "notify_last_sent": _dt(self.notify_last_sent),
            "today_date": self.today_date.isoformat() if self.today_date else None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> TrackerData:
        """Load, tolerating missing keys (documents grow over versions)."""
        tracker = cls()
        if not data:
            return tracker
        for name, raw in (data.get("activities") or {}).items():
            if name not in tracker.activities:
                continue
            tracker.activities[name] = ActivityState(
                total=int(raw.get("total", 0)),
                today=int(raw.get("today", 0)),
                last=_parse_dt(raw.get("last")),
                first=_parse_dt(raw.get("first")),
            )
        if (interval := data.get("interval_hours")) is not None:
            tracker.interval_hours = float(interval)
        tracker.notify_last_sent = _parse_dt(data.get("notify_last_sent"))
        if (today := data.get("today_date")) is not None:
            tracker.today_date = date.fromisoformat(today)
        return tracker


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def humanize_duration(delta: timedelta) -> str:
    """Compact duration for notifications: '2d 3h', '3h 20m', '45m'."""
    minutes = max(0, int(delta.total_seconds() // 60))
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Home Assistant glue
# ---------------------------------------------------------------------------


class ChildStore:
    """Owns the per-child storage document and notifies listeners on change."""

    def __init__(self, hass: HomeAssistant, prefix: str) -> None:
        """Initialize the store for one child."""
        self._hass = hass
        self.prefix = prefix
        self.tracker = TrackerData()
        self._listeners: list[Callable[[], None]] = []
        self._store = storage.Store(
            hass,
            STORAGE_VERSION,
            self.storage_key,
            minor_version=STORAGE_MINOR_VERSION,
        )

    @property
    def storage_key(self) -> str:
        """Storage key (also the file name under .storage/)."""
        return f"{DOMAIN}_{self.prefix}"

    async def async_load(self) -> None:
        """Load the document.

        STORAGE_VERSION is 1; when it is ever bumped, load with the old
        major version via a second ``Store`` object, transform the dict
        with ``TrackerData.from_json`` and ``async_save`` it back before
        constructing the new-version store.
        """
        data = await self._store.async_load()
        self.tracker = TrackerData.from_json(data)

    async def async_remove(self) -> None:
        """Delete the storage document (config entry removal)."""
        await self._store.async_remove()

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired after every data change."""

        def _remove_listener() -> None:
            self._listeners.remove(update_callback)

        self._listeners.append(update_callback)
        return _remove_listener

    async def _async_commit(self) -> None:
        """Persist the tracker and notify the listeners."""
        await self._store.async_save(self.tracker.to_json())
        for listener in self._listeners:
            listener()

    async def async_log_event(self, activity: str, now: datetime | None = None) -> None:
        """Handle a button press: an event happened now."""
        self.tracker.log_event(activity, now or dt_util.now())
        await self._async_commit()

    async def async_undo_event(self, activity: str) -> None:
        """Handle an undo button press."""
        self.tracker.undo_event(activity)
        await self._async_commit()

    async def async_reset_today(self, today: date | None = None) -> None:
        """Zero the today counters for a new local day."""
        self.tracker.reset_today(today or dt_util.now().date())
        await self._async_commit()

    async def async_set_interval(self, hours: float) -> None:
        """Persist a new feeding interval (the number entity is the source)."""
        self.tracker.interval_hours = float(hours)
        await self._async_commit()

    async def async_mark_notified(self, now: datetime) -> None:
        """Record that the overdue notification was just sent."""
        self.tracker.notify_last_sent = now
        await self._async_commit()


class FeedingMonitor:
    """Computes the feeding-due state and sends overdue notifications.

    Recomputed on every store change (press, undo, interval edit) and by
    the 60 s periodic check registered in ``__init__.py``. On a transition
    to due (and at most hourly while due) it calls ``notify.send_message``
    for each configured target. A restart that lands inside the repeat
    window does not re-notify.
    """

    def __init__(self, hass: HomeAssistant, runtime: ChildRuntime) -> None:
        """Initialize the monitor for one child."""
        self._hass = hass
        self._runtime = runtime
        self._is_due: bool | None = None
        self._sensor_listeners: list[Callable[[], None]] = []
        runtime.store.async_add_listener(self._async_store_changed)

    @property
    def is_due(self) -> bool:
        """Last computed due state."""
        return bool(self._is_due)

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired when the due state changes."""

        def _remove_listener() -> None:
            self._sensor_listeners.remove(update_callback)

        self._sensor_listeners.append(update_callback)
        return _remove_listener

    @callback
    def _async_store_changed(self) -> None:
        # Any data change (feeding press, interval edit, ...) can flip the
        # due state immediately; do not wait for the periodic check.
        self._hass.async_create_task(self.async_recheck())

    async def async_recheck(self, now: datetime | None = None) -> None:
        """Recompute due, update listeners and maybe notify.

        ``now`` defaults to the current time; the periodic listener passes
        the time it fired at.
        """
        now = now or dt_util.now()
        tracker = self._runtime.store.tracker
        was_due = self._is_due
        due = tracker.feeding_due(now)
        self._is_due = due
        # Fire on any change, including the very first check (None -> bool),
        # so the binary sensor renders a persisted overdue state at startup.
        if was_due is None or due != was_due:
            for listener in self._sensor_listeners:
                listener()

        if not due or not self._runtime.notify_targets:
            return

        became_due = was_due is not True
        repeat_due = (
            tracker.notify_last_sent is None
            or now - tracker.notify_last_sent >= NOTIFY_REPEAT
        )
        if not (became_due or repeat_due):
            return
        if (
            was_due is None
            and tracker.notify_last_sent is not None
            and now - tracker.notify_last_sent < NOTIFY_REPEAT
        ):
            # Restarted while already overdue and notified recently.
            return

        await self._async_send_notifications(now)

    async def _async_send_notifications(self, now: datetime) -> None:
        """Call notify.send_message for every configured target.

        The message follows the Home Assistant profile language (exact
        language-code match, then primary subtag, else English); see
        NOTIFICATION_MESSAGES in const.py.
        """
        tracker = self._runtime.store.tracker
        last = tracker.activities["feeding"].last
        assert last is not None  # guarded by feeding_due()
        template = NOTIFICATION_MESSAGES[
            notification_language(self._hass.config.language)
        ]
        message = template.format(
            name=self._runtime.name,
            ago=humanize_duration(now - last),
            interval=f"{tracker.interval_hours:g}",
        )
        for target in self._runtime.notify_targets:
            if self._hass.states.get(target) is None:
                _LOGGER.warning(
                    "%s: notification target %s does not exist, skipping",
                    DOMAIN,
                    target,
                )
                continue
            try:
                await self._hass.services.async_call(
                    NOTIFY_SERVICE[0],
                    NOTIFY_SERVICE[1],
                    {"message": message},
                    target={"entity_id": target},
                    blocking=False,
                )
            except HomeAssistantError as exc:  # e.g. notify not set up
                _LOGGER.warning("%s: calling %s failed: %s", DOMAIN, target, exc)
        await self._runtime.store.async_mark_notified(now)


class ChildRuntime:
    """Everything a config entry (one child) needs while it is loaded."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: ChildStore) -> None:
        """Initialize the runtime for one child."""
        self.hass = hass
        self.entry = entry
        self.store = store
        self.monitor = FeedingMonitor(hass, self)

    @classmethod
    async def async_create(cls, hass: HomeAssistant, entry: ConfigEntry) -> ChildRuntime:
        """Load storage and prepare the runtime for one child."""
        prefix: str = entry.data[CONF_PREFIX]
        store = ChildStore(hass, prefix)
        await store.async_load()

        # The number entity owns the feeding interval once it exists. If it
        # was deleted, the config entry value is the seed for re-creation.
        registry = er.async_get(hass)
        if (
            registry.async_get_entity_id("number", DOMAIN, f"{prefix}_feeding_interval_hours")
            is None
        ):
            wanted = float(entry.data.get(CONF_FEEDING_INTERVAL, DEFAULT_FEEDING_INTERVAL))
            if store.tracker.interval_hours != wanted:
                await store.async_set_interval(wanted)

        runtime = cls(hass, entry, store)

        # If HA was down across local midnight, roll the today counters.
        # (The live rollover is a local-midnight listener in __init__.py.)
        today = dt_util.now().date()
        if store.tracker.today_date != today:
            await store.async_reset_today(today)

        return runtime

    @property
    def prefix(self) -> str:
        """Entity-id prefix of this child."""
        return str(self.entry.data[CONF_PREFIX])

    @property
    def name(self) -> str:
        """Display name of this child."""
        return str(self.entry.data[CONF_NAME])

    @property
    def notify_targets(self) -> list[str]:
        """Configured notify targets (may be empty)."""
        return list(self.entry.data.get(CONF_NOTIFY_TARGETS) or [])

    @callback
    def async_midnight(self, now: datetime) -> None:
        """Handle local midnight: zero the today counters.

        ``async_track_time_change`` fires in local time, so DST shifts are
        handled by Home Assistant's scheduler.
        """
        if self.store.tracker.today_date != now.date():
            self.hass.async_create_task(self.store.async_reset_today(now.date()))

    async def async_periodic_check(self, now: datetime) -> None:
        """Periodic feeding-due re-check (60 s), at the fired time."""
        await self.monitor.async_recheck(now)


__all__ = [
    "ActivityState",
    "ChildRuntime",
    "ChildStore",
    "FeedingMonitor",
    "TrackerData",
    "humanize_duration",
]
