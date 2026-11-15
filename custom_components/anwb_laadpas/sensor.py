"""Sensors for ANWB Laadpas."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AnwbLaadpasConfigEntry
from .coordinator import AnwbLaadpasCoordinator, LaadpasData, month_start
from .entity import AnwbLaadpasEntity
from .http import links_for_rows
from .models import ChargingSession

# Coordinator based, the coordinator serializes updates.
PARALLEL_UPDATES = 0

CURRENCY = "EUR"


def _session_attributes(
    coordinator: AnwbLaadpasCoordinator, session: ChargingSession | None
) -> dict[str, Any] | None:
    if session is None:
        return None
    return session.as_event_data(coordinator.card_name(session.card_id))


def _duration_minutes(session: ChargingSession | None) -> float | None:
    if session is None or session.duration is None:
        return None
    return round(session.duration.total_seconds() / 60, 1)


@dataclass(frozen=True, kw_only=True)
class LaadpasSensorDescription(SensorEntityDescription):
    """Describes an ANWB Laadpas sensor.

    value_fn and last_reset_fn get the data and the card id (None for the
    account wide sensors).
    """

    value_fn: Callable[[LaadpasData, str | None], Any]
    with_session_attributes: bool = False
    with_session_list: bool = False
    attributes_fn: Callable[[AnwbLaadpasCoordinator], dict[str, Any] | None] | None = (
        None
    )
    last_reset_fn: Callable[[], datetime] | None = None


def _last(attr: str) -> Callable[[LaadpasData, str | None], Any]:
    def value(data: LaadpasData, card_id: str | None) -> Any:
        session = data.last(card_id)
        return getattr(session, attr) if session else None

    return value


LAST_SESSION_ENERGY = LaadpasSensorDescription(
    key="last_session_energy",
    translation_key="last_session_energy",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    suggested_display_precision=2,
    value_fn=_last("energy_kwh"),
    with_session_attributes=True,
)
LAST_SESSION_COST = LaadpasSensorDescription(
    key="last_session_cost",
    translation_key="last_session_cost",
    device_class=SensorDeviceClass.MONETARY,
    native_unit_of_measurement=CURRENCY,
    suggested_display_precision=2,
    value_fn=_last("cost"),
)
LAST_SESSION_START = LaadpasSensorDescription(
    key="last_session_start",
    translation_key="last_session_start",
    device_class=SensorDeviceClass.TIMESTAMP,
    value_fn=_last("start"),
)
LAST_SESSION_END = LaadpasSensorDescription(
    key="last_session_end",
    translation_key="last_session_end",
    device_class=SensorDeviceClass.TIMESTAMP,
    value_fn=_last("end"),
)
LAST_SESSION_DURATION = LaadpasSensorDescription(
    key="last_session_duration",
    translation_key="last_session_duration",
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.MINUTES,
    suggested_display_precision=0,
    value_fn=lambda data, card: _duration_minutes(data.last(card)),
)
MONTH_SESSIONS = LaadpasSensorDescription(
    key="month_sessions",
    translation_key="month_sessions",
    state_class=SensorStateClass.TOTAL,
    value_fn=lambda data, card: len(data.this_month(card)),
    last_reset_fn=month_start,
)
MONTH_ENERGY = LaadpasSensorDescription(
    key="month_energy",
    translation_key="month_energy",
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    suggested_display_precision=2,
    value_fn=lambda data, card: round(
        sum(s.energy_kwh for s in data.this_month(card)), 3
    ),
    last_reset_fn=month_start,
)
MONTH_COST = LaadpasSensorDescription(
    key="month_cost",
    translation_key="month_cost",
    device_class=SensorDeviceClass.MONETARY,
    state_class=SensorStateClass.TOTAL,
    native_unit_of_measurement=CURRENCY,
    suggested_display_precision=2,
    value_fn=lambda data, card: round(
        sum(s.cost or 0.0 for s in data.this_month(card)), 2
    ),
    last_reset_fn=month_start,
)
TOTAL_ENERGY = LaadpasSensorDescription(
    key="total_energy",
    translation_key="total_energy",
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    suggested_display_precision=2,
    value_fn=lambda data, card: data.total_energy(card),
)
TOTAL_COST = LaadpasSensorDescription(
    key="total_cost",
    translation_key="total_cost",
    device_class=SensorDeviceClass.MONETARY,
    state_class=SensorStateClass.TOTAL,
    native_unit_of_measurement=CURRENCY,
    suggested_display_precision=2,
    value_fn=lambda data, card: data.total_cost(card),
)

SESSIONS = LaadpasSensorDescription(
    key="sessions",
    translation_key="sessions",
    state_class=SensorStateClass.TOTAL_INCREASING,
    value_fn=lambda data, card: len(data.for_card(card)),
    with_session_list=True,
)

LAST_INVOICE = LaadpasSensorDescription(
    key="last_invoice",
    translation_key="last_invoice",
    device_class=SensorDeviceClass.MONETARY,
    native_unit_of_measurement=CURRENCY,
    suggested_display_precision=2,
    value_fn=lambda data, card: (
        data.last_invoice.amount_incl_vat if data.last_invoice else None
    ),
    attributes_fn=lambda coordinator: (
        links_for_rows(
            coordinator.hass,
            coordinator.config_entry.entry_id,
            [coordinator.data.last_invoice],
        )[0]
        if coordinator.data.last_invoice
        else None
    ),
)
OPEN_AMOUNT = LaadpasSensorDescription(
    key="open_amount",
    translation_key="open_amount",
    device_class=SensorDeviceClass.MONETARY,
    native_unit_of_measurement=CURRENCY,
    suggested_display_precision=2,
    value_fn=lambda data, card: data.open_amount,
)
INVOICES = LaadpasSensorDescription(
    key="invoices",
    translation_key="invoices",
    state_class=SensorStateClass.TOTAL_INCREASING,
    value_fn=lambda data, card: len(data.invoices),
    attributes_fn=lambda coordinator: {
        "invoices": links_for_rows(
            coordinator.hass,
            coordinator.config_entry.entry_id,
            coordinator.data.invoices,
        )
    },
)

ACCOUNT_SENSORS: tuple[LaadpasSensorDescription, ...] = (
    LAST_SESSION_ENERGY,
    LAST_SESSION_COST,
    LAST_SESSION_START,
    LAST_SESSION_END,
    LAST_SESSION_DURATION,
    MONTH_SESSIONS,
    MONTH_ENERGY,
    MONTH_COST,
    TOTAL_ENERGY,
    TOTAL_COST,
    SESSIONS,
    LAST_INVOICE,
    OPEN_AMOUNT,
    INVOICES,
)
CARD_SENSORS: tuple[LaadpasSensorDescription, ...] = (
    LAST_SESSION_ENERGY,
    LAST_SESSION_COST,
    LAST_SESSION_END,
    MONTH_SESSIONS,
    MONTH_ENERGY,
    MONTH_COST,
    TOTAL_ENERGY,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnwbLaadpasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors, and add card sensors when a new card shows up."""
    coordinator = entry.runtime_data
    known_cards: set[str] = set()

    entities: list[LaadpasSensor] = [
        LaadpasSensor(coordinator, description) for description in ACCOUNT_SENSORS
    ]

    @callback
    def _card_entities() -> list[LaadpasSensor]:
        new_cards = set(coordinator.data.cards) - known_cards
        known_cards.update(new_cards)
        return [
            LaadpasSensor(coordinator, description, card_id)
            for card_id in sorted(new_cards)
            for description in CARD_SENSORS
        ]

    async_add_entities([*entities, *_card_entities()])

    @callback
    def _check_new_cards() -> None:
        if new := _card_entities():
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_check_new_cards))


class LaadpasSensor(AnwbLaadpasEntity, SensorEntity):
    """A sensor for the account or one charging card."""

    entity_description: LaadpasSensorDescription
    # Full lists are for dashboards only; keep them out of the database.
    _unrecorded_attributes = frozenset({"sessions", "invoices"})

    def __init__(
        self,
        coordinator: AnwbLaadpasCoordinator,
        description: LaadpasSensorDescription,
        card_id: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key, card_id)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data, self.card_id)

    @property
    def last_reset(self) -> datetime | None:
        """Return the start of the current period for monthly totals."""
        if self.entity_description.last_reset_fn is None:
            return None
        return self.entity_description.last_reset_fn()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return details of the last session on the energy sensor."""
        if self.entity_description.attributes_fn is not None:
            return self.entity_description.attributes_fn(self.coordinator)
        if self.entity_description.with_session_list:
            return {
                "sessions": [
                    session.as_row(self.coordinator.card_name(session.card_id))
                    for session in self.coordinator.data.for_card(self.card_id)
                ]
            }
        if not self.entity_description.with_session_attributes:
            return None
        return _session_attributes(
            self.coordinator, self.coordinator.data.last(self.card_id)
        )
