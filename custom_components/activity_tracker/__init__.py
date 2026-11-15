"""The Activity Tracker integration: one config entry per child.

Setup wires a config entry to:
- a per-child storage document (see coordinator.py),
- one device per child, so the entities group in the UI,
- the entity platforms (button, sensor, number, binary_sensor),
- a local-midnight listener (today-counter rollover, DST safe) and a
  60 s periodic feeding-due check (both cancelled automatically on unload
  via ``entry.async_on_unload``).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)

from .const import (
    CONF_PREFIX,
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    DUE_CHECK_INTERVAL,
    entity_unique_ids,
)
from .coordinator import ChildRuntime, ChildStore

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BINARY_SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one child from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    runtime = await ChildRuntime.async_create(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = runtime

    # One device per child groups all entities of the entry.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, runtime.prefix)},
        name=runtime.name,
    )
    # A prefix change in the options flow creates entities with new unique
    # ids; drop the stale registry leftovers so no ghosts linger.
    _async_remove_stale(hass, entry, runtime.prefix)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        async_track_time_change(
            hass, runtime.async_midnight, hour=0, minute=0, second=0
        )
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass, runtime.async_periodic_check, DUE_CHECK_INTERVAL
        )
    )
    # Initial feeding-due computation (may send a throttled notification if
    # HA was down while a feeding became overdue).
    await runtime.monitor.async_recheck()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one child (listeners are cancelled by async_on_unload)."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the child's device and storage document on entry removal."""
    prefix = entry.data.get(CONF_PREFIX)
    if prefix:
        await ChildStore(hass, str(prefix)).async_remove()
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry):
        device_registry.async_remove_device(device.id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry versions to the current one."""
    if entry.version > CONFIG_ENTRY_VERSION:
        # Unknown future version: do not touch it.
        return False
    hass.config_entries.async_update_entry(
        entry,
        minor_version=CONFIG_ENTRY_MINOR_VERSION,
        version=CONFIG_ENTRY_VERSION,
    )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its data changes (options flow)."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_remove_stale(
    hass: HomeAssistant, entry: ConfigEntry, prefix: str
) -> None:
    """Remove entities/devices from a previous prefix of this entry."""
    valid = entity_unique_ids(prefix)
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry):
        if reg_entry.unique_id not in valid:
            registry.async_remove(reg_entry.entity_id)
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry):
        if (DOMAIN, prefix) not in device.identifiers:
            device_registry.async_remove_device(device.id)
