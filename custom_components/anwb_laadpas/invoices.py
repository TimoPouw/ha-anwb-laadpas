"""Saving invoice PDFs to the Home Assistant media folder."""

from __future__ import annotations

from pathlib import Path
import re

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import AnwbLaadpasClient
from .const import MEDIA_SUBDIR
from .models import Invoice

INVOICE = "invoice"
SPECIFICATION = "specification"


def invoice_folder(hass: HomeAssistant) -> Path:
    """Return the folder for invoices, inside the local media source."""
    media_root = hass.config.media_dirs.get("local") or hass.config.path("media")
    return Path(media_root) / MEDIA_SUBDIR


def invoice_file_name(invoice: Invoice, kind: str) -> str:
    """Return a sortable, filesystem safe file name."""
    # Invoice dates are calendar dates sent as midnight UTC: keep the UTC date.
    date = dt_util.as_utc(invoice.date).date().isoformat() if invoice.date else "nodate"
    number = re.sub(r"[^A-Za-z0-9_-]", "_", invoice.number)
    return f"{date}_{number}_{kind}.pdf"


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


async def async_save_invoice(
    hass: HomeAssistant,
    client: AnwbLaadpasClient,
    invoice: Invoice,
    kinds: tuple[str, ...] = (INVOICE, SPECIFICATION),
    overwrite: bool = False,
) -> list[str]:
    """Download an invoice (and/or its specification) and return the paths."""
    folder = invoice_folder(hass)
    paths: list[str] = []
    for kind in kinds:
        path = folder / invoice_file_name(invoice, kind)
        exists = await hass.async_add_executor_job(path.exists)
        if not exists or overwrite:
            _, content = await client.async_download_invoice(
                invoice, specification=kind == SPECIFICATION
            )
            await hass.async_add_executor_job(_write, path, content)
        paths.append(str(path))
    return paths
