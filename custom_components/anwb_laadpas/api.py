"""Async client for the ANWB Laadpas (Deftpower) API.

Has no Home Assistant dependencies apart from the session model, so it can be
tested in isolation. Pass in the aiohttp ClientSession from Home Assistant.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import aiohttp

from .const import (
    APP_CONFIG_PATH,
    INVOICE_DOWNLOAD_PATH,
    INVOICE_PAGE_SIZE,
    INVOICES_PATH,
    OIDC_PROVIDER_NAME,
    PORTAL_URL,
    PROVIDERS_PATH,
    REDIRECT_PATH,
    SESSION_DETAIL_PATH,
    SESSIONS_PATH,
    TEMP_FILE_PATH,
    TENANT_PATH,
)
from .models import ChargingSession, Invoice, SessionDetails, SessionPage

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Refresh the access token this many seconds before it actually expires.
EXPIRY_MARGIN = 120


class AnwbLaadpasError(Exception):
    """Base error for this integration."""


class AuthError(AnwbLaadpasError):
    """Authentication failed: the refresh token is invalid or revoked."""


class ApiError(AnwbLaadpasError):
    """The API returned an unexpected response or could not be reached."""


class InvalidRedirectUrl(AnwbLaadpasError):
    """The pasted redirect URL does not contain a usable authorization code."""


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """OAuth and API settings discovered from the ANWB Laadpas web app."""

    api_base: str
    client_id: str
    authorize_url: str
    token_url: str
    scope: str
    tenant_id: str | None
    redirect_uri: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the config entry."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthConfig:
        """Deserialize from the config entry."""
        return cls(**data)


@dataclass(frozen=True, slots=True)
class PkceLogin:
    """State of one authorization code + PKCE login attempt."""

    verifier: str
    challenge: str
    state: str
    nonce: str

    @classmethod
    def generate(cls) -> PkceLogin:
        """Create a fresh verifier, S256 challenge, state and nonce."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return cls(
            verifier=verifier,
            challenge=challenge,
            state=secrets.token_urlsafe(24),
            nonce=secrets.token_urlsafe(24),
        )


def _portal_headers() -> dict[str, str]:
    # The API resolves the ANWB tenant from the Referer of the web app.
    return {
        "Origin": PORTAL_URL,
        "Referer": f"{PORTAL_URL}/",
        "Accept": "application/json",
    }


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    try:
        async with session.get(
            url, headers=_portal_headers(), timeout=REQUEST_TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise ApiError(f"GET {url} returned HTTP {resp.status}")
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise ApiError(f"GET {url} failed: {err}") from err


async def async_discover_auth_config(session: aiohttp.ClientSession) -> AuthConfig:
    """Discover the API base, OAuth client and tenant from the web app."""
    app_config = await _get_json(session, f"{PORTAL_URL}{APP_CONFIG_PATH}")
    api_base = str(app_config.get("remoteServiceBaseUrl") or "").rstrip("/")
    if not api_base.startswith("https://"):
        raise ApiError("No remoteServiceBaseUrl in app config")

    providers = await _get_json(session, f"{api_base}{PROVIDERS_PATH}")
    provider = next(
        (
            p
            for p in (providers or {}).get("result") or []
            if p.get("name") == OIDC_PROVIDER_NAME
        ),
        None,
    )
    if not provider or not provider.get("clientId"):
        raise ApiError("No OpenIdConnect provider found")
    params = provider.get("additionalParams") or {}
    scope = str(params.get("Scope") or "openid")
    if "offline_access" not in scope.split():
        # The web app adds this too; it is what gives us a refresh token.
        scope = f"{scope} offline_access"

    tenant = await _get_json(session, f"{api_base}{TENANT_PATH}")
    tenant_id = (tenant or {}).get("result") or None

    authority = str(params.get("Authority") or "").rstrip("/")
    return AuthConfig(
        api_base=api_base,
        client_id=provider["clientId"],
        authorize_url=params.get("LoginUrl") or f"{authority}/connect/authorize",
        token_url=params.get("TokenEndpoint") or f"{authority}/connect/token",
        scope=scope,
        tenant_id=tenant_id,
        redirect_uri=f"{PORTAL_URL}{REDIRECT_PATH}",
    )


def build_authorize_url(config: AuthConfig, login: PkceLogin) -> str:
    """Return the URL the user opens to log in."""
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "response_mode": "query",
        "scope": config.scope,
        "state": login.state,
        "nonce": login.nonce,
        "code_challenge": login.challenge,
        "code_challenge_method": "S256",
    }
    if config.tenant_id:
        params["acr_values"] = f"tenant:{config.tenant_id} autoRedirect:true"
    return f"{config.authorize_url}?{urlencode(params, quote_via=quote)}"


def parse_redirect_url(url: str, expected_state: str) -> str:
    """Extract the authorization code from the URL the user landed on."""
    query = parse_qs(urlparse(url.strip()).query)
    if "error" in query:
        raise InvalidRedirectUrl(query["error"][0])
    if query.get("state", [None])[0] != expected_state:
        raise InvalidRedirectUrl("state mismatch")
    code = query.get("code", [None])[0]
    if not code:
        raise InvalidRedirectUrl("no code in URL")
    return code


def jwt_claims(token: str) -> dict[str, Any]:
    """Decode the payload of a JWT without verifying it."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


async def _token_request(
    session: aiohttp.ClientSession, config: AuthConfig, data: dict[str, str]
) -> dict[str, Any]:
    data = {**data, "client_id": config.client_id}
    try:
        async with session.post(
            config.token_url,
            data=data,
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            body = await resp.json(content_type=None)
            status = resp.status
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise ApiError(f"Token request failed: {err}") from err

    if status == 200 and isinstance(body, dict) and body.get("access_token"):
        return body
    error = body.get("error") if isinstance(body, dict) else None
    if status in (400, 401) and error in (
        "invalid_grant",
        "invalid_client",
        "unauthorized_client",
    ):
        raise AuthError(error)
    raise ApiError(f"Token endpoint returned HTTP {status} ({error})")


async def async_exchange_code(
    session: aiohttp.ClientSession, config: AuthConfig, code: str, verifier: str
) -> dict[str, Any]:
    """Exchange an authorization code for tokens."""
    return await _token_request(
        session,
        config,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "code_verifier": verifier,
        },
    )


class AnwbLaadpasClient:
    """Client for the charging sessions API with automatic token refresh."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        config: AuthConfig,
        refresh_token: str,
        on_refresh_token: Callable[[str], None] | None = None,
        access_token: str | None = None,
    ) -> None:
        """Initialize the client.

        on_refresh_token is called with every new refresh token, because the
        identity server rotates them and the old one stops working.
        """
        self._session = session
        self._config = config
        self._refresh_token = refresh_token
        self._on_refresh_token = on_refresh_token
        self._access_token = access_token
        self._expires_at = (
            float(jwt_claims(access_token).get("exp", 0)) if access_token else 0.0
        )
        self._lock = asyncio.Lock()

    @property
    def config(self) -> AuthConfig:
        """Return the auth configuration in use."""
        return self._config

    @property
    def refresh_token(self) -> str:
        """Return the current refresh token."""
        return self._refresh_token

    async def async_refresh_access_token(self) -> None:
        """Get a new access token using the refresh token."""
        async with self._lock:
            await self._refresh_locked()

    async def _refresh_locked(self) -> None:
        tokens = await _token_request(
            self._session,
            self._config,
            {"grant_type": "refresh_token", "refresh_token": self._refresh_token},
        )
        self._access_token = tokens["access_token"]
        self._expires_at = time.time() + float(tokens.get("expires_in", 3600))
        new_refresh = tokens.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._on_refresh_token:
                self._on_refresh_token(new_refresh)

    async def _async_access_token(self, force_refresh: bool = False) -> str:
        async with self._lock:
            if (
                force_refresh
                or not self._access_token
                or time.time() > self._expires_at - EXPIRY_MARGIN
            ):
                await self._refresh_locked()
            assert self._access_token is not None
            return self._access_token

    async def async_get_user_id(self) -> str:
        """Return a stable id for the account (the 'sub' claim)."""
        claims = jwt_claims(await self._async_access_token())
        user_id = claims.get("sub") or claims.get("user_id")
        if not user_id:
            raise ApiError("Access token has no subject")
        return str(user_id)

    async def _async_request(
        self, method: str, path: str, params: dict[str, Any], *, raw: bool = False
    ) -> Any:
        """Call the API, refreshing the access token once on 401.

        Returns the ABP 'result', or the response bytes when raw is True.
        """
        url = f"{self._config.api_base}{path}"
        for attempt in range(2):
            token = await self._async_access_token(force_refresh=attempt > 0)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                async with self._session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    allow_redirects=False,
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    status = resp.status
                    location = resp.headers.get("Location", "")
                    body: Any = None
                    if status == 200:
                        body = (
                            await resp.read()
                            if raw
                            else await resp.json(content_type=None)
                        )
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise ApiError(f"{method} {path} failed: {err}") from err

            # Without a valid token the API redirects to /Error?statusCode=401
            # instead of answering 401 itself.
            unauthorized = status in (401, 403) or (
                300 <= status < 400 and "401" in location
            )
            if status == 200 and raw:
                return body
            if status == 200 and isinstance(body, dict):
                if body.get("unAuthorizedRequest"):
                    unauthorized = True
                elif not body.get("success", True):
                    message = (body.get("error") or {}).get("message")
                    raise ApiError(f"{method} {path} failed: {message}")
                else:
                    return body.get("result")
            if not unauthorized:
                raise ApiError(f"{method} {path} returned HTTP {status}")
            _LOGGER.debug("Unauthorized on %s, refreshing token", path)
        raise AuthError("Unauthorized after refreshing the access token")

    async def _async_get(self, path: str, params: dict[str, Any]) -> Any:
        return await self._async_request("GET", path, params)

    async def async_get_sessions(self, offset: int = 0, limit: int = 50) -> SessionPage:
        """Fetch one page of completed sessions, newest first."""
        result = await self._async_get(
            SESSIONS_PATH,
            {
                "SessionStatusFilter": "COMPLETED",
                "IncludeTotalCount": "true",
                "Sorting": "startDateTime DESC",
                "SkipCount": offset,
                "MaxResultCount": limit,
            },
        )
        if not isinstance(result, dict):
            raise ApiError("Unexpected sessions response")
        sessions: list[ChargingSession] = []
        for item in result.get("result") or []:
            try:
                sessions.append(ChargingSession.from_api(item))
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Skipping session that could not be parsed: %s", err)
        total = (result.get("meta") or {}).get("totalCount")
        return SessionPage(
            total_count=int(total) if total is not None else len(sessions),
            sessions=sessions,
        )

    async def async_get_session_details(self, session_id: str) -> SessionDetails:
        """Fetch the extra fields of one session."""
        result = await self._async_get(SESSION_DETAIL_PATH, {"id": session_id})
        if not isinstance(result, dict):
            raise ApiError("Unexpected session detail response")
        return SessionDetails.from_api(result)

    async def async_get_invoices(self) -> list[Invoice]:
        """Fetch all invoices."""
        invoices: list[Invoice] = []
        offset = 0
        while True:
            result = await self._async_get(
                INVOICES_PATH,
                {"SkipCount": offset, "MaxResultCount": INVOICE_PAGE_SIZE},
            )
            if not isinstance(result, dict):
                raise ApiError("Unexpected invoices response")
            items = result.get("items") or []
            for item in items:
                try:
                    invoices.append(Invoice.from_api(item))
                except (KeyError, TypeError, ValueError) as err:
                    _LOGGER.warning(
                        "Skipping invoice that could not be parsed: %s", err
                    )
            offset += INVOICE_PAGE_SIZE
            if not items or offset >= int(result.get("totalCount") or 0):
                return invoices

    async def async_download_invoice(
        self, invoice: Invoice, specification: bool = False
    ) -> tuple[str, bytes]:
        """Download the invoice or its specification as PDF.

        Returns the file name suggested by the server and the content.
        """
        params: dict[str, Any] = {"id": invoice.id, "invoiceNumber": invoice.number}
        if specification:
            params["type"] = "specification"
        meta = await self._async_request("POST", INVOICE_DOWNLOAD_PATH, params)
        if not isinstance(meta, dict) or not meta.get("fileToken"):
            raise ApiError("Unexpected download response")
        content = await self._async_request(
            "GET",
            TEMP_FILE_PATH,
            {
                "fileType": meta.get("fileType", ""),
                "fileToken": meta["fileToken"],
                "fileName": meta.get("fileName", ""),
            },
            raw=True,
        )
        if not content.startswith(b"%PDF"):
            raise ApiError("Downloaded file is not a PDF")
        kind = "specification" if specification else "invoice"
        return str(meta.get("fileName") or f"{kind}-{invoice.number}.pdf"), content
