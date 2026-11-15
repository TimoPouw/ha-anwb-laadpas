"""Tests for diagnostics."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anwb_laadpas.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import USER_ID, FakeDeftpower, load_sessions


async def test_diagnostics_are_redacted(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"card_names": {"100002": "Private name"}}
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    dumped = json.dumps(diag, default=str)

    last = load_sessions()[0]["chargingSession"]
    for secret in (
        fake_api.valid_refresh_token,
        USER_ID,
        "Private name",
        last["token"]["emaId"],
        last["location"]["address"],
        last["location"]["name"],
        last["location"]["evseId"],
        last["id"],
    ):
        assert secret not in dumped, secret

    assert diag["data"]["session_count"] == 30
    assert diag["data"]["card_count"] == 2
    assert diag["data"]["sessions_without_cost"] == 1
    assert diag["data"]["sessions_without_end"] == 1
    assert diag["data"]["last_session"]["energy_kwh"] == last["kwh"]
