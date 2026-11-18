from datetime import datetime

from app.baseline_state import current_baseline

from .evidence import connection_evidence
from .rules import MAX_WAIT_MINUTES, connection_result, required_minutes
from .windows import waiting_minutes


def run(payload: dict | None = None) -> dict:
    if payload and payload.get("inbound") and payload.get("outbound") and payload.get("airport"):
        # 有完整航段输入时按显式基准重放，结论自带可追溯证据。
        result = connection_evidence(
            payload["inbound"], payload["outbound"], payload["airport"], payload.get("baseline")
        )
        return result
    payload = payload or {
        "arrival_utc": "2026-11-16T10:00:00+00:00",
        "departure_utc": "2026-11-16T12:30:00+00:00",
        "airport": "JFK",
        "arrival_terminal": "T4",
        "departure_terminal": "T1",
    }
    baseline = payload.get("baseline") or current_baseline()
    wait = waiting_minutes(
        datetime.fromisoformat(payload["arrival_utc"]), datetime.fromisoformat(payload["departure_utc"])
    )
    required = required_minutes(payload["airport"], payload["arrival_terminal"], payload["departure_terminal"])
    return {
        **connection_result(wait, required, MAX_WAIT_MINUTES),
        "baseline_version": baseline,
        "airport": payload["airport"],
    }


def demo():
    return run()
