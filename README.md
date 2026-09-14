# Home Energy Instrumentation

LAN-only dashboard and structured collector for Shelly smart plugs.

## First deployment on the Linux server

1. Install Docker Engine and the Docker Compose plugin.
2. Copy this repository to the server. Copy `.env.example` to `.env`; set a private login name, the server's LAN address, a strong password, and a long random session secret. Keep `.env` private.
3. Copy `config/lan.example.json` to `config/lan.json` and add device names and addresses. This private LAN configuration is ignored by Git.
4. Start it with `docker compose up -d --build`.
5. Browse to `http://<server-LAN-address>:8088` and sign in with the private login name from `.env`.

The first deployment creates the local account from `ENERGY_INITIAL_PASSWORD`. Changing that value later does not change the saved password.

## Data and backup

All persistent state is in `data/home-energy.sqlite3`. To make a consistent manual backup while the app is running:

```sh
docker compose exec home-energy python -c "import sqlite3; source=sqlite3.connect('/app/data/home-energy.sqlite3'); target=sqlite3.connect('/app/data/home-energy-backup-$(date +%F).sqlite3'); source.backup(target)"
```

Copy the generated file off the server if you want a second copy. Minute samples are retained for seven days, then consolidated into five-minute data for 30 days, hourly data for six months, and daily data for 18 months. Raw device responses are retained with the minute samples.

## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
ENERGY_ADMIN_USERNAME=admin ENERGY_INITIAL_PASSWORD=dev-password ENERGY_SESSION_SECRET=dev-secret uvicorn app.main:app --reload
```

## Existing-log import

The current collector's JSON-lines format can be imported without losing the original Shelly status object. Copy the log into the repository directory on the server, then run:

```sh
docker compose exec home-energy python -m app.import_logs /app/data/collector.log
```

The device IPs must first exist in Settings. The importer is idempotent: re-running it does not duplicate device/timestamp readings.

## Normalize one Shelly Plug US Gen4

`scripts/normalize_shelly.py` is an interactive, one-plug-at-a-time local
configuration tool. It prompts for the plug's LAN address and friendly name,
then shows the current configuration before requiring `APPLY` to proceed.
It sets the device and switch names, sets power-on recovery to `on`, and
disables the unused Shelly access point. It does not switch the relay, change
station Wi-Fi, update firmware, or alter cloud/radio settings.

It writes a pre-change JSON backup to `shelly-backups/`, which is intentionally
ignored by Git. Run it from the repository directory on a LAN-connected machine:

```sh
python3 scripts/normalize_shelly.py
```

The normalizer prompts for one shared Shelly password (with hidden input),
enables local Digest authentication as its final step, and never saves that
password. Before normalizing plugs, set the same private value in `.env` as
`ENERGY_SHELLY_PASSWORD`, then rebuild the app. The collector uses it only for
local Shelly polling.

## Lennox S40 discovery

The optional discovery command makes a read-only local connection to a Lennox
S40. It records the available operating fields, zones, equipment models, and
diagnostics in `data/lennox-discovery.json`. It intentionally excludes the
thermostat serial number, Wi-Fi details, and network address.

Set `ENERGY_LENNOX_HOST` in the private `.env` file, rebuild the container,
then run:

```sh
docker compose up -d --build
docker compose exec home-energy python -m app.lennox_discovery
```

The command changes no thermostat setting. Review `data/lennox-discovery.json`
locally before sharing any part of it; it is ignored by Git along with the rest
of `data/`.

## Optional NWS weather observation

Set `ENERGY_NWS_STATION` in the private `.env` file to an NWS station identifier,
then rebuild with `docker compose up -d --build`. The app reads that station's
latest observation every ten minutes and uses the same retention tiers as the
other telemetry. It is read-only and does not expose the station setting in the
web interface. Use an independent station to compare ambient conditions with
the thermostat's reported outdoor value.

## Bambu printer discovery

The optional Bambu discovery command makes a read-only subscription to the
printer's local MQTT status topic. It sends no printer command and does not
access files or the camera. Set `ENERGY_BAMBU_HOST`, `ENERGY_BAMBU_SERIAL`, and
`ENERGY_BAMBU_ACCESS_CODE` in the private `.env` file, rebuild, then run:

```sh
docker compose up -d --build
docker compose exec home-energy python -m app.bambu_discovery
```

It saves a scrubbed status sample in `data/bambu-discovery.json` and pins the
printer's local TLS certificate in `data/bambu-printer.pem`. Both are ignored
by Git. The scrubbed sample omits network details, the serial number, access
code, and print/project names.

With those same private settings present, the app maintains a read-only local
status subscription and stores printer state, progress, temperatures, fan
activity, and AMS conditions once per minute. Electrical power remains the
responsibility of the separate Shelly plug assigned to the printer. Set
`ENERGY_BAMBU_POWER_DEVICE_NAME` to that Shelly device's private Settings name
to link its power and voltage history to the printer page.
