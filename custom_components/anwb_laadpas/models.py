"""Data models for ANWB Laadpas charging sessions and invoices."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return dt_util.parse_datetime(value)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class SessionDetails:
    """Extra fields from the session detail endpoint (one call per session)."""

    cost_excl_vat: float | None = None
    operator_name: str | None = None
    connector_standard: str | None = None
    connector_format: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    vehicle: str | None = None
    stop_reason: str | None = None

    @classmethod
    def from_api(cls, result: dict[str, Any]) -> SessionDetails:
        """Build from the GetChargingSessionForView result."""
        raw = result.get("chargingSession") or {}
        location = raw.get("location") or {}
        coordinates = location.get("coordinates") or {}
        vehicle = raw.get("vehicle") or {}
        vehicle_name = " ".join(
            part
            for part in (
                vehicle.get("manufacturerName"),
                vehicle.get("manufacturerModelName"),
                vehicle.get("manufacturerModelVersion"),
            )
            if part
        )
        return cls(
            cost_excl_vat=_float((raw.get("totalCosts") or {}).get("excl_vat")),
            operator_name=location.get("operatorName") or None,
            connector_standard=location.get("connectorStandard"),
            connector_format=location.get("connectorFormat"),
            latitude=_float(coordinates.get("latitude")),
            longitude=_float(coordinates.get("longitude")),
            vehicle=vehicle_name or None,
            stop_reason=raw.get("stopReason"),
        )


EMPTY_DETAILS = SessionDetails()


@dataclass(frozen=True, slots=True)
class ChargingSession:
    """A completed charging session."""

    id: str
    start: datetime
    end: datetime | None
    energy_kwh: float
    cost: float | None
    currency: str | None
    card_id: str | None
    card_number: str | None
    location_name: str | None
    address: str | None
    city: str | None
    postal_code: str | None
    country: str | None
    evse_id: str | None
    operator: str | None
    # None until the detail endpoint has been called for this session.
    details: SessionDetails | None = None

    def with_details(self, details: SessionDetails | None) -> ChargingSession:
        """Return a copy with the given details."""
        return replace(self, details=details)

    @property
    def duration(self) -> timedelta | None:
        """Return the duration of the session, if the end is known."""
        if self.end is None:
            return None
        return self.end - self.start

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> ChargingSession:
        """Build a session from one element of the GetAll result list."""
        raw = item.get("chargingSession") or item
        start = _parse_dt(raw.get("startDateTime"))
        if start is None:
            raise ValueError(f"Session {raw.get('id')} has no start time")
        token = raw.get("token") or {}
        location = raw.get("location") or {}
        costs = raw.get("totalCosts") or {}
        card_id = token.get("id")
        return cls(
            id=str(raw["id"]),
            start=start,
            end=_parse_dt(raw.get("endDateTime")),
            energy_kwh=float(raw.get("kwh") or 0.0),
            cost=costs.get("incl_vat"),
            currency=raw.get("currency"),
            card_id=str(card_id) if card_id is not None else None,
            card_number=token.get("emaId"),
            location_name=location.get("name"),
            address=location.get("address"),
            city=location.get("city"),
            postal_code=location.get("postalCode"),
            country=location.get("countryCode"),
            evse_id=location.get("evseId"),
            operator=location.get("partyId"),
        )

    def as_storage(self) -> dict[str, Any]:
        """Serialize for helpers.storage."""
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat() if self.end else None
        return data

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> ChargingSession:
        """Deserialize from helpers.storage."""
        details = data.get("details")
        return cls(
            **{
                **data,
                "start": _parse_dt(data["start"]),
                "end": _parse_dt(data.get("end")),
                "details": SessionDetails(**details) if details is not None else None,
            }
        )

    def as_event_data(self, card_name: str | None) -> dict[str, Any]:
        """Return the payload for the new session event."""
        duration = self.duration
        return {
            "session_id": self.id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat() if self.end else None,
            "duration_minutes": (
                round(duration.total_seconds() / 60, 1) if duration else None
            ),
            "energy_kwh": self.energy_kwh,
            "cost": self.cost,
            "currency": self.currency,
            "card_id": self.card_id,
            "card_name": card_name,
            "location_name": self.location_name,
            "address": self.address,
            "city": self.city,
            "country": self.country,
            "evse_id": self.evse_id,
            "operator": self.operator,
            **asdict(self.details or EMPTY_DETAILS),
        }

    def as_row(self, card_name: str | None) -> dict[str, Any]:
        """Return a compact summary for session lists, in local time."""
        duration = self.duration
        return {
            "session_id": self.id,
            "start": dt_util.as_local(self.start).isoformat(),
            "end": dt_util.as_local(self.end).isoformat() if self.end else None,
            "duration_minutes": (
                round(duration.total_seconds() / 60, 1) if duration else None
            ),
            "energy_kwh": self.energy_kwh,
            "cost": self.cost,
            "card": card_name or self.card_number,
            "location_name": self.location_name,
            "address": self.address,
            "postal_code": self.postal_code,
            "city": self.city,
            "country": self.country,
            "operator_name": self.details.operator_name if self.details else None,
        }


@dataclass(frozen=True, slots=True)
class SessionPage:
    """One page of the session list."""

    total_count: int
    sessions: list[ChargingSession]


@dataclass(frozen=True, slots=True)
class Invoice:
    """An invoice, without the personal data of the account holder."""

    id: str
    number: str
    date: datetime | None
    due_date: datetime | None
    paid_date: datetime | None
    amount_excl_vat: float | None
    amount_incl_vat: float | None
    vat_rate: float | None
    currency: str | None
    status: str | None
    payment_status: str | None
    invoice_type: str | None
    period: str | None
    session_count: int
    has_pdf: bool

    @property
    def is_paid(self) -> bool:
        """Return True when nothing is left to pay."""
        return "PAID" in (self.payment_status, self.status) or (
            self.invoice_type == "CREDIT"
        )

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Invoice:
        """Build from one element of the Invoices/GetAll items."""
        raw = item.get("invoice") or item
        lines = raw.get("invoiceLines") or []
        created = _parse_dt(raw.get("dateCreated"))
        return cls(
            id=str(raw["id"]),
            number=str(raw.get("invoiceNumber") or raw["id"]),
            date=_parse_dt(raw.get("invoiceDate")) or created,
            due_date=_parse_dt(raw.get("dateDue")),
            paid_date=_parse_dt(raw.get("datePaid")),
            amount_excl_vat=_float(raw.get("totalLocalCostExclVat")),
            amount_incl_vat=_float(raw.get("totalLocalCostInclVat")),
            vat_rate=_float(raw.get("vat")),
            currency=raw.get("currency"),
            status=raw.get("status"),
            payment_status=raw.get("paymentStatus"),
            invoice_type=raw.get("invoiceType"),
            period=lines[0].get("description") if lines else None,
            session_count=sum(len(line.get("mspCdrIds") or []) for line in lines),
            has_pdf=bool(raw.get("hasInvoicePdf")),
        )

    def as_row(self) -> dict[str, Any]:
        """Return a summary for lists, events and attributes (local time)."""

        def local(value: datetime | None) -> str | None:
            return dt_util.as_local(value).isoformat() if value else None

        return {
            "invoice_id": self.id,
            "number": self.number,
            "date": local(self.date),
            "due_date": local(self.due_date),
            "paid_date": local(self.paid_date),
            "amount_excl_vat": self.amount_excl_vat,
            "amount_incl_vat": self.amount_incl_vat,
            "vat_rate": self.vat_rate,
            "currency": self.currency,
            "status": self.payment_status or self.status,
            "paid": self.is_paid,
            "type": self.invoice_type,
            "period": self.period,
            "session_count": self.session_count,
        }
