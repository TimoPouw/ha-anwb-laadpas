"""Tests for the signed invoice download links."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from yarl import URL

from custom_components.anwb_laadpas.const import DOMAIN

from .conftest import FAKE_PDF, USER_ID, FakeDeftpower


@pytest.fixture
async def invoices_entity(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    tmp_path: Path,
) -> str:
    hass.config.media_dirs = {"local": str(tmp_path)}
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{USER_ID}_invoices"
    )


def _rows(hass: HomeAssistant, entity_id: str) -> list[dict]:
    return hass.states.get(entity_id).attributes["invoices"]


async def test_signed_link_downloads_pdf(
    hass: HomeAssistant,
    invoices_entity: str,
    fake_api: FakeDeftpower,
    hass_client_no_auth,
    tmp_path: Path,
) -> None:
    row = _rows(hass, invoices_entity)[0]
    assert "authSig=" in row["pdf_url"]
    assert "authSig=" in row["specification_url"]

    client = await hass_client_no_auth()
    resp = await client.get(row["pdf_url"])
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "application/pdf"
    assert resp.headers["Content-Disposition"].startswith('attachment; filename="')
    assert "_invoice.pdf" in resp.headers["Content-Disposition"]
    assert await resp.read() == FAKE_PDF
    # Stored in the media folder as well, so a second click needs no download.
    assert len(fake_api.downloads) == 1
    resp = await client.get(row["pdf_url"])
    assert resp.status == 200
    assert len(fake_api.downloads) == 1
    assert len(list((tmp_path / "anwb_laadpas").glob("*_invoice.pdf"))) == 1

    resp = await client.get(row["specification_url"])
    assert resp.status == 200
    assert "_specification.pdf" in resp.headers["Content-Disposition"]


async def test_unsigned_or_tampered_link_is_refused(
    hass: HomeAssistant, invoices_entity: str, hass_client_no_auth
) -> None:
    rows = _rows(hass, invoices_entity)
    client = await hass_client_no_auth()

    unsigned = URL(rows[0]["pdf_url"]).path
    assert (await client.get(unsigned)).status == 401

    # The signature of one invoice does not open another.
    other = URL(rows[1]["pdf_url"]).path
    sig = URL(rows[0]["pdf_url"]).query["authSig"]
    assert (await client.get(f"{other}?authSig={sig}")).status == 401


async def test_unknown_invoice_or_kind(
    hass: HomeAssistant,
    invoices_entity: str,
    mock_config_entry: MockConfigEntry,
    hass_client,
) -> None:
    client = await hass_client()
    base = f"/api/anwb_laadpas/invoice/{mock_config_entry.entry_id}"
    invoice_id = _rows(hass, invoices_entity)[0]["invoice_id"]
    assert (await client.get(f"{base}/nope/invoice")).status == 404
    assert (await client.get(f"{base}/{invoice_id}/other")).status == 404
    assert (
        await client.get(f"/api/anwb_laadpas/invoice/x/{invoice_id}/invoice")
    ).status == 404


async def test_links_stay_stable_until_renewal(
    hass: HomeAssistant,
    invoices_entity: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    first = _rows(hass, invoices_entity)[0]["pdf_url"]
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _rows(hass, invoices_entity)[0]["pdf_url"] == first

    # Renewed once less than a day of validity is left.
    for _ in range(3):
        freezer.tick(timedelta(hours=12))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert _rows(hass, invoices_entity)[0]["pdf_url"] != first
