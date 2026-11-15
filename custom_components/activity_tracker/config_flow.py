"""Config flow and options flow for Activity Tracker.

One config entry = one child. The entry title shows name and prefix
("Robert (rob)"). The entry unique id is the prefix, so the same child
cannot be added twice while its entry exists.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    CONF_FEEDING_INTERVAL,
    CONF_NAME,
    CONF_NOTIFY_TARGETS,
    CONF_PREFIX,
    DEFAULT_FEEDING_INTERVAL,
    DOMAIN,
    MAX_FEEDING_INTERVAL,
    MIN_FEEDING_INTERVAL,
)

_NOTIFY_TARGET_RE = re.compile(r"^notify\.[a-z0-9_]+$")


def sanitize_prefix(raw: str) -> str:
    """Return a [a-z0-9_] prefix (HA slugify defaults to 'unknown')."""
    return slugify(raw.strip()) or "child"


def parse_targets(raw: str) -> list[str]:
    """Split a comma-separated entity id list, dropping empties."""
    return [target.strip() for target in (raw or "").split(",") if target.strip()]


def validate(
    user_input: dict[str, Any],
) -> tuple[str, str, float, list[str], dict[str, str]]:
    """Validate a submitted form.

    Returns (name, prefix, interval, targets, errors); errors is empty
    when the input is valid.
    """
    errors: dict[str, str] = {}
    name = str(user_input.get(CONF_NAME, "")).strip()
    prefix = sanitize_prefix(str(user_input.get(CONF_PREFIX) or name))
    targets = parse_targets(str(user_input.get(CONF_NOTIFY_TARGETS) or ""))
    try:
        interval = float(user_input[CONF_FEEDING_INTERVAL])
    except (KeyError, TypeError, ValueError):
        interval = DEFAULT_FEEDING_INTERVAL

    if not name:
        errors[CONF_NAME] = "invalid_name"
    elif not (MIN_FEEDING_INTERVAL <= interval <= MAX_FEEDING_INTERVAL):
        errors[CONF_FEEDING_INTERVAL] = "invalid_interval"
    elif any(not _NOTIFY_TARGET_RE.match(target) for target in targets):
        errors[CONF_NOTIFY_TARGETS] = "invalid_targets"
    return name, prefix, interval, targets, errors


def step_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the shared user/options form schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, description={"suggested_value": defaults.get(CONF_NAME)}
            ): str,
            vol.Optional(
                CONF_PREFIX, description={"suggested_value": defaults.get(CONF_PREFIX)}
            ): str,
            vol.Required(
                CONF_FEEDING_INTERVAL,
                default=defaults.get(CONF_FEEDING_INTERVAL, DEFAULT_FEEDING_INTERVAL),
            ): vol.All(
                vol.Coerce(float),
                vol.Range(min=MIN_FEEDING_INTERVAL, max=MAX_FEEDING_INTERVAL),
            ),
            vol.Optional(
                CONF_NOTIFY_TARGETS,
                description={
                    "suggested_value": ", ".join(defaults.get(CONF_NOTIFY_TARGETS) or [])
                },
            ): str,
        }
    )


class ActivityTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Activity Tracker config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a child."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name, prefix, interval, targets, errors = validate(user_input)
            if not errors:
                await self.async_set_unique_id(prefix)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{name} ({prefix})",
                    data={
                        CONF_NAME: name,
                        CONF_PREFIX: prefix,
                        CONF_FEEDING_INTERVAL: interval,
                        CONF_NOTIFY_TARGETS: targets,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=step_schema(user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: Any,
    ) -> ActivityTrackerOptionsFlow:
        """Return the options flow for an entry."""
        return ActivityTrackerOptionsFlow()


class ActivityTrackerOptionsFlow(OptionsFlow):
    """Edit name, prefix, feeding interval and notify targets.

    Changing name/prefix/targets rewrites the entry data and reloads the
    entry (entities and the device are re-created; a prefix change moves
    them to new entity ids — long-term statistics follow entity ids, so
    rename only when needed). The feeding interval is written through the
    number entity when it exists — the entity is the source of truth —
    and stored in the entry only for (re-)creation.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        entry = self.config_entry
        if user_input is not None:
            name, prefix, interval, targets, errors = validate(user_input)
            if not errors and prefix != entry.data[CONF_PREFIX]:
                if any(
                    other.data.get(CONF_PREFIX) == prefix
                    for other in self.hass.config_entries.async_entries(DOMAIN)
                    if other.entry_id != entry.entry_id
                ):
                    errors[CONF_PREFIX] = "duplicate_prefix"
            if not errors:
                old_prefix: str = entry.data[CONF_PREFIX]
                number_entity_id = er.async_get(self.hass).async_get_entity_id(
                    "number", DOMAIN, f"{old_prefix}_feeding_interval_hours"
                )
                if number_entity_id is not None:
                    await self.hass.services.async_call(
                        "number",
                        "set_value",
                        {"value": interval},
                        target={"entity_id": number_entity_id},
                        blocking=True,
                    )
                data = {
                    CONF_NAME: name,
                    CONF_PREFIX: prefix,
                    # Keep the entry value unchanged while the number
                    # entity exists; it holds the live value.
                    CONF_FEEDING_INTERVAL: (
                        float(entry.data[CONF_FEEDING_INTERVAL])
                        if number_entity_id is not None
                        else interval
                    ),
                    CONF_NOTIFY_TARGETS: targets,
                }
                if data != dict(entry.data):
                    self.hass.config_entries.async_update_entry(
                        entry, data=data, title=f"{name} ({prefix})"
                    )
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="init",
            data_schema=step_schema(dict(entry.data)),
            errors=errors,
        )
