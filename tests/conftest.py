"""Fixtures for ANWB Laadpas tests."""

from __future__ import annotations

import base64
from collections.abc import Generator
import copy
import json
from pathlib import Path
import time
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.anwb_laadpas.const import (
    CONF_AUTH_CONFIG,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

FIXTURES = Path(__file__).parent / "fixtures"

PORTAL = "https://laadpas.anwb.nl"
API = "https://portal-api.example.test"
IDP = "https://account.example.test"
CLIENT_ID = "00000000-0000-4000-8000-0000000000c1"
TENANT_ID = "00000000-0000-4000-8000-0000000000e1"
USER_ID = "00000000-0000-4000-8000-0000000000a1"

AUTH_CONFIG = {
    "api_base": API,
    "client_id": CLIENT_ID,
    "authorize_url": f"{IDP}/connect/authorize",
    "token_url": f"{IDP}/connect/token",
    "scope": "openid backend offline_access",
    "tenant_id": TENANT_ID,
    "redirect_uri": f"{PORTAL}/account/login",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in all tests."""


def load_sessions() -> list[dict[str, Any]]:
    """Return the fictional session list (newest first)."""
    return json.loads((FIXTURES / "sessions.json").read_text())


def make_jwt(claims: dict[str, Any]) -> str:
    """Build an unsigned JWT for tests."""

    def enc(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def abp(result: Any, success: bool = True) -> dict[str, Any]:
    """Wrap a result in the ABP response envelope."""
    return {
        "result": result,
        "targetUrl": None,
        "success": success,
        "error": None,
        "unAuthorizedRequest": False,
        "__abp": True,
    }


class FakeDeftpower:
    """A fake of the web app config, identity server and sessions API."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        """Register all endpoints on the aiohttp mocker."""
        self.mocker = mocker
        self.sessions = load_sessions()
        self.refresh_counter = 0
        self.valid_refresh_token = "refresh-0"
        self.token_error: str | None = None
        self.sessions_status = 200
        self.reject_access_tokens: set[str] = set()
        self.issued_codes: dict[str, str] = {}
        self.client_id = CLIENT_ID
        self.app_config_status = 200
        self.tenant_id: str | None = TENANT_ID

        mocker.get(
            f"{PORTAL}/assets/appconfig.production.json", side_effect=self._app_config
        )
        mocker.get(
            f"{API}/api/TokenAuth/GetExternalAuthenticationProviders",
            side_effect=self._providers,
        )
        mocker.get(
            f"{API}/api/services/app/Account/GetIdentityTenantId",
            side_effect=self._tenant,
        )
        mocker.post(f"{IDP}/connect/token", side_effect=self._token)
        mocker.get(
            f"{API}/api/services/app/ChargingSessions/GetAll",
            side_effect=self._sessions,
        )

    def add_session(self, item: dict[str, Any]) -> None:
        """Add a session at the top of the list."""
        self.sessions.insert(0, item)

    def session_calls(self) -> list[Any]:
        """Return the recorded calls to the sessions endpoint."""
        return [c for c in self.mocker.mock_calls if "ChargingSessions" in str(c[1])]

    async def _app_config(self, method: str, url: Any, data: Any) -> Any:
        return AiohttpClientMockResponse(
            method,
            url,
            status=self.app_config_status,
            json={"remoteServiceBaseUrl": API, "accountBaseUrl": IDP},
        )

    async def _tenant(self, method: str, url: Any, data: Any) -> Any:
        return AiohttpClientMockResponse(method, url, json=abp(self.tenant_id))

    async def _providers(self, method: str, url: Any, data: Any) -> Any:
        return AiohttpClientMockResponse(
            method,
            url,
            json=abp(
                [
                    {
                        "name": "OpenIdConnect",
                        "clientId": self.client_id,
                        "additionalParams": {
                            "LoginUrl": f"{IDP}/connect/authorize",
                            "TokenEndpoint": f"{IDP}/connect/token",
                            "Authority": IDP,
                            "Scope": "openid backend",
                        },
                    }
                ]
            ),
        )

    def _issue(self) -> dict[str, Any]:
        self.refresh_counter += 1
        self.valid_refresh_token = f"refresh-{self.refresh_counter}"
        access = make_jwt(
            {"sub": USER_ID, "exp": time.time() + 28800, "n": self.refresh_counter}
        )
        return {
            "access_token": access,
            "expires_in": 28800,
            "refresh_token": self.valid_refresh_token,
            "token_type": "Bearer",
        }

    async def _token(self, method: str, url: Any, data: Any) -> Any:
        form = dict(data or {})
        if self.token_error:
            return AiohttpClientMockResponse(
                method, url, status=400, json={"error": self.token_error}
            )
        if form.get("client_id") != self.client_id:
            return AiohttpClientMockResponse(
                method, url, status=400, json={"error": "invalid_client"}
            )
        grant = form.get("grant_type")
        if (
            grant == "refresh_token"
            and form.get("refresh_token") == self.valid_refresh_token
        ):
            return AiohttpClientMockResponse(method, url, json=self._issue())
        if grant == "authorization_code" and form.get("code") in self.issued_codes:
            del self.issued_codes[form["code"]]
            return AiohttpClientMockResponse(method, url, json=self._issue())
        return AiohttpClientMockResponse(
            method, url, status=400, json={"error": "invalid_grant"}
        )

    async def _sessions(self, method: str, url: Any, data: Any) -> Any:
        if self.sessions_status != 200:
            return AiohttpClientMockResponse(
                method,
                url,
                status=self.sessions_status,
                headers={"Location": "/Error?statusCode=401"},
            )
        query = parse_qs(url.query_string)
        offset = int(query["SkipCount"][0])
        limit = int(query["MaxResultCount"][0])
        page = copy.deepcopy(self.sessions[offset : offset + limit])
        return AiohttpClientMockResponse(
            method,
            url,
            json=abp(
                {
                    "meta": {
                        "offset": offset,
                        "limit": limit,
                        "nextLink": None,
                        "totalCount": len(self.sessions),
                    },
                    "result": page,
                }
            ),
        )


@pytest.fixture
def fake_api(aioclient_mock: AiohttpClientMocker) -> FakeDeftpower:
    """Return the fake Deftpower backend."""
    return FakeDeftpower(aioclient_mock)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the fake account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="ANWB Laadpas",
        unique_id=USER_ID,
        data={CONF_REFRESH_TOKEN: "refresh-0", CONF_AUTH_CONFIG: dict(AUTH_CONFIG)},
    )


@pytest.fixture
def small_pages() -> Generator[None]:
    """Use small pages so pagination is exercised with the fixture."""
    with patch("custom_components.anwb_laadpas.coordinator.PAGE_SIZE", 5):
        yield
