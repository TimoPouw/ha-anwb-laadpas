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
SESSION_DETAIL_PATH: Final = (
    "/api/services/app/ChargingSessions/GetChargingSessionForView"
)
INVOICES_PATH: Final = "/api/services/app/Invoices/GetAll"
INVOICE_DOWNLOAD_PATH: Final = "/api/services/app/Invoices/Download"
TEMP_FILE_PATH: Final = "/File/DownloadTempFile"
OIDC_PROVIDER_NAME: Final = "OpenIdConnect"

PAGE_SIZE: Final = 50
MIN_PAGES_PER_UPDATE: Final = 2
MAX_PAGES_PER_UPDATE: Final = 100
INVOICE_PAGE_SIZE: Final = 100
# Session details cost one call each: new sessions first, then this many
# older ones per update until the history is complete.
DETAILS_PER_UPDATE: Final = 20

CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_AUTH_CONFIG: Final = "auth_config"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_CARD_NAMES: Final = "card_names"
CONF_AUTO_DOWNLOAD: Final = "auto_download_invoices"

DEFAULT_SCAN_INTERVAL_MIN: Final = 30
MIN_SCAN_INTERVAL_MIN: Final = 5
MAX_SCAN_INTERVAL_MIN: Final = 1440

EVENT_NEW_SESSION: Final = f"{DOMAIN}_new_session"
EVENT_NEW_INVOICE: Final = f"{DOMAIN}_new_invoice"
MEDIA_SUBDIR: Final = DOMAIN

STORAGE_VERSION: Final = 1
