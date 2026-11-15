"""Data update coordinator for ANWB Laadpas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AnwbLaadpasClient, ApiError, AuthError
from .const import (
    CONF_CARD_NAMES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    EVENT_NEW_SESSION,
    MAX_PAGES_PER_UPDATE,
    MIN_PAGES_PER_UPDATE,
    PAGE_SIZE,
    STORAGE_VERSION,
)
from .models import ChargingSession

if TYPE_CHECKING:
    from . import AnwbLaadpasConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LaadpasData:
    """All known sessions plus derived figures, used by the sensors."""

    sessions: list[ChargingSession] = field(default_factory=list)

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

    async def _async_setup(self) -> None:
        """Load the sessions we already know from storage."""
        stored = await self._store.async_load() or {}
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
        first_sync = not self._sessions
        try:
            fetched = await self._async_fetch_recent(first_sync)
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ApiError as err:
            raise UpdateFailed(str(err)) from err

        new: list[ChargingSession] = []
        changed = False
        for session in fetched:
            known = self._sessions.get(session.id)
            if known is None:
                new.append(session)
            if known != session:
                # New, or updated afterwards (for example costs filled in later).
                self._sessions[session.id] = session
                changed = True

        if changed:
            await self._store.async_save(
                {"sessions": [s.as_storage() for s in self._sessions.values()]}
            )

        if first_sync:
            _LOGGER.debug("Initial sync loaded %s sessions", len(new))
        else:
            # Fire events oldest first, so automations see them in order.
            for session in sorted(new, key=lambda s: s.start):
                self.hass.bus.async_fire(
                    EVENT_NEW_SESSION,
                    {
                        "config_entry_id": self.config_entry.entry_id,
                        **session.as_event_data(self.card_name(session.card_id)),
                    },
                )

        return LaadpasData(
            sessions=sorted(
                self._sessions.values(), key=lambda s: s.start, reverse=True
            )
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
