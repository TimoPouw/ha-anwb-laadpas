"""Data update coordinator for ANWB Laadpas."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AnwbLaadpasClient, ApiError, AuthError
from .const import (
    BACKFILL_DELAY,
    BACKFILL_SAVE_EVERY,
    CONF_AUTO_DOWNLOAD,
    CONF_CARD_NAMES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    EVENT_NEW_INVOICE,
    EVENT_NEW_SESSION,
    MAX_PAGES_PER_UPDATE,
    MIN_PAGES_PER_UPDATE,
    PAGE_SIZE,
    STORAGE_VERSION,
)
from .invoices import async_save_invoice
from .models import EMPTY_DETAILS, ChargingSession, Invoice

if TYPE_CHECKING:
    from . import AnwbLaadpasConfigEntry

_LOGGER = logging.getLogger(__name__)

# Give up on the details of a session after this many failed attempts.
MAX_DETAIL_ATTEMPTS = 3


@dataclass(slots=True)
class LaadpasData:
    """All known sessions plus derived figures, used by the sensors."""

    sessions: list[ChargingSession] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)

    def for_card(self, card_id: str | None) -> list[ChargingSession]:
        """Return the sessions of one card, or all sessions for None."""
        if card_id is None:
            return self.sessions
        return [s for s in self.sessions if s.card_id == card_id]

    @property
    def cards(self) -> dict[str, str | None]:
        """Return card id -> card number for every card seen."""
        cards: dict[str, str | None] = {}
        for session in self.sessions:
            if session.card_id is not None:
                cards.setdefault(session.card_id, session.card_number)
        return cards

    def last(self, card_id: str | None = None) -> ChargingSession | None:
        """Return the most recent session."""
        sessions = self.for_card(card_id)
        return sessions[0] if sessions else None

    def this_month(self, card_id: str | None = None) -> list[ChargingSession]:
        """Return the sessions that started in the current local month."""
        start = month_start()
        return [s for s in self.for_card(card_id) if s.start >= start]

    def total_energy(self, card_id: str | None = None) -> float:
        """Return the total energy in kWh."""
        return round(sum(s.energy_kwh for s in self.for_card(card_id)), 3)

    def total_cost(self, card_id: str | None = None) -> float:
        """Return the total costs including VAT."""
        return round(sum(s.cost or 0.0 for s in self.for_card(card_id)), 2)

    @property
    def last_invoice(self) -> Invoice | None:
        """Return the most recent invoice."""
        return self.invoices[0] if self.invoices else None

    @property
    def open_amount(self) -> float:
        """Return the total of invoices that are not paid yet."""
        return round(
            sum(i.amount_incl_vat or 0.0 for i in self.invoices if not i.is_paid), 2
        )

    @property
    def details_pending(self) -> int:
        """Return how many sessions still wait for their details."""
        return sum(1 for s in self.sessions if s.details is None)


def month_start() -> datetime:
    """Return the start of the current month in the local time zone."""
    return dt_util.start_of_local_day().replace(day=1)


class AnwbLaadpasCoordinator(DataUpdateCoordinator[LaadpasData]):
    """Fetch new charging sessions and keep all known ones in storage."""

    config_entry: AnwbLaadpasConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AnwbLaadpasConfigEntry,
        client: AnwbLaadpasClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN)
            ),
        )
        self.client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._sessions: dict[str, ChargingSession] = {}
        self._invoices: list[Invoice] = []
        self._known_invoice_ids: set[str] = set()
        self._detail_attempts: dict[str, int] = {}
        self._backfill_task: asyncio.Task[None] | None = None
        # Options the entities were last written with (see the update listener).
        self.applied_options: dict[str, Any] = dict(entry.options)
        # True once the full history has been loaded; events fire after that.
        self._synced = False

    async def _async_setup(self) -> None:
        """Load the sessions we already know from storage."""
        stored = await self._store.async_load() or {}
        self._synced = bool(stored.get("synced"))
        self._known_invoice_ids = set(stored.get("invoice_ids", []))
        for raw in stored.get("sessions", []):
            try:
                session = ChargingSession.from_storage(raw)
            except (KeyError, TypeError, ValueError):
                continue
            self._sessions[session.id] = session

    def card_name(self, card_id: str | None) -> str | None:
        """Return the user chosen name of a card, if any."""
        if card_id is None:
            return None
        return self.config_entry.options.get(CONF_CARD_NAMES, {}).get(card_id)

    async def _async_update_data(self) -> LaadpasData:
        first_sync = not self._synced
        try:
            fetched = await self._async_fetch_recent(first_sync)
            new = self._merge_sessions(fetched)
            await self._async_fetch_details(new)
            new_invoices = await self._async_fetch_invoices()
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ApiError as err:
            raise UpdateFailed(str(err)) from err

        self._synced = True
        await self._async_save()

        if first_sync:
            _LOGGER.debug(
                "Initial sync loaded %s sessions and %s invoices",
                len(new),
                len(self._invoices),
            )
        else:
            # Fire events oldest first, so automations see them in order.
            for listed in sorted(new, key=lambda s: s.start):
                # Use the stored version: it has the details fetched above.
                session = self._sessions[listed.id]
                self.hass.bus.async_fire(
                    EVENT_NEW_SESSION,
                    {
                        "config_entry_id": self.config_entry.entry_id,
                        **session.as_event_data(self.card_name(session.card_id)),
                    },
                )
            for invoice in sorted(
                new_invoices, key=lambda i: i.date or dt_util.utcnow()
            ):
                await self._async_new_invoice(invoice)

        self._async_start_backfill()
        return self._build_data()

    def _merge_sessions(self, fetched: list[ChargingSession]) -> list[ChargingSession]:
        """Add new sessions and apply later changes; return the new ones."""
        new: list[ChargingSession] = []
        for listed in fetched:
            known = self._sessions.get(listed.id)
            if known is None:
                new.append(listed)
                self._sessions[listed.id] = listed
            else:
                # Apply later changes (for example costs filled in afterwards),
                # keeping the details we already fetched: the list has none.
                self._sessions[listed.id] = listed.with_details(known.details)
        return new

    async def _async_fetch_details(self, new: list[ChargingSession]) -> None:
        """Fetch details for new sessions, so their event includes them.

        On the first sync every session is new; the background task fills those
        in instead, so the setup is not held up.
        """
        if not self._synced:
            return
        for session in new:
            await self._async_fetch_one_detail(session.id)

    async def _async_fetch_one_detail(self, session_id: str) -> None:
        """Fetch and store the details of one session.

        After MAX_DETAIL_ATTEMPTS failures the session gets empty details, so
        it is not retried forever. AuthError is raised to the caller.
        """
        try:
            details = await self.client.async_get_session_details(session_id)
        except ApiError as err:
            attempts = self._detail_attempts.get(session_id, 0) + 1
            self._detail_attempts[session_id] = attempts
            _LOGGER.debug("Details of %s failed (%s): %s", session_id, attempts, err)
            if attempts < MAX_DETAIL_ATTEMPTS:
                return
            details = EMPTY_DETAILS
        if session := self._sessions.get(session_id):
            self._sessions[session_id] = session.with_details(details)

    @callback
    def _async_start_backfill(self) -> None:
        """Start filling in details of older sessions, if needed."""
        if self._backfill_task and not self._backfill_task.done():
            return
        if not any(s.details is None for s in self._sessions.values()):
            return
        self._backfill_task = self.config_entry.async_create_background_task(
            self.hass, self._async_backfill(), name=f"{DOMAIN} details backfill"
        )

    async def _async_backfill(self) -> None:
        """Fetch missing details one by one, newest first, with a pause."""
        pending = [
            s.id
            for s in sorted(
                self._sessions.values(), key=lambda s: s.start, reverse=True
            )
            if s.details is None
        ]
        _LOGGER.debug("Filling in details of %s sessions", len(pending))
        done = 0
        try:
            for session_id in pending:
                session = self._sessions.get(session_id)
                if session is None or session.details is not None:
                    continue
                await self._async_fetch_one_detail(session_id)
                done += 1
                if done % BACKFILL_SAVE_EVERY == 0:
                    await self._async_publish()
                await asyncio.sleep(BACKFILL_DELAY)
        except AuthError as err:
            # The next regular update raises it again and starts reauth.
            _LOGGER.debug("Stopped filling in details: %s", err)
        finally:
            if done:
                await self._async_publish()

    async def _async_publish(self) -> None:
        """Save progress and show it, without moving the next update."""
        await self._async_save()
        self.data = self._build_data()
        self.async_update_listeners()

    def _build_data(self) -> LaadpasData:
        return LaadpasData(
            sessions=sorted(
                self._sessions.values(), key=lambda s: s.start, reverse=True
            ),
            invoices=list(self._invoices),
        )

    async def _async_fetch_invoices(self) -> list[Invoice]:
        """Refresh the invoices; return the ones not seen before."""
        try:
            invoices = await self.client.async_get_invoices()
        except AuthError:
            raise
        except ApiError as err:
            # Invoices are secondary: keep the last known list.
            _LOGGER.warning("Could not fetch invoices: %s", err)
            return []
        self._invoices = sorted(
            invoices,
            key=lambda i: i.date or datetime.min.replace(tzinfo=dt_util.UTC),
            reverse=True,
        )
        new = [i for i in self._invoices if i.id not in self._known_invoice_ids]
        self._known_invoice_ids.update(i.id for i in self._invoices)
        return new

    async def _async_new_invoice(self, invoice: Invoice) -> None:
        """Download a new invoice if enabled and fire the event."""
        files: list[str] = []
        if self.config_entry.options.get(CONF_AUTO_DOWNLOAD) and invoice.has_pdf:
            try:
                files = await async_save_invoice(self.hass, self.client, invoice)
            except ApiError as err:
                _LOGGER.warning(
                    "Could not download invoice %s: %s", invoice.number, err
                )
        self.hass.bus.async_fire(
            EVENT_NEW_INVOICE,
            {
                "config_entry_id": self.config_entry.entry_id,
                **invoice.as_row(),
                "files": files,
            },
        )

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "synced": True,
                "invoice_ids": sorted(self._known_invoice_ids),
                "sessions": [s.as_storage() for s in self._sessions.values()],
            }
        )

    async def _async_fetch_recent(self, full: bool) -> list[ChargingSession]:
        """Page through sessions, newest first, until we reach known ones.

        Sessions can show up late (an older session appears after a newer one),
        so we always look at a few pages and only stop at a page in which every
        session is already known. On the first sync we fetch everything.
        """
        fetched: list[ChargingSession] = []
        offset = 0
        for page_no in range(MAX_PAGES_PER_UPDATE):
            page = await self.client.async_get_sessions(offset, PAGE_SIZE)
            fetched.extend(page.sessions)
            offset += PAGE_SIZE
            if not page.sessions or offset >= page.total_count:
                break
            all_known = all(s.id in self._sessions for s in page.sessions)
            if not full and all_known and page_no + 1 >= MIN_PAGES_PER_UPDATE:
                break
        else:
            _LOGGER.warning("Stopped after %s pages of sessions", MAX_PAGES_PER_UPDATE)
        return fetched
