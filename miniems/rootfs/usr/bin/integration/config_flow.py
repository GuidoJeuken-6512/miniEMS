"""Config flow for miniEMS integration."""
from __future__ import annotations

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from . import (
    CONF_BASE_URL,
    CONF_POLL_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str}
)


async def _test_connection(base_url: str) -> bool:
    """Return True if GET /api/status responds with HTTP 200."""
    url = f"{base_url.rstrip('/')}/api/status"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


class MiniEMSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial config flow for miniEMS."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            if not base_url.startswith(("http://", "https://")):
                errors[CONF_BASE_URL] = "invalid_url"
            elif not await _test_connection(base_url):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="miniEMS",
                    data={CONF_BASE_URL: base_url},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={"default_url": DEFAULT_BASE_URL},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> MiniEMSOptionsFlow:
        return MiniEMSOptionsFlow(config_entry)


class MiniEMSOptionsFlow(config_entries.OptionsFlow):
    """Handle options (poll interval) for miniEMS."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_POLL_INTERVAL, default=current): vol.All(
                    int, vol.Range(min=10, max=300)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
