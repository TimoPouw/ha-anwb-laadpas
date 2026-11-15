"""Diagnostics for ANWB Laadpas."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import AnwbLaadpasConfigEntry
from .const import CONF_CARD_NAMES, CONF_REFRESH_TOKEN

TO_REDACT = {
    CONF_REFRESH_TOKEN,
    CONF_CARD_NAMES,
    "unique_id",
    "title",
    "card_id",
    "card_number",
    "card_name",
    "location_name",
    "address",
    "city",
    "postal_code",
    "evse_id",
    "session_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AnwbLaadpasConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with tokens and personal data removed."""
    coordinator = entry.runtime_data
    data = coordinator.data
    last = data.last() if data else None
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
        },
        "data": {
            "session_count": len(data.sessions) if data else 0,
            "card_count": len(data.cards) if data else 0,
            "sessions_per_card": [len(data.for_card(card_id)) for card_id in data.cards]
            if data
            else [],
            "sessions_without_cost": sum(1 for s in data.sessions if s.cost is None)
            if data
            else 0,
            "sessions_without_end": sum(1 for s in data.sessions if s.end is None)
            if data
            else 0,
            "last_session": async_redact_data(last.as_event_data(None), TO_REDACT)
            if last
            else None,
        },
    }
