"""Config flow for ANWB Laadpas."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from . import AnwbLaadpasConfigEntry
from .api import (
    AnwbLaadpasClient,
    ApiError,
    AuthConfig,
    AuthError,
    InvalidRedirectUrl,
    PkceLogin,
    async_discover_auth_config,
    async_exchange_code,
    build_authorize_url,
    parse_redirect_url,
)
from .const import (
    CONF_AUTH_CONFIG,
    CONF_AUTO_DOWNLOAD,
    CONF_CARD_NAMES,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    MAX_SCAN_INTERVAL_MIN,
    MIN_SCAN_INTERVAL_MIN,
)

_LOGGER = logging.getLogger(__name__)

CONF_REDIRECT_URL = "redirect_url"
TITLE = "ANWB Laadpas"


class AnwbLaadpasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ANWB Laadpas."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._auth_config: AuthConfig | None = None
        self._login: PkceLogin | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: AnwbLaadpasConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return AnwbLaadpasOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover the login settings and let the user pick a method."""
        try:
            self._auth_config = await async_discover_auth_config(
                async_get_clientsession(self.hass)
            )
        except ApiError as err:
            _LOGGER.warning("Could not discover ANWB Laadpas settings: %s", err)
            return self.async_abort(reason="cannot_connect")
        return self.async_show_menu(step_id="user", menu_options=["login", "token"])

    async def async_step_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in via the browser and paste the URL you land on."""
        assert self._auth_config is not None
        errors: dict[str, str] = {}

        if user_input is not None and self._login is not None:
            session = async_get_clientsession(self.hass)
            try:
                code = parse_redirect_url(
                    user_input[CONF_REDIRECT_URL], self._login.state
                )
                tokens = await async_exchange_code(
                    session, self._auth_config, code, self._login.verifier
                )
            except InvalidRedirectUrl:
                errors["base"] = "invalid_redirect_url"
            except AuthError:
                # The code is used or expired: start over with a new login.
                errors["base"] = "invalid_code"
                self._login = None
            except ApiError:
                errors["base"] = "cannot_connect"
            else:
                if not tokens.get("refresh_token"):
                    return self.async_abort(reason="no_refresh_token")
                client = AnwbLaadpasClient(
                    session,
                    self._auth_config,
                    tokens["refresh_token"],
                    access_token=tokens["access_token"],
                )
                return await self._async_finish(client)

        if self._login is None:
            self._login = PkceLogin.generate()

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REDIRECT_URL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    )
                }
            ),
            description_placeholders={
                "authorize_url": build_authorize_url(self._auth_config, self._login)
            },
            errors=errors,
        )

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste a refresh token copied from the browser."""
        assert self._auth_config is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            client = AnwbLaadpasClient(
                async_get_clientsession(self.hass),
                self._auth_config,
                user_input[CONF_REFRESH_TOKEN].strip(),
            )
            try:
                # This rotates the token; the client keeps the new one.
                await client.async_refresh_access_token()
            except AuthError:
                errors["base"] = "invalid_auth"
            except ApiError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish(client)

        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REFRESH_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    async def _async_finish(self, client: AnwbLaadpasClient) -> ConfigFlowResult:
        """Validate access to the sessions and create or update the entry."""
        try:
            user_id = await client.async_get_user_id()
            await client.async_get_sessions(0, 1)
        except AuthError:
            return self.async_abort(reason="invalid_auth")
        except ApiError:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(user_id)
        data = {
            CONF_REFRESH_TOKEN: client.refresh_token,
            CONF_AUTH_CONFIG: client.config.as_dict(),
        }
        if self.source == "reauth":
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=TITLE, data=data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication when the refresh token stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain why a new login is needed, then show the login methods."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()


class AnwbLaadpasOptionsFlow(OptionsFlow):
    """Polling interval and names for the charging cards."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        entry: AnwbLaadpasConfigEntry = self.config_entry
        coordinator = getattr(entry, "runtime_data", None)
        cards: dict[str, str | None] = (
            coordinator.data.cards if coordinator and coordinator.data else {}
        )
        names: dict[str, str] = dict(entry.options.get(CONF_CARD_NAMES, {}))
        # One text field per card, labelled with the card number.
        field_to_card = {
            (number or card_id): card_id for card_id, number in cards.items()
        }

        if user_input is not None:
            card_names = {
                field_to_card[field]: value.strip()
                for field, value in user_input.items()
                if field in field_to_card and value and value.strip()
            }
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_AUTO_DOWNLOAD: bool(user_input.get(CONF_AUTO_DOWNLOAD, False)),
                    CONF_CARD_NAMES: card_names,
                }
            )

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL_MIN,
                    max=MAX_SCAN_INTERVAL_MIN,
                    step=1,
                    unit_of_measurement="min",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_AUTO_DOWNLOAD,
                default=entry.options.get(CONF_AUTO_DOWNLOAD, False),
            ): BooleanSelector(),
        }
        for field, card_id in field_to_card.items():
            current = names.get(card_id)
            key = (
                vol.Optional(field, description={"suggested_value": current})
                if current
                else vol.Optional(field)
            )
            schema[key] = TextSelector()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            description_placeholders={"card_count": str(len(field_to_card))},
        )
