# Home Energy Instrumentation

LAN-only dashboard and structured collector for Shelly smart plugs.

## First deployment on the Linux server

1. Install Docker Engine and the Docker Compose plugin.
2. Copy this repository to the server. Copy `.env.example` to `.env`; set the server's LAN address, a strong password, and a long random session secret. Keep `.env` private.
3. Copy `config/lan.example.json` to `config/lan.json` and add device names and addresses. This private LAN configuration is ignored by Git.
4. Start it with `docker compose up -d --build`.
5. Browse to `http://<server-LAN-address>:8088` and sign in as `admin`.

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
ENERGY_INITIAL_PASSWORD=dev-password ENERGY_SESSION_SECRET=dev-secret uvicorn app.main:app --reload
```

## Existing-log import

The current collector's JSON-lines format can be imported without losing the original Shelly status object. Copy the log into the repository directory on the server, then run:

```sh
docker compose exec home-energy python -m app.import_logs /app/data/collector.log
```

The device IPs must first exist in Settings. The importer is idempotent: re-running it does not duplicate device/timestamp readings.

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
