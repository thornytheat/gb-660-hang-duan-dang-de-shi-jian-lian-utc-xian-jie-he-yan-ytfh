from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web import page

from .baselines import baseline_diff, list_baselines
from .service import run

router = APIRouter()
ROWS = [
    {"key": "PVG", "status": "Asia/Shanghai", "value": "+08:00"},
    {"key": "JFK", "status": "America/New_York", "value": "DST"},
    {"key": "LHR", "status": "Europe/London", "value": "DST"},
]


@router.get("/airports", response_class=HTMLResponse)
def view(request: Request):
    return page(request, "m2.html", "机场时区", "/api/airports", ROWS)


@router.post("/api/airports")
def execute(payload: dict | None = None):
    return run(payload or {})


@router.get("/api/airports/baselines")
def baselines():
    return {"baselines": list_baselines()}


@router.get("/api/airports/baselines/diff")
def diff(from_version: str, to_version: str):
    return {"from": from_version, "to": to_version, "changes": baseline_diff(from_version, to_version)}
