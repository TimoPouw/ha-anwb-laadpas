#!/usr/bin/env python3
"""Generate tests/fixtures/sessions.json with fictional data.

The structure mirrors a real GetAll response, but every value (ids, card
numbers, locations, amounts, dates) is made up. Edge cases seen in real data
are included: missing end time, missing costs, fractional seconds, two cards
and several charge point operators.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import random

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


def stamp(moment: datetime, fractional: bool) -> str:
    """Format like the API: whole seconds, sometimes with milliseconds."""
    if fractional:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


items = []
start = datetime(2026, 11, 8, 12, 15, 16, tzinfo=UTC)
for i in range(30):
    loc = LOCATIONS[i % len(LOCATIONS)]
    card = CARDS[0] if i % 5 == 0 else CARDS[1]
    duration = timedelta(minutes=random.randint(20, 240))
    kwh = round(random.uniform(3, 45), 2)
    frac = i % 7 == 3
    session_no = 900000000 - i
    items.append(
        {
            "chargingSession": {
                "id": f"NL{loc[5]}-{session_no}",
                "sessionId": str(session_no),
                "startDateTime": stamp(start, frac),
                "endDateTime": None if i == 12 else stamp(start + duration, frac),
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


# --------------------------------------------------------------------------- #
# Session details and invoices (separate random stream: sessions.json stays
# identical when this part changes).
# --------------------------------------------------------------------------- #
rnd = random.Random(7)
DETAILS_OUT = OUT.parent / "session_details.json"
INVOICES_OUT = OUT.parent / "invoices.json"

details = {}
for i, item in enumerate(items):
    cs = item["chargingSession"]
    incl = cs["totalCosts"]["incl_vat"]
    details[cs["id"]] = {
        "chargingSession": {
            **cs,
            "totalCosts": {
                "excl_vat": round(incl / 1.21, 2) if incl is not None else None,
                "incl_vat": incl,
            },
            "stopReason": None,
            "completionType": "AUTOMATED",
            "location": {
                **cs["location"],
                "coordinates": {
                    "latitude": round(52.0 + rnd.random(), 6),
                    "longitude": round(5.0 + rnd.random(), 6),
                },
                "connectorStandard": "IEC_62196_T2_COMBO" if i % 3 else "IEC_62196_T2",
                "connectorFormat": "CABLE" if i % 3 else "SOCKET",
                "operatorName": None
                if i == 4
                else f"Example Operator {cs['location']['partyId']}",
            },
            "vehicle": None
            if i == 4
            else {
                "profileVehicleId": "00000000-0000-4000-8000-0000000000f1",
                "manufacturerName": "Example",
                "manufacturerModelName": "EV1",
                "manufacturerModelVersion": None,
            },
        },
        "relatedMspCdr": None,
        "relatedCpoCdr": None,
        "profile": None,
    }
DETAILS_OUT.write_text(json.dumps(details, indent=2) + "\n")

FAKE_PARTNER = {
    "name": "Test User",
    "contactEmailInvoice": "test.user@example.invalid",
    "billingAddress": {
        "street": "Teststraat",
        "houseNumber": 1,
        "postalCode": "1234AB",
        "city": "Voorbeelddorp",
    },
}
invoices = []
for n, month in enumerate(range(10, 0, -1)):
    excl = round(rnd.uniform(5, 250), 2)
    incl = round(excl * 1.21, 2)
    unpaid = n == 0
    invoices.append(
        {
            "invoice": {
                "invoiceNumber": str(1700000010 - n),
                "invoiceReference": f"NL-EUR-2026{month:02d}01000000-TEST",
                "totalLocalCostExclVat": excl,
                "totalLocalCostInclVat": incl,
                "vat": 21.0,
                "currency": "EUR",
                "status": "SENT" if unpaid else "PAID",
                "paymentStatus": "OPEN" if unpaid else "PAID",
                "invoiceStatus": "OPEN" if unpaid else "CLOSED",
                "businessPartner": FAKE_PARTNER,
                "invoiceLines": [
                    {
                        "amount": excl,
                        "description": f"Laadsessies - (Periode 01-{month:02d}-2026 - 28-{month:02d}-2026)",
                        "mspCdrIds": [
                            f"00000000-0000-4000-8000-{n:06d}{k:06d}"
                            for k in range(n % 4 + 1)
                        ],
                        "referenceType": "MSP_CDR",
                    }
                ],
                "dateCreated": f"2026-{month:02d}-02T08:00:00.1234567Z",
                "datePaid": None if unpaid else f"2026-{month:02d}-20T21:00:00Z",
                "dateDue": f"2026-{month:02d}-28T00:00:00Z",
                # Older invoices sometimes have no invoiceDate or invoiceType.
                "invoiceDate": None if n == 9 else f"2026-{month:02d}-28T00:00:00Z",
                "invoiceType": None if n >= 7 else "DEBIT",
                "hasInvoicePdf": True,
                "id": f"00000000-0000-4000-8000-1{n:011d}",
            },
            "pdfInvoice": {"businessPartner": FAKE_PARTNER},
        }
    )
INVOICES_OUT.write_text(json.dumps(invoices, indent=2, ensure_ascii=False) + "\n")
print(f"wrote {len(details)} details and {len(invoices)} invoices")
