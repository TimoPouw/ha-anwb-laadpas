"""Tests for the get_sessions action."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anwb_laadpas.const import DOMAIN

from .conftest import FakeDeftpower, load_sessions


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> MockConfigEntry:
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to("2026-11-15T12:00:00+01:00")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


async def _call(hass: HomeAssistant, **data):
    return await hass.services.async_call(
        DOMAIN, "get_sessions", data, blocking=True, return_response=True
    )


async def test_get_all_sessions(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    sessions = [i["chargingSession"] for i in load_sessions()]
    result = await _call(hass)
    assert result["count"] == 30
    assert result["energy_kwh"] == pytest.approx(sum(s["kwh"] for s in sessions))
    assert result["sessions"][0]["session_id"] == sessions[0]["id"]


async def test_filter_by_period_card_and_limit(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    sessions = [i["chargingSession"] for i in load_sessions()]
    october = [s for s in sessions if s["startDateTime"].startswith("2026-10")]
    result = await _call(hass, start="2026-10-01", end="2026-10-31")
    assert result["count"] == len(october)

    hass.config_entries.async_update_entry(
        loaded_entry, options={"card_names": {"100001": "Werk"}}
    )
    result = await _call(hass, card="werk")
    assert result["count"] == sum(1 for s in sessions if s["token"]["id"] == 100001)
    assert {row["card"] for row in result["sessions"]} == {"Werk"}

    result = await _call(hass, card="NL-XXX-C00000002-B", limit=3)
    assert result["count"] == 3


async def test_not_loaded(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    with pytest.raises(ServiceValidationError):
        await _call(hass, config_entry_id="unknown")
