"""Download invoice PDFs from Home Assistant, with signed links."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
import time
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .api import ApiError, AuthError
from .const import DOMAIN
from .invoices import INVOICE, SPECIFICATION, async_save_invoice
from .models import Invoice

URL_TEMPLATE = "/api/anwb_laadpas/invoice/{entry_id}/{invoice_id}/{kind}"
# Links in sensor attributes are valid this long and renewed a day before.
LINK_VALIDITY = timedelta(days=2)
LINK_RENEW_BEFORE = timedelta(days=1)
DATA_LINKS = f"{DOMAIN}_signed_links"


@callback
def async_invoice_links(
    hass: HomeAssistant, entry_id: str, invoice: Invoice
) -> dict[str, str | None]:
    """Return signed download links for an invoice and its specification.

    Links are cached, so sensor attributes do not change on every update.
    """
    if not invoice.has_pdf:
        return {"pdf_url": None, "specification_url": None}
    cache: dict[str, tuple[str, float]] = hass.data.setdefault(DATA_LINKS, {})
    now = time.time()
    links: dict[str, str | None] = {}
    for kind, key in ((INVOICE, "pdf_url"), (SPECIFICATION, "specification_url")):
        path = URL_TEMPLATE.format(entry_id=entry_id, invoice_id=invoice.id, kind=kind)
        cached = cache.get(path)
        if cached is None or cached[1] - now < LINK_RENEW_BEFORE.total_seconds():
            # Signed for the internal content user: works in a browser without
            # a login header, only for this path and only until it expires.
            url = async_sign_path(hass, path, LINK_VALIDITY, use_content_user=True)
            cached = (url, now + LINK_VALIDITY.total_seconds())
            cache[path] = cached
        links[key] = cached[0]
    return links


class InvoiceDownloadView(HomeAssistantView):
    """Serve an invoice or specification PDF as a download."""

    url = "/api/anwb_laadpas/invoice/{entry_id}/{invoice_id}/{kind}"
    name = "api:anwb_laadpas:invoice"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, invoice_id: str, kind: str
    ) -> web.StreamResponse:
        """Return the PDF, downloading it from ANWB if not stored yet."""
        hass: HomeAssistant = request.app["hass"]
        if kind not in (INVOICE, SPECIFICATION):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        coordinator = entry.runtime_data
        invoice = next(
            (i for i in coordinator.data.invoices if i.id == invoice_id), None
        )
        if invoice is None or not invoice.has_pdf:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        try:
            paths = await async_save_invoice(hass, coordinator.client, invoice, (kind,))
        except (ApiError, AuthError):
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        path = Path(paths[0])
        content = await hass.async_add_executor_job(path.read_bytes)
        return web.Response(
            body=content,
            content_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{path.name}"',
                "Cache-Control": "private, no-store",
            },
        )


@callback
def async_register_views(hass: HomeAssistant) -> None:
    """Register the download view."""
    hass.http.register_view(InvoiceDownloadView())


def links_for_rows(
    hass: HomeAssistant, entry_id: str, invoices: list[Invoice]
) -> list[dict[str, Any]]:
    """Return invoice rows with download links."""
    return [
        {**invoice.as_row(), **async_invoice_links(hass, entry_id, invoice)}
        for invoice in invoices
    ]
