"""Data models for ANWB Laadpas charging sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return dt_util.parse_datetime(value)


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
        return cls(
            **{
                **data,
                "start": _parse_dt(data["start"]),
                "end": _parse_dt(data.get("end")),
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
        }


@dataclass(frozen=True, slots=True)
class SessionPage:
    """One page of the session list."""

    total_count: int
    sessions: list[ChargingSession]
