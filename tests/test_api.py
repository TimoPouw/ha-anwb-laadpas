"""Unit tests for the API client and helpers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.anwb_laadpas.api import (
    AnwbLaadpasClient,
    ApiError,
    AuthConfig,
    AuthError,
    InvalidRedirectUrl,
    PkceLogin,
    async_discover_auth_config,
    parse_redirect_url,
)
from custom_components.anwb_laadpas.models import ChargingSession

from .conftest import AUTH_CONFIG, CLIENT_ID, TENANT_ID, FakeDeftpower, load_sessions


def test_pkce_challenge_is_s256() -> None:
    import base64
    import hashlib

    login = PkceLogin.generate()
    digest = hashlib.sha256(login.verifier.encode()).digest()
    assert login.challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert login.state != PkceLogin.generate().state


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://laadpas.anwb.nl/account/login?code=abc&state=other", "state"),
        ("https://laadpas.anwb.nl/account/login?state=s", "no code"),
        ("https://laadpas.anwb.nl/account/login?error=access_denied&state=s", "access"),
        ("not a url", "state"),
    ],
)
def test_parse_redirect_url_errors(url: str, message: str) -> None:
    with pytest.raises(InvalidRedirectUrl, match=message):
        parse_redirect_url(url, "s")


def test_parse_redirect_url() -> None:
    url = " https://laadpas.anwb.nl/account/login?code=ABC-1&state=s&iss=x \n"
    assert parse_redirect_url(url, "s") == "ABC-1"


def test_session_parsing_edge_cases() -> None:
    items = load_sessions()
    fractional = ChargingSession.from_api(items[3])
    assert fractional.start.microsecond == 0
    assert fractional.start.tzinfo is not None

    no_end = ChargingSession.from_api(items[12])
    assert no_end.end is None
    assert no_end.duration is None
    assert no_end.as_event_data(None)["duration_minutes"] is None

    no_cost = ChargingSession.from_api(items[7])
    assert no_cost.cost is None

    roundtrip = ChargingSession.from_storage(no_end.as_storage())
    assert roundtrip == no_end


async def test_discovery(hass: HomeAssistant, fake_api: FakeDeftpower) -> None:
    config = await async_discover_auth_config(async_get_clientsession(hass))
    assert config.client_id == CLIENT_ID
    assert config.tenant_id == TENANT_ID
    assert config.scope == "openid backend offline_access"
    assert config.redirect_uri == "https://laadpas.anwb.nl/account/login"
    # The tenant is resolved from the Origin header of the web app.
    tenant_call = next(
        c for c in fake_api.mocker.mock_calls if "IdentityTenant" in str(c[1])
    )
    assert tenant_call[3]["Origin"] == "https://laadpas.anwb.nl"


async def test_client_refreshes_and_rotates(
    hass: HomeAssistant, fake_api: FakeDeftpower
) -> None:
    seen: list[str] = []
    client = AnwbLaadpasClient(
        async_get_clientsession(hass),
        AuthConfig.from_dict(AUTH_CONFIG),
        "refresh-0",
        seen.append,
    )
    page = await client.async_get_sessions(0, 10)
    assert page.total_count == 30
    assert len(page.sessions) == 10
    assert seen == ["refresh-1"]
    assert client.refresh_token == "refresh-1"

    # The access token is reused while valid.
    await client.async_get_sessions(10, 10)
    assert fake_api.refresh_counter == 1
    auth = [c[3]["Authorization"] for c in fake_api.session_calls()]
    assert auth[0] == auth[1]
    assert auth[0].startswith("Bearer ")


async def test_client_auth_error(hass: HomeAssistant, fake_api: FakeDeftpower) -> None:
    client = AnwbLaadpasClient(
        async_get_clientsession(hass), AuthConfig.from_dict(AUTH_CONFIG), "revoked"
    )
    with pytest.raises(AuthError):
        await client.async_get_sessions()


async def test_client_server_error(
    hass: HomeAssistant, fake_api: FakeDeftpower
) -> None:
    fake_api.sessions_status = 500
    client = AnwbLaadpasClient(
        async_get_clientsession(hass), AuthConfig.from_dict(AUTH_CONFIG), "refresh-0"
    )
    with pytest.raises(ApiError):
        await client.async_get_sessions()


async def test_client_unauthorized_redirect(
    hass: HomeAssistant, fake_api: FakeDeftpower
) -> None:
    fake_api.sessions_status = 302
    client = AnwbLaadpasClient(
        async_get_clientsession(hass), AuthConfig.from_dict(AUTH_CONFIG), "refresh-0"
    )
    with pytest.raises(AuthError):
        await client.async_get_sessions()
    # Initial token plus one forced refresh after the redirect.
    assert fake_api.refresh_counter == 2
