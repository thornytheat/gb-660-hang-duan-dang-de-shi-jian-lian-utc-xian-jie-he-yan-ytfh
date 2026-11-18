from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.web import page

from .exporter import itineraries_csv, published_rows
from .service import demo

router = APIRouter()
ROWS = [
    {"key": "RUN-660-A", "status": "守恒", "value": "14"},
    {"key": "RUN-660-B", "status": "复用", "value": "14"},
    {"key": "RUN-660-C", "status": "隔离", "value": "1"},
]


@router.get("/batches", response_class=HTMLResponse)
def view(request: Request):
    return page(request, "m6.html", "批次导出", "/api/reconcile", ROWS)


@router.post("/api/reconcile")
def execute():
    return demo()


@router.get("/api/export/itineraries.csv")
def export_itineraries():
    body = itineraries_csv(published_rows())
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"content-disposition": "attachment; filename=itineraries.csv"},
    )
