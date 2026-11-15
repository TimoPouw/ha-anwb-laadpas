"""The ANWB Laadpas integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import AnwbLaadpasClient, ApiError, AuthConfig, async_discover_auth_config
from .const import (
    CONF_AUTH_CONFIG,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    STORAGE_VERSION,
)
from .coordinator import AnwbLaadpasCoordinator
from .entity import card_device_identifier, card_device_name

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type AnwbLaadpasConfigEntry = ConfigEntry[AnwbLaadpasCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AnwbLaadpasConfigEntry) -> bool:
    """Set up ANWB Laadpas from a config entry."""
    session = async_get_clientsession(hass)
    auth_config = AuthConfig.from_dict(entry.data[CONF_AUTH_CONFIG])

    # Pick up changes to the client id, endpoints or tenant published by the
    # web app. Keep using the stored values if discovery is not possible.
    try:
        discovered = await async_discover_auth_config(session)
    except ApiError as err:
        _LOGGER.debug("Discovery failed, using stored auth config: %s", err)
    else:
        if discovered != auth_config:
            _LOGGER.info("ANWB Laadpas auth configuration changed, updating entry")
            auth_config = discovered
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_AUTH_CONFIG: discovered.as_dict()}
            )

    def _store_refresh_token(token: str) -> None:
        # The identity server rotates refresh tokens: persist every new one.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
        )

    client = AnwbLaadpasClient(
        session, auth_config, entry.data[CONF_REFRESH_TOKEN], _store_refresh_token
    )
    coordinator = AnwbLaadpasCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: AnwbLaadpasConfigEntry
) -> None:
    """Apply changed options without a reload.

    This also runs when only the refresh token changed, so it must stay cheap.
    """
    coordinator = entry.runtime_data
    interval = timedelta(
        minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN)
    )
    if coordinator.update_interval != interval:
        coordinator.update_interval = interval

    device_registry = dr.async_get(hass)
    for card_id, card_number in (
        coordinator.data.cards if coordinator.data else {}
    ).items():
        device = device_registry.async_get_device(
            identifiers={card_device_identifier(entry, card_id)}
        )
        name = card_device_name(coordinator, card_id, card_number)
        if device and device.name != name:
            device_registry.async_update_device(device.id, name=name)


async def async_unload_entry(
    hass: HomeAssistant, entry: AnwbLaadpasConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: AnwbLaadpasConfigEntry
) -> None:
    """Remove stored session data when the entry is deleted."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
