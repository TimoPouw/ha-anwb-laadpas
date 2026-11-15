"""Tests for session details, invoices and invoice downloads."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.anwb_laadpas.const import (
    CONF_AUTO_DOWNLOAD,
    DOMAIN,
    EVENT_NEW_INVOICE,
    EVENT_NEW_SESSION,
)
from custom_components.anwb_laadpas.coordinator import MAX_DETAIL_ATTEMPTS
from custom_components.anwb_laadpas.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import FAKE_PDF, USER_ID, FakeDeftpower, load_invoices, load_sessions


@pytest.fixture
def media_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    hass.config.media_dirs = {"local": str(tmp_path)}
    return tmp_path / "anwb_laadpas"


def _listen(hass: HomeAssistant, event_type: str) -> list[Any]:
    events: list[Any] = []

    @callback
    def _listener(event: Any) -> None:
        events.append(event)

    hass.bus.async_listen(event_type, _listener)
    return events


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, wait_backfill: bool = True
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=wait_backfill)


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    # The refresh runs as a background task; file writes use the executor.
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_details_backfill_runs_in_background(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    import asyncio
    from unittest.mock import patch

    from custom_components.anwb_laadpas.api import AnwbLaadpasClient

    release = asyncio.Event()
    original = AnwbLaadpasClient.async_get_session_details

    async def _slow(self, session_id):
        await release.wait()
        return await original(self, session_id)

    with patch.object(AnwbLaadpasClient, "async_get_session_details", _slow):
        await _setup(hass, mock_config_entry, wait_backfill=False)
        # Setup finished while the details are still being fetched.
        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert mock_config_entry.runtime_data.data.details_pending == 30
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    data = mock_config_entry.runtime_data.data
    assert data.details_pending == 0
    # Newest first, one call per session.
    assert fake_api.detail_calls()[0] == data.sessions[0].id
    assert len(fake_api.detail_calls()) == 30

    # Nothing left to fetch: no more detail calls.
    await _tick(hass, freezer)
    assert len(fake_api.detail_calls()) == 30

    last = data.sessions[0]
    assert last.details.operator_name == "Example Operator AAA"
    assert last.details.vehicle == "Example EV1"
    assert last.details.connector_standard == "IEC_62196_T2"
    assert last.details.cost_excl_vat == pytest.approx(last.cost / 1.21, abs=0.01)

    # The sensors show the details without waiting for the next update.
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{USER_ID}_last_session_energy"
    )
    assert hass.states.get(entity_id).attributes["operator_name"] == (
        "Example Operator AAA"
    )


async def test_backfill_pauses_between_calls(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
) -> None:
    from unittest.mock import patch

    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    with (
        patch("custom_components.anwb_laadpas.coordinator.BACKFILL_DELAY", 1.5),
        patch("custom_components.anwb_laadpas.coordinator.asyncio.sleep", _sleep),
    ):
        await _setup(hass, mock_config_entry)
    assert delays.count(1.5) == 30


async def test_new_session_event_has_details(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    events = _listen(hass, EVENT_NEW_SESSION)
    await _setup(hass, mock_config_entry)

    item = load_sessions()[1]
    item["chargingSession"]["id"] = "NLAAA-1"
    item["chargingSession"]["startDateTime"] = "2026-11-14T10:00:00Z"
    detail = json.loads(
        json.dumps(fake_api.details[load_sessions()[1]["chargingSession"]["id"]])
    )
    detail["chargingSession"]["id"] = "NLAAA-1"
    fake_api.details["NLAAA-1"] = detail
    fake_api.add_session(item)
    await _tick(hass, freezer)

    assert len(events) == 1
    data = events[0].data
    assert data["operator_name"] == "Example Operator BBB"
    assert data["vehicle"] == "Example EV1"
    assert data["latitude"] is not None


async def test_details_survive_list_update_and_restart(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, mock_config_entry)
    await _tick(hass, freezer)
    session_id = fake_api.sessions[0]["chargingSession"]["id"]
    # The list shows a correction; details must be kept.
    fake_api.sessions[0]["chargingSession"]["totalCosts"]["incl_vat"] = 99.0
    await _tick(hass, freezer)
    session = next(
        s for s in mock_config_entry.runtime_data.data.sessions if s.id == session_id
    )
    assert session.cost == 99.0
    assert session.details is not None

    calls = len(fake_api.detail_calls())
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.runtime_data.data.details_pending == 0
    assert len(fake_api.detail_calls()) == calls


async def test_failing_details_give_up(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    broken = fake_api.sessions[0]["chargingSession"]["id"]
    del fake_api.details[broken]
    await _setup(hass, mock_config_entry)
    for _ in range(MAX_DETAIL_ATTEMPTS + 1):
        await _tick(hass, freezer)
    data = mock_config_entry.runtime_data.data
    assert fake_api.detail_calls().count(broken) == MAX_DETAIL_ATTEMPTS
    assert data.details_pending == 0
    assert data.sessions[0].details.operator_name is None


async def test_invoice_sensors(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    await _setup(hass, mock_config_entry)
    invoices = [i["invoice"] for i in load_invoices()]

    def state(key: str):
        entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{USER_ID}_{key}"
        )
        return hass.states.get(entity_id)

    last = state("last_invoice")
    assert float(last.state) == invoices[0]["totalLocalCostInclVat"]
    assert last.attributes["number"] == invoices[0]["invoiceNumber"]
    assert last.attributes["paid"] is False
    assert last.attributes["session_count"] == 1

    assert float(state("open_amount").state) == invoices[0]["totalLocalCostInclVat"]

    listing = state("invoices")
    assert int(listing.state) == 10
    rows = listing.attributes["invoices"]
    assert len(rows) == 10
    # The invoice without invoiceDate falls back to its creation date.
    assert all(row["date"] for row in rows)
    # Personal data of the account holder never reaches Home Assistant.
    dumped = json.dumps(rows)
    for secret in ("Test User", "example.invalid", "Teststraat", "1234AB"):
        assert secret not in dumped


async def test_invoice_error_keeps_sessions_working(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
) -> None:
    fake_api.invoices_status = 500
    await _setup(hass, mock_config_entry)
    data = mock_config_entry.runtime_data.data
    assert len(data.sessions) == 30
    assert data.invoices == []


async def test_new_invoice_event_and_auto_download(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    media_dir: Path,
) -> None:
    events = _listen(hass, EVENT_NEW_INVOICE)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_AUTO_DOWNLOAD: True}
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    # Existing invoices are neither announced nor downloaded.
    assert events == []
    assert fake_api.downloads == []

    new = json.loads(json.dumps(fake_api.invoices[0]))
    new["invoice"]["id"] = "00000000-0000-4000-8000-999999999999"
    new["invoice"]["invoiceNumber"] = "1700000099"
    new["invoice"]["invoiceDate"] = "2026-11-30T00:00:00Z"
    fake_api.invoices.insert(0, new)
    await _tick(hass, freezer)

    assert len(events) == 1
    data = events[0].data
    assert data["number"] == "1700000099"
    assert [Path(f).name for f in data["files"]] == [
        "2026-11-30_1700000099_invoice.pdf",
        "2026-11-30_1700000099_specification.pdf",
    ]
    for file in data["files"]:
        assert Path(file).read_bytes() == FAKE_PDF
    assert [d.get("type") for d in fake_api.downloads] == [None, "specification"]


async def test_get_invoices_action(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    result = await hass.services.async_call(
        DOMAIN, "get_invoices", {}, blocking=True, return_response=True
    )
    assert result["count"] == 10
    assert (
        result["open_amount"]
        == fake_api.invoices[0]["invoice"]["totalLocalCostInclVat"]
    )

    result = await hass.services.async_call(
        DOMAIN,
        "get_invoices",
        {"unpaid_only": True},
        blocking=True,
        return_response=True,
    )
    assert result["count"] == 1

    result = await hass.services.async_call(
        DOMAIN,
        "get_invoices",
        {"start": "2026-08-01", "end": "2026-09-30"},
        blocking=True,
        return_response=True,
    )
    assert [r["date"][:7] for r in result["invoices"]] == ["2026-09", "2026-08"]


async def test_download_invoices_action(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    media_dir: Path,
) -> None:
    await _setup(hass, mock_config_entry)
    number = fake_api.invoices[2]["invoice"]["invoiceNumber"]

    result = await hass.services.async_call(
        DOMAIN,
        "download_invoices",
        {"invoice": number, "kind": "invoice"},
        blocking=True,
        return_response=True,
    )
    assert result["count"] == 1
    assert len(result["files"]) == 1
    assert Path(result["files"][0]).read_bytes() == FAKE_PDF
    assert len(fake_api.downloads) == 1

    # Existing files are skipped, unless overwrite is set.
    await hass.services.async_call(
        DOMAIN,
        "download_invoices",
        {"invoice": number, "kind": "invoice"},
        blocking=True,
    )
    assert len(fake_api.downloads) == 1
    await hass.services.async_call(
        DOMAIN,
        "download_invoices",
        {"invoice": number, "kind": "invoice", "overwrite": True},
        blocking=True,
    )
    assert len(fake_api.downloads) == 2

    # All invoices, both kinds.
    result = await hass.services.async_call(
        DOMAIN, "download_invoices", {}, blocking=True, return_response=True
    )
    assert result["count"] == 10
    assert len(list(media_dir.glob("*.pdf"))) == 20

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "download_invoices", {"invoice": "nope"}, blocking=True
        )


async def test_diagnostics_include_invoices_redacted(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diag["data"]["invoice_count"] == 10
    assert diag["data"]["sessions_without_details"] == 0
    dumped = json.dumps(diag, default=str)
    assert fake_api.invoices[0]["invoice"]["invoiceNumber"] not in dumped
    detail = fake_api.details[fake_api.sessions[0]["chargingSession"]["id"]]
    assert (
        str(detail["chargingSession"]["location"]["coordinates"]["latitude"])
        not in dumped
    )


async def test_unload_stops_backfill(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
) -> None:
    import asyncio
    from unittest.mock import patch

    from custom_components.anwb_laadpas.api import AnwbLaadpasClient

    async def _never(self, session_id):
        await asyncio.Event().wait()

    with patch.object(AnwbLaadpasClient, "async_get_session_details", _never):
        await _setup(hass, mock_config_entry, wait_backfill=False)
        task = mock_config_entry.runtime_data._backfill_task
        assert task is not None and not task.done()
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert task.cancelled()


async def test_coordinates_do_not_put_sensors_on_the_map(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, mock_config_entry)
    for state in hass.states.async_all("sensor"):
        # The map dashboard and map cards pick up any entity with these.
        assert "latitude" not in state.attributes, state.entity_id
        assert "longitude" not in state.attributes, state.entity_id
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{USER_ID}_last_session_energy"
    )
    attrs = hass.states.get(entity_id).attributes
    assert attrs["location_latitude"] is not None
    assert attrs["location_longitude"] is not None
