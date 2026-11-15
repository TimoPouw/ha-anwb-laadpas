"""Constants for the ANWB Laadpas integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "anwb_laadpas"
MANUFACTURER: Final = "ANWB"

# The only fixed value: the ANWB Laadpas web app. Everything else (API base URL,
# OAuth client id, endpoints, scope and tenant) is discovered from it at runtime
# and cached in the config entry as a fallback.
PORTAL_URL: Final = "https://laadpas.anwb.nl"
APP_CONFIG_PATH: Final = "/assets/appconfig.production.json"
REDIRECT_PATH: Final = "/account/login"
PROVIDERS_PATH: Final = "/api/TokenAuth/GetExternalAuthenticationProviders"
TENANT_PATH: Final = "/api/services/app/Account/GetIdentityTenantId"
SESSIONS_PATH: Final = "/api/services/app/ChargingSessions/GetAll"
OIDC_PROVIDER_NAME: Final = "OpenIdConnect"

PAGE_SIZE: Final = 50
MIN_PAGES_PER_UPDATE: Final = 2
MAX_PAGES_PER_UPDATE: Final = 100

CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_AUTH_CONFIG: Final = "auth_config"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_CARD_NAMES: Final = "card_names"

DEFAULT_SCAN_INTERVAL_MIN: Final = 30
MIN_SCAN_INTERVAL_MIN: Final = 5
MAX_SCAN_INTERVAL_MIN: Final = 1440

EVENT_NEW_SESSION: Final = f"{DOMAIN}_new_session"

STORAGE_VERSION: Final = 1
