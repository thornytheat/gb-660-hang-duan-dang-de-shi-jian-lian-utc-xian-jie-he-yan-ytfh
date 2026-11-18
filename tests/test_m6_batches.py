from app.manifest import build_manifest, run_id
from app.modules.m6_batches.compare import reconcile, itinerary_account
from app.modules.m6_batches.exporter import itineraries_csv
import app.store as store

def test_leg_counts_balance():
    assert reconcile(14, 11, 2, 1)["balanced"]

def test_leg_difference_visible():
    assert reconcile(14, 10, 2, 1)["difference"] == 1

def test_itinerary_account_balances():
    assert itinerary_account(10, 8, 2)["balanced"]

def test_run_id_content_stable():
    assert run_id(b"x") == run_id(b"x")

def test_manifest_sha_full_length():
    assert len(build_manifest(b"x", 1, 0)["input_sha256"]) == 64

def test_same_input_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATABASE_PATH", tmp_path / "db.sqlite")
    manifest = build_manifest(b"x", 1, 0)
    assert store.record_run(manifest) and not store.record_run(manifest) and store.run_count() == 1

def test_export_contains_utc_evidence():
    text = itineraries_csv([{"flight_key": "A", "departure_utc": "2026-01-01T00:00:00Z", "arrival_utc": "2026-01-01T01:00:00Z", "status": "VALID", "reason_code": ""}])
    assert "departure_utc" in text and "VALID" in text


def test_export_rows_carry_baseline_and_fix_id():
    text = itineraries_csv(
        [
            {
                "flight_key": "A",
                "departure_utc": "2026-01-01T00:00:00Z",
                "arrival_utc": "2026-01-01T01:00:00Z",
                "status": "VALID",
                "reason_code": "",
                "baseline_version": "TZ-2026.11",
                "fix_id": "FIX-TZ-2026.10-TZ-2026.11",
            }
        ]
    )
    header = text.splitlines()[0]
    assert "baseline_version" in header and "fix_id" in header
    assert "TZ-2026.11" in text and "FIX-TZ-2026.10-TZ-2026.11" in text

