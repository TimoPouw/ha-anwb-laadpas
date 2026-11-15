"""Base entity and device helpers for ANWB Laadpas."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AnwbLaadpasCoordinator

if TYPE_CHECKING:
    from . import AnwbLaadpasConfigEntry


def _account_key(entry: AnwbLaadpasConfigEntry) -> str:
    return entry.unique_id or entry.entry_id


def account_device_identifier(entry: AnwbLaadpasConfigEntry) -> tuple[str, str]:
    """Return the device identifier of the account."""
    return (DOMAIN, _account_key(entry))


def card_device_identifier(
    entry: AnwbLaadpasConfigEntry, card_id: str
) -> tuple[str, str]:
    """Return the device identifier of one charging card."""
    return (DOMAIN, f"{_account_key(entry)}_card_{card_id}")


def card_device_name(
    coordinator: AnwbLaadpasCoordinator, card_id: str, card_number: str | None
) -> str:
    """Return the name of a card device: the user's name or the card number."""
    return coordinator.card_name(card_id) or f"Laadpas {card_number or card_id}"


class AnwbLaadpasEntity(CoordinatorEntity[AnwbLaadpasCoordinator]):
    """Base entity, attached to the account or to one card."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AnwbLaadpasCoordinator,
        key: str,
        card_id: str | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.card_id = card_id
        if card_id is None:
            self._attr_unique_id = f"{_account_key(entry)}_{key}"
            self._attr_device_info = DeviceInfo(
                identifiers={account_device_identifier(entry)},
                name="ANWB Laadpas",
                manufacturer=MANUFACTURER,
                model="Account",
                entry_type=DeviceEntryType.SERVICE,
            )
        else:
            card_number = coordinator.data.cards.get(card_id)
            self._attr_unique_id = f"{_account_key(entry)}_card_{card_id}_{key}"
            self._attr_device_info = DeviceInfo(
                identifiers={card_device_identifier(entry, card_id)},
                name=card_device_name(coordinator, card_id, card_number),
                manufacturer=MANUFACTURER,
                model="Laadpas",
                serial_number=card_number,
                via_device=account_device_identifier(entry),
            )
