"""Tests for the config, reauth and options flows."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anwb_laadpas.const import (
    CONF_AUTH_CONFIG,
    CONF_CARD_NAMES,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import CLIENT_ID, TENANT_ID, USER_ID, FakeDeftpower


async def _start_login(hass: HomeAssistant) -> tuple[str, str]:
    """Start the flow, choose login and return (flow_id, authorize_url)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["login", "token"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "login"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    return result["flow_id"], result["description_placeholders"]["authorize_url"]


def _redirect_for(
    fake_api: FakeDeftpower, authorize_url: str, code: str = "code-1"
) -> str:
    state = parse_qs(urlparse(authorize_url).query)["state"][0]
    fake_api.issued_codes[code] = state
    return f"https://laadpas.anwb.nl/account/login?code={code}&state={state}&iss=x"


async def test_login_flow(hass: HomeAssistant, fake_api: FakeDeftpower) -> None:
    flow_id, url = await _start_login(hass)
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["code_challenge_method"] == ["S256"]
    assert query["acr_values"] == [f"tenant:{TENANT_ID} autoRedirect:true"]
    assert "offline_access" in query["scope"][0].split()

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"redirect_url": _redirect_for(fake_api, url)}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ANWB Laadpas"
    entry = result["result"]
    assert entry.unique_id == USER_ID
    assert entry.data[CONF_AUTH_CONFIG]["client_id"] == CLIENT_ID
    # The token in the entry is always the newest one the server handed out.
    assert entry.data[CONF_REFRESH_TOKEN] == fake_api.valid_refresh_token


async def test_login_flow_state_mismatch(
    hass: HomeAssistant, fake_api: FakeDeftpower
) -> None:
    flow_id, _ = await _start_login(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {"redirect_url": "https://laadpas.anwb.nl/account/login?code=x&state=other"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_redirect_url"}


async def test_login_flow_used_code_gives_new_link(
    hass: HomeAssistant, fake_api: FakeDeftpower
) -> None:
    flow_id, url = await _start_login(hass)
    redirect = _redirect_for(fake_api, url)
    fake_api.issued_codes.clear()  # The web app already used the code.

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"redirect_url": redirect}
    )
    assert result["errors"] == {"base": "invalid_code"}
    new_url = result["description_placeholders"]["authorize_url"]
    assert new_url != url

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"redirect_url": _redirect_for(fake_api, new_url, "code-2")}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_token_flow(hass: HomeAssistant, fake_api: FakeDeftpower) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    assert result["step_id"] == "token"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: " refresh-0 "}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data[CONF_REFRESH_TOKEN] == fake_api.valid_refresh_token
    assert result["result"].data[CONF_REFRESH_TOKEN] != "refresh-0"


async def test_token_flow_invalid(hass: HomeAssistant, fake_api: FakeDeftpower) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_discovery_failure_aborts(
    hass: HomeAssistant, fake_api: FakeDeftpower
) -> None:
    fake_api.app_config_status = 503
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_already_configured(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    fake_api.valid_refresh_token = "browser-token"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: "browser-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, fake_api: FakeDeftpower, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "login"}
    )
    url = result["description_placeholders"]["authorize_url"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": _redirect_for(fake_api, url)}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == fake_api.valid_refresh_token


async def test_reauth_wrong_account(
    hass: HomeAssistant, fake_api: FakeDeftpower
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="someone-else",
        data={CONF_REFRESH_TOKEN: "old", CONF_AUTH_CONFIG: {}},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: "refresh-0"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_options_flow_names_cards(
    hass: HomeAssistant,
    fake_api: FakeDeftpower,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"card_count": "2"}
    fields = [str(k) for k in result["data_schema"].schema]
    assert fields == [CONF_SCAN_INTERVAL, "NL-XXX-C00000001-A", "NL-XXX-C00000002-B"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 15, "NL-XXX-C00000002-B": " Tesla "},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        CONF_SCAN_INTERVAL: 15,
        CONF_CARD_NAMES: {"100002": "Tesla"},
    }

    coordinator = mock_config_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 15 * 60
    names = sorted(
        d.name
        for d in dr.async_entries_for_config_entry(
            device_registry, mock_config_entry.entry_id
        )
    )
    assert names == ["ANWB Laadpas", "Laadpas NL-XXX-C00000001-A", "Tesla"]
