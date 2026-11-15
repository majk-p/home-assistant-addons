"""The Activity Tracker integration: one config entry per child.

Setup wires a config entry to:
- a per-child storage document (see coordinator.py),
- one device per child, so the entities group in the UI,
- the entity platforms (button, sensor, number, binary_sensor),
- a local-midnight listener (today-counter rollover, DST safe) and a
  60 s periodic feeding-due check (both cancelled automatically on unload
  via ``entry.async_on_unload``),
- the dashboard strategy resource (see frontend/): served over HTTP and
  auto-loaded by the frontend, once per Home Assistant run, so installing
  the integration provides its dashboard without any pasted YAML.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
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

_LOGGER = logging.getLogger(__name__)

#: URL the strategy script is served at (and auto-loaded from).
_MANIFEST = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
MANIFEST_VERSION: Final = _MANIFEST["version"]

# Version-stamped URL: every release is a fresh URL, so no browser, proxy or
# webview can ever serve a stale copy of the strategy from cache (a fixed URL
# with heuristic caching once left an old script running after an update).
STRATEGY_JS_URL: Final = f"/activity_tracker/strategy-{MANIFEST_VERSION}.js"

#: hass.data[DOMAIN] flag guarding the once-per-run frontend registration.
_FRONTEND_REGISTERED = "frontend_registered"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve and auto-load the dashboard strategy, once per HA run.

    1. Serves the strategy script as a static path under a version-stamped
       URL (``/activity_tracker/strategy-<version>.js``, long-cacheable:
       each release is a new URL, so caches can never serve a stale copy).
    2. Registers it with ``frontend.add_extra_js_url``, which injects it as
       a module into the frontend of every dashboard: the strategy element
       defines itself and (on 2026.5+) its picker metadata on load.

    Home Assistant core always sets up the ``http`` and ``frontend``
    components before custom integration entries; the existence checks
    keep headless setups without them (the test harness) working.
    Static paths cannot be unregistered, and keeping the module
    registered across entry removals is the friendlier behaviour, so the
    registration deliberately stays for the rest of the run.
    """
    if hass.data[DOMAIN].get(_FRONTEND_REGISTERED):
        return
    if hass.http is None or frontend.DATA_EXTRA_MODULE_URL not in hass.data:
        # Home Assistant core always sets up ``http`` and ``frontend``
        # before custom integration entries; headless test setups may
        # have neither — skip and try again on the next entry rather
        # than fail child setup over the dashboard.
        _LOGGER.debug(
            "http/frontend not set up; deferring registration of %s",
            STRATEGY_JS_URL,
        )
        return
    strategy_js = Path(__file__).parent / "frontend" / "activity-tracker-strategy.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STRATEGY_JS_URL, str(strategy_js), True)]
    )
    frontend.add_extra_js_url(hass, STRATEGY_JS_URL)
    hass.data[DOMAIN][_FRONTEND_REGISTERED] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one child from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    runtime = await ChildRuntime.async_create(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = runtime

    await _async_register_frontend(hass)

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
