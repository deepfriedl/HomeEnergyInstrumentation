import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.collector import collect_once
from app.bambu import BambuCollector
from app.lennox import LennoxCollector
from app.weather import WeatherCollector

logger = logging.getLogger(__name__)


async def run_collection_step(name, action):
    """Run one collector without allowing its failure to stop all telemetry."""
    try:
        await action()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Telemetry collection step failed: %s", name)


async def collector_loop(lennox_collector, weather_collector, bambu_collector):
    while True:
        await run_collection_step("Shelly", collect_once)
        await run_collection_step("Lennox S40", lennox_collector.collect_once)
        await run_collection_step("NWS weather", weather_collector.collect_once)
        await run_collection_step("Bambu printer", bambu_collector.collect_once)
        try:
            db.cleanup_and_rollup()
        except Exception:
            logger.exception("Telemetry rollup and retention maintenance failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    db.initialize()
    lennox_collector = LennoxCollector()
    weather_collector = WeatherCollector()
    bambu_collector = BambuCollector()
    task = asyncio.create_task(collector_loop(lennox_collector, weather_collector, bambu_collector))
    yield
    task.cancel()
    await lennox_collector.shutdown()
    await bambu_collector.shutdown()


app = FastAPI(title="Home Energy", lifespan=lifespan)
session_secret = os.getenv("ENERGY_SESSION_SECRET")
if not session_secret:
    raise RuntimeError("Set ENERGY_SESSION_SECRET in .env before starting the application.")
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="home_energy_session",
    max_age=None,
    https_only=False,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def signed_in(request): return request.session.get("user") == db.admin_username()
def require_login(request): return None if signed_in(request) else RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(), password: str = Form()):
    configured_username = db.admin_username()
    with db.connection() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE username=?", (configured_username,)).fetchone()
    if username == configured_username and row and db.verify_password(password, row["password_hash"]):
        request.session["user"] = configured_username
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": "Invalid username or password."}, status_code=401)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, range: str = "24h"):
    redirect = require_login(request)
    if redirect: return redirect
    if range not in db.RANGES:
        return RedirectResponse("/?range=24h", status_code=303)
    devices, series = db.overview(range)
    comparison = db.comparison_series(range)
    bambu_power_device = db.bambu_power_device()
    return templates.TemplateResponse(request, "dashboard.html", {"devices": devices, "series": series, "comparison": comparison, "hvac": db.hvac_latest(), "weather": db.weather_latest(), "bambu": db.bambu_latest(), "bambu_power_device": bambu_power_device, "range": range, "ranges": db.RANGES})


@app.get("/hvac", response_class=HTMLResponse)
def hvac_detail(request: Request, range: str = "24h"):
    redirect = require_login(request)
    if redirect: return redirect
    if range not in db.RANGES:
        return RedirectResponse("/hvac?range=24h", status_code=303)
    return templates.TemplateResponse(request, "hvac.html", {"hvac": db.hvac_latest(), "weather": db.weather_latest(), "series": db.climate_series(range), "range": range, "ranges": db.RANGES})


@app.get("/weather", response_class=HTMLResponse)
def weather_detail(request: Request, range: str = "24h"):
    redirect = require_login(request)
    if redirect: return redirect
    if range not in db.RANGES:
        return RedirectResponse("/weather?range=24h", status_code=303)
    return templates.TemplateResponse(request, "weather.html", {"weather": db.weather_latest(), "series": db.weather_series(range), "range": range, "ranges": db.RANGES})


@app.get("/printer", response_class=HTMLResponse)
def printer_detail(request: Request, range: str = "24h"):
    redirect = require_login(request)
    if redirect: return redirect
    if range not in db.RANGES:
        return RedirectResponse("/printer?range=24h", status_code=303)
    power_device = db.bambu_power_device()
    return templates.TemplateResponse(request, "printer.html", {"printer": db.bambu_latest(), "series": db.bambu_series(range), "power_device": power_device, "power_series": db.device_series(power_device["id"], range) if power_device else [], "range": range, "ranges": db.RANGES})


@app.get("/devices/{device_id}", response_class=HTMLResponse)
def device_detail(request: Request, device_id: int, range: str = "24h"):
    redirect = require_login(request)
    if redirect: return redirect
    if range not in db.RANGES:
        return RedirectResponse(f"/devices/{device_id}?range=24h", status_code=303)
    device = next((item for item in db.devices() if item["id"] == device_id), None)
    if not device: return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "device.html", {"device": device, "series": db.device_series(device_id, range), "range": range, "ranges": db.RANGES})


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    redirect = require_login(request)
    if redirect: return redirect
    return templates.TemplateResponse(request, "settings.html", {"devices": db.devices()})


@app.post("/settings/device")
def add_device(request: Request, name: str = Form(), ip_address: str = Form(), color: str = Form("#64d8cb"), enabled: bool = Form(False)):
    redirect = require_login(request)
    if redirect: return redirect
    db.save_device(name.strip(), ip_address.strip(), color, int(enabled))
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/device/{device_id}")
def update_device(request: Request, device_id: int, name: str = Form(), ip_address: str = Form(), color: str = Form("#64d8cb"), enabled: bool = Form(False)):
    redirect = require_login(request)
    if redirect: return redirect
    db.save_device(name.strip(), ip_address.strip(), color, int(enabled), device_id)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/device/{device_id}/delete")
def remove_device(request: Request, device_id: int):
    redirect = require_login(request)
    if redirect: return redirect
    db.delete_device(device_id)
    return RedirectResponse("/settings", status_code=303)


@app.get("/api/overview")
def overview_api(request: Request):
    if not signed_in(request): return JSONResponse({"detail": "Authentication required"}, 401)
    devices, series = db.overview()
    return {"devices": devices, "series": series, "generated_at": db.iso()}
