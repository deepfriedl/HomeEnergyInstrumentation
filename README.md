# Home Energy Instrumentation

LAN-only dashboard and structured collector for Shelly smart plugs.

## First deployment on the Linux server

1. Install Docker Engine and the Docker Compose plugin.
2. Copy this repository to the server. The included Compose file binds the app to the server's LAN address, `192.168.1.100`, rather than every interface.
3. Copy `.env.example` to `.env`; set a strong password and a long random session secret. Keep `.env` private.
4. Start it with `docker compose up -d --build`.
5. Browse to `http://<server-LAN-address>:8088`, sign in as `admin`, and add the Shelly plugs in Settings.

The first deployment creates the local account from `ENERGY_INITIAL_PASSWORD`. Changing that value later does not change the saved password.

## Data and backup

All persistent state is in `data/home-energy.sqlite3`. To make a consistent manual backup while the app is running:

```sh
docker compose exec home-energy python -c "import sqlite3; source=sqlite3.connect('/app/data/home-energy.sqlite3'); target=sqlite3.connect('/app/data/home-energy-backup-$(date +%F).sqlite3'); source.backup(target)"
```

Copy the generated file off the server if you want a second copy. The application keeps raw device responses for 30 days; normalized and rolled-up measurements use the configured retention schedule.

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
