"""Actions for ANWB Laadpas."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import DOMAIN

SERVICE_GET_SESSIONS = "get_sessions"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_START = "start"
ATTR_END = "end"
ATTR_CARD = "card"
ATTR_LIMIT = "limit"

GET_SESSIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_START): vol.Any(cv.datetime, cv.date),
        vol.Optional(ATTR_END): vol.Any(cv.datetime, cv.date),
        vol.Optional(ATTR_CARD): cv.string,
        vol.Optional(ATTR_LIMIT): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)


def _as_local_datetime(value: date | datetime, end_of_day: bool) -> datetime:
    if isinstance(value, datetime):
        return (
            dt_util.as_local(value)
            if value.tzinfo
            else value.replace(tzinfo=dt_util.get_default_time_zone())
        )
    moment = time.max if end_of_day else time.min
    return datetime.combine(value, moment, tzinfo=dt_util.get_default_time_zone())


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions of the integration."""

    async def _get_sessions(call: ServiceCall) -> ServiceResponse:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
            and call.data.get(ATTR_CONFIG_ENTRY_ID, entry.entry_id) == entry.entry_id
        ]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )

        start = call.data.get(ATTR_START)
        end = call.data.get(ATTR_END)
        start_dt = _as_local_datetime(start, end_of_day=False) if start else None
        end_dt = _as_local_datetime(end, end_of_day=True) if end else None
        card = call.data.get(ATTR_CARD, "").strip().casefold()

        rows: list[dict[str, Any]] = []
        for entry in entries:
            coordinator = entry.runtime_data
            for session in coordinator.data.sessions:
                if start_dt and session.start < start_dt:
                    continue
                if end_dt and session.start > end_dt:
                    continue
                name = coordinator.card_name(session.card_id)
                if card and card not in {
                    (session.card_id or "").casefold(),
                    (session.card_number or "").casefold(),
                    (name or "").casefold(),
                }:
                    continue
                rows.append(session.as_row(name))

        rows.sort(key=lambda row: row["start"], reverse=True)
        if limit := call.data.get(ATTR_LIMIT):
            rows = rows[:limit]
        return {
            "count": len(rows),
            "energy_kwh": round(sum(row["energy_kwh"] for row in rows), 3),
            "cost": round(sum(row["cost"] or 0.0 for row in rows), 2),
            "sessions": rows,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SESSIONS,
        _get_sessions,
        schema=GET_SESSIONS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
