"""Tests for the sensors."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .conftest import USER_ID, FakeDeftpower, load_sessions


def _state(hass: HomeAssistant, entity_registry: er.EntityRegistry, unique_id: str):
    entity_id = entity_registry.async_get_entity_id("sensor", "anwb_laadpas", unique_id)
    assert entity_id, unique_id
    return hass.states.get(entity_id)


@pytest.fixture
async def setup_entry(
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


async def test_account_sensors(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    sessions = [i["chargingSession"] for i in load_sessions()]
    november = [s for s in sessions if s["startDateTime"] >= "2026-11"]
    last = sessions[0]

    state = _state(hass, entity_registry, f"{USER_ID}_last_session_energy")
    assert float(state.state) == last["kwh"]
    assert state.attributes["unit_of_measurement"] == "kWh"
    assert state.attributes["session_id"] == last["id"]
    assert state.attributes["location_name"] == last["location"]["name"]
    assert state.attributes["evse_id"] == last["location"]["evseId"]
    assert state.attributes["card_id"] == "100001"

    state = _state(hass, entity_registry, f"{USER_ID}_last_session_cost")
    assert float(state.state) == last["totalCosts"]["incl_vat"]
    assert state.attributes["unit_of_measurement"] == "EUR"

    start = dt_util.parse_datetime(last["startDateTime"])
    end = dt_util.parse_datetime(last["endDateTime"])
    state = _state(hass, entity_registry, f"{USER_ID}_last_session_start")
    assert dt_util.parse_datetime(state.state) == start
    state = _state(hass, entity_registry, f"{USER_ID}_last_session_duration")
    assert float(state.state) == round((end - start).total_seconds() / 60, 1)

    state = _state(hass, entity_registry, f"{USER_ID}_month_sessions")
    assert int(state.state) == len(november)
    state = _state(hass, entity_registry, f"{USER_ID}_month_energy")
    assert float(state.state) == pytest.approx(sum(s["kwh"] for s in november))
    assert state.attributes["state_class"] == "total"
    assert state.attributes["last_reset"] == "2026-11-01T00:00:00+01:00"

    state = _state(hass, entity_registry, f"{USER_ID}_total_energy")
    assert float(state.state) == pytest.approx(sum(s["kwh"] for s in sessions))
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["device_class"] == "energy"

    state = _state(hass, entity_registry, f"{USER_ID}_total_cost")
    # One fixture session has no costs yet; it counts as zero.
    assert float(state.state) == pytest.approx(
        sum(s["totalCosts"]["incl_vat"] or 0 for s in sessions)
    )


async def test_card_sensors_and_devices(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    sessions = [i["chargingSession"] for i in load_sessions()]
    card_2 = [s for s in sessions if s["token"]["id"] == 100002]

    state = _state(hass, entity_registry, f"{USER_ID}_card_100002_total_energy")
    assert float(state.state) == pytest.approx(sum(s["kwh"] for s in card_2))
    state = _state(hass, entity_registry, f"{USER_ID}_card_100002_last_session_energy")
    assert state.attributes["session_id"] == card_2[0]["id"]

    devices = dr.async_entries_for_config_entry(device_registry, setup_entry.entry_id)
    assert len(devices) == 3
    card_device = device_registry.async_get_device(
        identifiers={("anwb_laadpas", f"{USER_ID}_card_100002")}
    )
    assert card_device.serial_number == "NL-XXX-C00000002-B"
    assert card_device.via_device_id is not None


async def test_new_card_adds_device(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_api: FakeDeftpower,
    freezer: FrozenDateTimeFactory,
    entity_registry: er.EntityRegistry,
) -> None:
    item = load_sessions()[0]
    item["chargingSession"]["id"] = "NLAAA-123"
    item["chargingSession"]["startDateTime"] = "2026-11-15T09:00:00Z"
    item["chargingSession"]["token"] = {"id": 100003, "emaId": "NL-XXX-C00000003-C"}
    fake_api.add_session(item)

    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = _state(hass, entity_registry, f"{USER_ID}_card_100003_total_energy")
    assert float(state.state) == item["chargingSession"]["kwh"]


async def test_sessions_sensor_lists_all_sessions(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    sessions = [i["chargingSession"] for i in load_sessions()]
    state = _state(hass, entity_registry, f"{USER_ID}_sessions")
    assert int(state.state) == 30
    rows = state.attributes["sessions"]
    assert len(rows) == 30
    assert rows[0]["session_id"] == sessions[0]["id"]
    # Local time (Europe/Amsterdam), card number when no name is set.
    assert rows[0]["start"] == "2026-11-08T13:15:16+01:00"
    assert rows[0]["card"] == "NL-XXX-C00000001-A"
    assert set(rows[0]) == {
        "session_id",
        "start",
        "end",
        "duration_minutes",
        "energy_kwh",
        "cost",
        "card",
        "location_name",
        "city",
    }

    # The list lives on the account only; per card it would duplicate data.
    assert not entity_registry.async_get_entity_id(
        "sensor", "anwb_laadpas", f"{USER_ID}_card_100002_sessions"
    )


async def test_session_list_is_not_recorded(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    from custom_components.anwb_laadpas.sensor import LaadpasSensor

    assert "sessions" in LaadpasSensor._unrecorded_attributes
