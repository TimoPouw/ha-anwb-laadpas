<img src="brand/icon.png" alt="ANWB Laadpas" width="96" align="right">

# ANWB Laadpas for Home Assistant

Unofficial Home Assistant integration that reads the charging sessions of your
[ANWB Laadpas](https://laadpas.anwb.nl) (the backend is run by Deftpower) and
exposes them as sensors and events.

> **Disclaimer:** this integration is not made, endorsed or supported by ANWB
> or Deftpower. It uses the same private API as the ANWB Laadpas website, which
> can change at any time without notice. Use at your own risk.

## Features

- Sensors for the **last session**: energy, cost, start, end and duration, with
  location, charge point (EVSE) and card as attributes.
- Sensors for **this month**: number of sessions, energy and cost.
- **Total energy** as a `total_increasing` kWh sensor, usable in the Energy
  dashboard, plus total cost.
- A **device per charging card** with its own sensors, and a name you choose.
- An **event** `anwb_laadpas_new_session` for every new session, for
  automations.
- **All sessions** in a dashboard (via a sensor attribute) and through the
  `anwb_laadpas.get_sessions` action.
- **Session details**: price excluding VAT, operator, connector, GPS location,
  vehicle and stop reason, filled in for your whole history within minutes.
- **Invoices**: last invoice, open amount and the full list, an
  `anwb_laadpas_new_invoice` event, and invoice and specification PDFs saved to
  your media folder (on request or automatically).
- Browser login with MFA, automatic token refresh, reauthentication when the
  login expires, and diagnostics with personal data removed.

## Installation

### Via HACS (recommended)

1. In HACS, open the menu (three dots, top right) and choose **Custom
   repositories**.
2. Add `https://github.com/TimoPouw/ha-anwb-laadpas` with type
   **Integration**.
3. Install **ANWB Laadpas** and restart Home Assistant.

Home Assistant can also add a repository straight from its GitHub link; use the
same URL there.

### Manually

Copy `custom_components/anwb_laadpas` into the `custom_components` folder of
your Home Assistant configuration and restart Home Assistant.

### Adding the integration

Go to **Settings > Devices & services > Add integration** and search for
**ANWB Laadpas**.

## Logging in

You can choose between two methods. Both happen in Home Assistant; nothing is
stored outside your Home Assistant configuration.

### Log in via the browser (recommended)

1. Home Assistant shows a login link. Open it and log in with your ANWB
   Laadpas account, including the verification code (MFA).
2. You end up on a `laadpas.anwb.nl/account/login?code=...` page. Copy the full
   address from the address bar straight away: the code is valid for a few
   minutes and can be used once.
3. Paste it into Home Assistant.

If the page moves on before you can copy the address, open the developer tools
(F12), go to the **Network** tab, enable **Preserve log**, log in again and copy
the URL of the request to `/account/login?code=...`.

### Paste a refresh token

1. Log in on [laadpas.anwb.nl](https://laadpas.anwb.nl) with the developer
   tools (F12) open.
2. In the **Network** tab, find the request to `connect/token` and copy the
   value of `refresh_token` from its response.
3. Paste it into Home Assistant.

Refresh tokens can be used once: after Home Assistant uses yours, the website
may ask you to log in again.

## Options

Under **Settings > Devices & services > ANWB Laadpas > Configure** you can set:

- **Update interval**: how often to check for new sessions (default 30
  minutes).
- **Download new invoices automatically**: saves the invoice and specification
  of every new invoice as PDF (see [Invoices](#invoices)). Off by default.
- **A name per card**: each field is labelled with the card number. The name is
  used for the card's device and in the `card_name` field of events. You can
  also rename devices in Home Assistant itself.

## Sensors

Account device (`ANWB Laadpas`):

| Sensor | Unit | Notes |
| --- | --- | --- |
| Last session energy | kWh | Attributes: session id, start, end, duration, location, address, city, EVSE id, operator, card |
| Last session cost | EUR | Including VAT |
| Last session start / end | timestamp | |
| Last session duration | min | |
| Sessions this month | | Resets at the start of the month (local time) |
| Energy this month | kWh | |
| Cost this month | EUR | |
| Total energy | kWh | `total_increasing`, for the Energy dashboard |
| Total cost | EUR | |
| Sessions | | Number of sessions; attribute `sessions` holds the full list (see below) |
| Last invoice | EUR | Including VAT. Attributes: number, date, due and paid date, amount excluding VAT, VAT rate, status, period, number of sessions |
| Open amount | EUR | Total of invoices that are not paid yet |
| Invoices | | Number of invoices; attribute `invoices` holds the full list |

Each card device has: last session energy (with attributes), cost and end,
sessions, energy and cost this month, and total energy.

Sessions where the cost is not known yet count as 0 until the cost is filled in.

### Session details

Some fields come from a separate call per session: `cost_excl_vat`,
`operator_name`, `connector_standard`, `connector_format`, `latitude`,
`longitude`, `vehicle` and `stop_reason`. They are fetched straight away for
new sessions. After setup, a background task fills them in for your history,
newest first, with a short pause between calls (about 2 seconds per session,
so roughly 8 to 10 minutes for 250 sessions). It does not delay the setup or
the regular updates, and its progress is saved, so a restart continues where
it left off. Until then these fields are empty for older sessions.

## Viewing all sessions

### In a dashboard

The **Sessions** sensor has an attribute `sessions` with every session, newest
first. Each row has `session_id`, `start`, `end` (local time), `duration_minutes`,
`energy_kwh`, `cost`, `card` (your card name, or the card number),
`location_name` and `city`. The list is not stored in the recorder database.

A table of the latest 50 sessions with a Markdown card. Check the entity id of
the Sessions sensor in its settings; with Home Assistant in Dutch it may be
`sensor.anwb_laadpas_sessies`.

```yaml
type: markdown
title: Charging sessions
content: |
  | Date | Location | kWh | € | Card |
  |:--|:--|--:|--:|:--|
  {% for s in (state_attr('sensor.anwb_laadpas_sessions', 'sessions') or [])[:50] -%}
  | {{ as_timestamp(s.start) | timestamp_custom('%d-%m-%Y %H:%M') }} | {{ s.location_name }}, {{ s.city }} | {{ '%.2f' | format(s.energy_kwh) }} | {{ '%.2f' | format(s.cost) if s.cost is not none else '' }} | {{ s.card }} |
  {% endfor %}
```

### With the `anwb_laadpas.get_sessions` action

Returns sessions with totals, for scripts, automations or **Developer tools >
Actions**. All fields are optional:

```yaml
action: anwb_laadpas.get_sessions
data:
  start: "2026-11-01"   # sessions started on or after this date
  end: "2026-11-30"     # sessions started on or before this date
  card: Tesla           # card name, card number or card id
  limit: 20
```

Response:

```yaml
count: 3
energy_kwh: 22.96
cost: 18.4
sessions:
  - session_id: NLAAA-900000000
    start: "2026-11-08T13:15:16+01:00"
    end: "2026-11-08T13:41:16+01:00"
    duration_minutes: 26
    energy_kwh: 14.71
    cost: 12.11
    card: Tesla
    location_name: Example location
    city: Exampletown
```

## Invoices

The invoice sensors and actions only contain invoice data: your name, address
and e-mail address on the invoice are never read into Home Assistant.

### Getting invoices

```yaml
action: anwb_laadpas.get_invoices
data:
  start: "2026-01-01"   # invoice date on or after
  end: "2026-12-31"     # invoice date on or before
  unpaid_only: false
  limit: 12
```

Returns `count`, `amount_incl_vat`, `open_amount` and `invoices`, where each
invoice has `invoice_id`, `number`, `date`, `due_date`, `paid_date`,
`amount_excl_vat`, `amount_incl_vat`, `vat_rate`, `currency`, `status`, `paid`,
`type`, `period` and `session_count`.

### Downloading PDFs

```yaml
action: anwb_laadpas.download_invoices
data:
  invoice: "1700000001"   # optional; leave out for all invoices or use start/end
  kind: both              # invoice, specification or both
  overwrite: false
```

Files are saved in the `anwb_laadpas` folder of your local media folder
(usually `/media/anwb_laadpas`), named like
`2026-10-31_1700000001_invoice.pdf` and
`2026-10-31_1700000001_specification.pdf`. Open them via **Media > My media >
anwb_laadpas**. Existing files are skipped unless `overwrite` is set. The
action returns the paths of the files.

With the option **Download new invoices automatically**, every new invoice and
its specification are saved as soon as they appear.

### Event: `anwb_laadpas_new_invoice`

Fired once for every new invoice, with the same fields as `get_invoices` plus
`files` (the saved PDFs, empty when automatic download is off). Not fired for
invoices that already existed when you set up the integration.

```yaml
automation:
  - alias: "New charging invoice"
    triggers:
      - trigger: event
        event_type: anwb_laadpas_new_invoice
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "New ANWB Laadpas invoice"
          message: >-
            € {{ trigger.event.data.amount_incl_vat }} for
            {{ trigger.event.data.session_count }} sessions
            ({{ trigger.event.data.period }})
```

### Invoices in a dashboard

```yaml
type: markdown
title: Invoices
content: |
  | Date | Period | Sessions | € | Status |
  |:--|:--|--:|--:|:--|
  {% for i in state_attr('sensor.anwb_laadpas_invoices', 'invoices') or [] -%}
  | {{ as_timestamp(i.date) | timestamp_custom('%d-%m-%Y') }} | {{ i.period }} | {{ i.session_count }} | {{ '%.2f' | format(i.amount_incl_vat) }} | {{ 'paid' if i.paid else i.status }} |
  {% endfor %}
```

## Event: `anwb_laadpas_new_session`

Fired once for every new completed session, oldest first. Not fired for the
history that is loaded when you first set up the integration.

| Field | Example |
| --- | --- |
| `config_entry_id` | `01J...` |
| `session_id` | `NLAAA-900000000` |
| `start`, `end` | `2026-11-08T12:15:16+00:00` |
| `duration_minutes` | `26.0` |
| `energy_kwh` | `14.71` |
| `cost`, `currency` | `12.11`, `EUR` |
| `card_id`, `card_name` | `100002`, `Tesla` |
| `location_name`, `address`, `city`, `country` | |
| `evse_id`, `operator` | |
| `cost_excl_vat`, `operator_name`, `connector_standard`, `connector_format` | |
| `latitude`, `longitude`, `vehicle`, `stop_reason` | |

Example automation:

```yaml
automation:
  - alias: "Notify after a charging session"
    triggers:
      - trigger: event
        event_type: anwb_laadpas_new_session
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "Charging session finished"
          message: >-
            {{ trigger.event.data.energy_kwh }} kWh at
            {{ trigger.event.data.location_name }} for
            € {{ trigger.event.data.cost }}
            ({{ trigger.event.data.card_name or 'unnamed card' }})
```

## Energy dashboard

Add **Total energy** (account or per card) under **Settings > Dashboards >
Energy**, for example as an individual device or as an electric vehicle
consumption source. The first value after setup is your full history; the
Energy dashboard only counts increases from then on.

## How it works

- The only fixed address is `https://laadpas.anwb.nl`. The API base URL, OAuth
  client id, endpoints, scope and tenant are read from the website at setup and
  on every start, and kept in the config entry as a fallback.
- Login uses the authorization code flow with PKCE (S256), as the website does.
  Access tokens last 8 hours. Refresh tokens rotate, so Home Assistant stores
  every new one.
- On the first run all completed sessions are loaded. After that only the most
  recent pages are fetched, until a page contains only known sessions. Known
  sessions are kept in Home Assistant's storage, so they survive a restart.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

`tools/probe.py` is a standalone script that was used to explore the API. It
writes real data to `tools/sample_response.json` and tokens to `.env`; both are
in `.gitignore`. `tools/make_fixture.py` generates the fictional test fixture.

## License

MIT, see [LICENSE](LICENSE).
