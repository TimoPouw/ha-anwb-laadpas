#!/usr/bin/env python3
"""Generate tests/fixtures/sessions.json with fictional data.

The structure mirrors a real GetAll response, but every value (ids, card
numbers, locations, amounts, dates) is made up. Edge cases seen in real data
are included: missing end time, missing costs, fractional seconds, two cards
and several charge point operators.
"""

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sessions.json"

CARDS = [
    {"id": 100001, "emaId": "NL-XXX-C00000001-A"},
    {"id": 100002, "emaId": "NL-XXX-C00000002-B"},
]
LOCATIONS = [
    ("Teststraat 1 Laadplein", "Teststraat 1", "Voorbeelddorp", "1234AB", "NL", "AAA"),
    ("Parkeergarage Centrum", "Marktplein 10", "Fictiestad", "2345BC", "NL", "BBB"),
    ("Snellader Snelweg A99", "Rijksweg 99", "Nergenshuizen", "3456CD", "NL", "CCC"),
    ("Raststätte Musterhausen", "Musterweg 5", "Musterhausen", "12345", "DE", "DDD"),
]
PROFILE = {
    "profileId": "00000000-0000-4000-8000-000000000001",
    "name": "Test User",
    "organizationUnitName": None,
    "organizationUnitId": None,
    "isShared": False,
}

items = []
start = datetime(2026, 11, 8, 12, 15, 16, tzinfo=UTC)
for i in range(30):
    loc = LOCATIONS[i % len(LOCATIONS)]
    card = CARDS[0] if i % 5 == 0 else CARDS[1]
    duration = timedelta(minutes=random.randint(20, 240))
    kwh = round(random.uniform(3, 45), 2)
    frac = i % 7 == 3
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if frac else "%Y-%m-%dT%H:%M:%S"
    stamp = lambda d: d.strftime(fmt)[:23 if frac else 19] + "Z"  # noqa: E731
    session_no = 900000000 - i
    items.append(
        {
            "chargingSession": {
                "id": f"NL{loc[5]}-{session_no}",
                "sessionId": str(session_no),
                "startDateTime": stamp(start),
                "endDateTime": None if i == 12 else stamp(start + duration),
                "sessionStatus": "COMPLETED",
                "kwh": kwh,
                "currency": "EUR",
                "totalCosts": {
                    "excl_vat": None,
                    "incl_vat": None if i == 7 else round(kwh * 0.59, 2),
                },
                "token": dict(card),
                "location": {
                    "id": f"00000000-0000-4000-8000-{i:012d}",
                    "locationId": f"{i:024x}",
                    "name": loc[0],
                    "address": loc[1],
                    "city": loc[2],
                    "postalCode": loc[3],
                    "evseId": f"{loc[4]}*{loc[5]}*E{1000 + i}*1",
                    "countryCode": loc[4],
                    "partyId": loc[5],
                },
                "isGenerated": False,
                "isSmartCharging": False,
                "isVehicleApiEnabled": True,
            },
            "profile": dict(PROFILE),
        }
    )
    start -= timedelta(days=random.randint(1, 6), hours=random.randint(0, 12))

OUT.write_text(json.dumps(items, indent=2) + "\n")
print(f"wrote {len(items)} sessions to {OUT}")
