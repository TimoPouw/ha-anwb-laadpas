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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import ApiError, AuthError
from .const import DOMAIN
from .invoices import INVOICE, SPECIFICATION, async_save_invoice

SERVICE_GET_SESSIONS = "get_sessions"
SERVICE_GET_INVOICES = "get_invoices"
SERVICE_DOWNLOAD_INVOICES = "download_invoices"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_START = "start"
ATTR_END = "end"
ATTR_CARD = "card"
ATTR_LIMIT = "limit"
ATTR_UNPAID_ONLY = "unpaid_only"
ATTR_INVOICE = "invoice"
ATTR_KIND = "kind"
ATTR_OVERWRITE = "overwrite"
KIND_BOTH = "both"

GET_SESSIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_START): vol.Any(cv.datetime, cv.date),
        vol.Optional(ATTR_END): vol.Any(cv.datetime, cv.date),
        vol.Optional(ATTR_CARD): cv.string,
        vol.Optional(ATTR_LIMIT): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)


_PERIOD_SCHEMA = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(ATTR_START): vol.Any(cv.datetime, cv.date),
    vol.Optional(ATTR_END): vol.Any(cv.datetime, cv.date),
}
GET_INVOICES_SCHEMA = vol.Schema(
    {
        **_PERIOD_SCHEMA,
        vol.Optional(ATTR_UNPAID_ONLY, default=False): cv.boolean,
        vol.Optional(ATTR_LIMIT): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)
DOWNLOAD_INVOICES_SCHEMA = vol.Schema(
    {
        **_PERIOD_SCHEMA,
        vol.Optional(ATTR_INVOICE): cv.string,
        vol.Optional(ATTR_KIND, default=KIND_BOTH): vol.In(
            [INVOICE, SPECIFICATION, KIND_BOTH]
        ),
        vol.Optional(ATTR_OVERWRITE, default=False): cv.boolean,
    }
)


def _period(call: ServiceCall) -> tuple[datetime | None, datetime | None]:
    start = call.data.get(ATTR_START)
    end = call.data.get(ATTR_END)
    return (
        _as_local_datetime(start, end_of_day=False) if start else None,
        _as_local_datetime(end, end_of_day=True) if end else None,
    )


def _in_period(
    moment: datetime | None, start: datetime | None, end: datetime | None
) -> bool:
    if moment is None:
        return start is None and end is None
    return not ((start and moment < start) or (end and moment > end))


def _loaded_entries(hass: HomeAssistant, call: ServiceCall) -> list[Any]:
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
    return entries


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
        entries = _loaded_entries(hass, call)
        start_dt, end_dt = _period(call)
        card = call.data.get(ATTR_CARD, "").strip().casefold()

        rows: list[dict[str, Any]] = []
        for entry in entries:
            coordinator = entry.runtime_data
            for session in coordinator.data.sessions:
                if not _in_period(session.start, start_dt, end_dt):
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

    async def _get_invoices(call: ServiceCall) -> ServiceResponse:
        start_dt, end_dt = _period(call)
        rows: list[dict[str, Any]] = []
        for entry in _loaded_entries(hass, call):
            for invoice in entry.runtime_data.data.invoices:
                if not _in_period(invoice.date, start_dt, end_dt):
                    continue
                if call.data[ATTR_UNPAID_ONLY] and invoice.is_paid:
                    continue
                rows.append(invoice.as_row())
        rows.sort(key=lambda row: row["date"] or "", reverse=True)
        if limit := call.data.get(ATTR_LIMIT):
            rows = rows[:limit]
        return {
            "count": len(rows),
            "amount_incl_vat": round(sum(r["amount_incl_vat"] or 0 for r in rows), 2),
            "open_amount": round(
                sum(r["amount_incl_vat"] or 0 for r in rows if not r["paid"]), 2
            ),
            "invoices": rows,
        }

    async def _download_invoices(call: ServiceCall) -> ServiceResponse:
        start_dt, end_dt = _period(call)
        number = call.data.get(ATTR_INVOICE, "").strip()
        kind = call.data[ATTR_KIND]
        kinds = (INVOICE, SPECIFICATION) if kind == KIND_BOTH else (kind,)
        files: list[str] = []
        matched = 0
        for entry in _loaded_entries(hass, call):
            coordinator = entry.runtime_data
            for invoice in coordinator.data.invoices:
                if number and number not in (invoice.number, invoice.id):
                    continue
                if not _in_period(invoice.date, start_dt, end_dt) and not number:
                    continue
                if not invoice.has_pdf:
                    continue
                matched += 1
                try:
                    files += await async_save_invoice(
                        hass,
                        coordinator.client,
                        invoice,
                        kinds,
                        overwrite=call.data[ATTR_OVERWRITE],
                    )
                except AuthError as err:
                    entry.async_start_reauth(hass)
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="auth_failed"
                    ) from err
                except ApiError as err:
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="download_failed",
                        translation_placeholders={"invoice": invoice.number},
                    ) from err
        if number and not matched:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invoice_not_found",
                translation_placeholders={"invoice": number},
            )
        return {"count": matched, "files": files}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_INVOICES,
        _get_invoices,
        schema=GET_INVOICES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_INVOICES,
        _download_invoices,
        schema=DOWNLOAD_INVOICES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
