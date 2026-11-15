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

Each card device has: last session energy (with attributes), cost and end,
sessions, energy and cost this month, and total energy.

Sessions where the cost is not known yet count as 0 until the cost is filled in.

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
