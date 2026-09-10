# Home Energy Instrumentation

LAN-only dashboard and structured collector for Shelly smart plugs.

## First deployment on the Linux server

1. Install Docker Engine and the Docker Compose plugin.
2. Copy this repository to the server and change `192.168.1.10` in `compose.yaml` to the server's LAN address. This intentionally prevents the app from listening on every interface.
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

The importer will be added after inspecting a short representative sample of the existing refrigerator and washer logs. It will be idempotent so it is safe to rerun.
