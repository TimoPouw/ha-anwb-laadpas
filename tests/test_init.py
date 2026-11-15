"""Tests for setup, token rotation and the coordinator."""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.anwb_laadpas.const import (
    CONF_AUTH_CONFIG,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    EVENT_NEW_SESSION,
)

from .conftest import CLIENT_ID, FakeDeftpower


def _listen(hass: HomeAssistant, events: list[Any]) -> None:
    @callback
    def _listener(event: Any) -> None:
        events.append(event)

    hass.bus.async_listen(EVENT_NEW_SESSION, _listener)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _new_session(
    template: dict[str, Any], session_id: str, start: str
) -> dict[str, Any]:
    item = copy.deepcopy(template)
    item["chargingSession"]["id"] = session_id
    item["chargingSession"]["startDateTime"] = start
    item["chargingSession"]["endDateTime"] = start.replace("T10", "T11")
    item["chargingSession"]["kwh"] = 10.0
    return item


async def test_setup_and_unload(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert len(mock_config_entry.runtime_data.data.sessions) == 30

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_rotated_refresh_token_is_stored(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == fake_api.valid_refresh_token
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] != "refresh-0"


async def test_discovered_config_updates_entry(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    fake_api.client_id = "new-client-id"
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.data[CONF_AUTH_CONFIG]["client_id"] == "new-client-id"


async def test_stored_config_used_when_discovery_fails(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    fake_api.app_config_status = 500
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.data[CONF_AUTH_CONFIG]["client_id"] == CLIENT_ID
    assert fake_api.refresh_counter == 1


async def test_invalid_refresh_token_starts_reauth(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    fake_api.token_error = "invalid_grant"
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_api_error_retries_setup(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    fake_api.sessions_status = 500
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unauthorized_redirect_refreshes_and_reauths(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, mock_config_entry)
    refreshes = fake_api.refresh_counter
    fake_api.sessions_status = 302

    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # One forced refresh after the redirect, then give up and ask to log in.
    assert fake_api.refresh_counter == refreshes + 1
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_initial_sync_fires_no_events_then_new_session_does(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    small_pages: None,
) -> None:
    events: list[Any] = []

    @callback
    def _listener(event: Any) -> None:
        events.append(event)

    hass.bus.async_listen(EVENT_NEW_SESSION, _listener)
    await _setup(hass, mock_config_entry)
    # Initial sync fetched all 30 sessions in pages of 5, but fired nothing.
    assert len(fake_api.session_calls()) == 6
    assert events == []

    fake_api.add_session(
        _new_session(fake_api.sessions[0], "NLAAA-999999999", "2026-11-14T10:00:00Z")
    )
    calls_before = len(fake_api.session_calls())
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["session_id"] == "NLAAA-999999999"
    assert data["energy_kwh"] == 10.0
    assert data["duration_minutes"] == 60.0
    assert data["config_entry_id"] == mock_config_entry.entry_id
    # Incremental update: the minimum of two pages, not the full history.
    assert len(fake_api.session_calls()) - calls_before == 2


async def test_late_session_is_picked_up(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    small_pages: None,
) -> None:
    events: list[Any] = []
    _listen(hass, events)
    await _setup(hass, mock_config_entry)

    # A session that started earlier than the newest known one shows up later,
    # on the second page.
    late = _new_session(fake_api.sessions[0], "NLBBB-888888888", "2026-10-01T10:00:00Z")
    fake_api.sessions.insert(7, late)

    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert [e.data["session_id"] for e in events] == ["NLBBB-888888888"]


async def test_sessions_survive_restart(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    small_pages: None,
) -> None:
    events: list[Any] = []
    _listen(hass, events)
    await _setup(hass, mock_config_entry)
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    fake_api.add_session(
        _new_session(fake_api.sessions[0], "NLCCC-777777777", "2026-11-14T10:00:00Z")
    )
    calls_before = len(fake_api.session_calls())
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Known sessions were loaded from storage: only the new one is an event and
    # only two pages were fetched.
    assert [e.data["session_id"] for e in events] == ["NLCCC-777777777"]
    assert len(fake_api.session_calls()) - calls_before == 2


async def test_updated_session_replaces_known_one(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    events: list[Any] = []
    _listen(hass, events)
    await _setup(hass, mock_config_entry)
    session_id = fake_api.sessions[7]["chargingSession"]["id"]
    fake_api.sessions[7]["chargingSession"]["totalCosts"]["incl_vat"] = 1.23

    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    data = mock_config_entry.runtime_data.data
    assert next(s for s in data.sessions if s.id == session_id).cost == 1.23
    assert events == []
