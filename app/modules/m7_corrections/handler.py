from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.baseline_state import baseline_history, current_baseline
from app.errors import DomainError
from app.modules.m2_airports.baselines import list_baselines
from app.web import page

from .service import batches, get, history, publish

router = APIRouter()
ROWS = [
    {"key": "SFO|2026-11-25→", "status": "基准变更", "value": "Denver→Los Angeles"},
    {"key": "UA857|2026-11-24|JFK|SFO", "status": "受影响", "value": "到达 UTC +1h"},
    {"key": "MU5101|2026-11-16|PVG|JFK", "status": "不受影响", "value": "基准未触及"},
]


def _error(error: DomainError) -> JSONResponse:
    published = [batch for batch in batches()["batches"] if batch["status"] == "PUBLISHED"]
    return JSONResponse(
        status_code=error.status,
        content={
            "code": error.code,
            "message": error.message,
            "current_baseline": current_baseline(),
            "previous_published": published[0]["fix_id"] if published else None,
            "schedule_unchanged": published[0] if published else None,
        },
    )


@router.get("/baseline-fixes", response_class=HTMLResponse)
def view(request: Request):
    return page(request, "m7.html", "基准修正发布", "/api/baseline-fixes", ROWS)


@router.get("/api/baseline-fixes")
def listing():
    return {
        **batches(),
        "baseline_versions": list_baselines(),
        "baseline_history": baseline_history(),
    }


@router.get("/api/baseline-fixes/history/{kind}/{key}")
def history_detail(kind: str, key: str):
    try:
        return history(kind, key)
    except DomainError as error:
        return _error(error)


@router.get("/api/baseline-fixes/{fix_id}")
def detail(fix_id: str):
    try:
        return get(fix_id)
    except DomainError as error:
        return _error(error)


@router.post("/api/baseline-fixes")
def execute(payload: dict | None = None):
    try:
        return publish(payload or {})
    except DomainError as error:
        return _error(error)
    except ValueError as error:
        return JSONResponse(status_code=422, content={"code": getattr(error, "code", "BAD_REQUEST"), "message": str(error)})
