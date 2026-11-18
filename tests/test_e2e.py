from fastapi.testclient import TestClient

from app import config
from app.main import app


def test_all_module_pages_accessible():
    client = TestClient(app)
    for path in (
        "/legs",
        "/airports",
        "/local-time",
        "/itineraries",
        "/connections",
        "/batches",
        "/baseline-fixes",
    ):
        response = client.get(path)
        assert response.status_code == 200 and "panel" in response.text


def test_full_sample_flow_has_utc_chain():
    client = TestClient(app)
    results = [
        client.post(path).json()
        for path in (
            "/api/legs/import",
            "/api/airports",
            "/api/local-time/resolve",
            "/api/itineraries/build",
            "/api/connections/check",
            "/api/reconcile",
        )
    ]
    assert (
        results[0]["accepted"] == 1
        and results[2]["utc"].endswith("Z")
        and results[3]["duration_minutes"] == 870
        and results[4]["status"] == "VALID"
    )


def test_itinerary_export_downloads():
    response = TestClient(app).get("/api/export/itineraries.csv")
    assert response.status_code == 200 and "MU5101" in response.text


def test_baseline_fix_flow_over_http(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "e2e.sqlite3")
    client = TestClient(app)
    leg = {
        "flight_no": "UA857",
        "flight_date": "2026-11-24",
        "origin": "JFK",
        "destination": "SFO",
        "departure_local": "2026-11-24T20:00",
        "arrival_local": "2026-11-25T00:30",
    }
    first = client.post("/api/baseline-fixes", json={"legs": [leg]})
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "PUBLISHED" and body["scope"]["affected_legs"] == 1

    repeat = client.post("/api/baseline-fixes", json={"legs": [leg]})
    assert repeat.json()["reused"] is True and repeat.json()["fix_id"] == body["fix_id"]
    assert len(client.get("/api/baseline-fixes").json()["batches"]) == 1

    fix = client.get(f"/api/baseline-fixes/{body['fix_id']}").json()
    assert fix["affected"][0]["before"]["baseline"] == "TZ-2026.10"
    assert fix["affected"][0]["after"]["baseline"] == "TZ-2026.11"

    history = client.get(f"/api/baseline-fixes/history/leg/{body['affected'][0]['leg_key']}").json()
    assert history["history"][0]["evidence_before"]["arrival"]["utc"] == "2026-11-25T07:30:00Z"

    export = client.get("/api/export/itineraries.csv").text
    assert body["fix_id"] in export and "TZ-2026.11" in export
