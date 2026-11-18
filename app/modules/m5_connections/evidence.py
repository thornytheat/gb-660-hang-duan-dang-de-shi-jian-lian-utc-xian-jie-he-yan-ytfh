"""按指定基准重放一次转机判定，并留下可追溯证据。"""

from datetime import datetime

from app.baseline_state import current_baseline
from app.modules.m2_airports.registry import airport_version
from app.modules.m4_itinerary.builder import leg_evidence

from .rules import MAX_WAIT_MINUTES, connection_result, required_minutes


def _airport_facts(airport: str, local_value: str, baseline: str) -> dict:
    local_date = datetime.fromisoformat(str(local_value)).date().isoformat()
    version = airport_version(airport, local_date, baseline)
    return {
        "airport": airport,
        "local_date": local_date,
        "timezone": version["timezone"],
        "terminal": version["terminal"],
        "baseline_version": version["baseline_version"],
        "baseline_digest": version["baseline_digest"],
    }


def connection_evidence(inbound: dict, outbound: dict, airport: str, baseline: str | None = None) -> dict:
    """inbound/outbound 是航段输入行；airport 为转机机场。

    到达时刻取入港航段在转机机场的到达，离开时刻取出港航段在该机场的起飞，
    两者都用同一个显式基准版本换算，避免跨日航段与衔接等待用错基准。
    """
    version = baseline or current_baseline()
    arriving = leg_evidence(inbound, version)
    departing = leg_evidence(outbound, version)
    evidence = {"inbound": arriving["evidence"], "outbound": departing["evidence"]}
    if arriving["status"] not in {"VALID", "INVALID_DURATION"} or arriving["arrival_utc"] is None:
        return {"baseline": version, "airport": airport, "status": arriving["status"], "wait_minutes": None, "required_minutes": None, "evidence": evidence}
    if departing["status"] not in {"VALID", "INVALID_DURATION"} or departing["departure_utc"] is None:
        return {"baseline": version, "airport": airport, "status": departing["status"], "wait_minutes": None, "required_minutes": None, "evidence": evidence}
    try:
        arrival_facts = _airport_facts(airport, inbound["arrival_local"], version)
        departure_facts = _airport_facts(airport, outbound["departure_local"], version)
    except ValueError as error:
        return {"baseline": version, "airport": airport, "status": str(error), "wait_minutes": None, "required_minutes": None, "evidence": evidence}
    arrival_utc = datetime.fromisoformat(arriving["arrival_utc"])
    departure_utc = datetime.fromisoformat(departing["departure_utc"])
    wait = int((departure_utc - arrival_utc).total_seconds() // 60)
    required = required_minutes(airport, arrival_facts["terminal"], departure_facts["terminal"])
    result = connection_result(wait, required, MAX_WAIT_MINUTES)
    evidence["transfer"] = {
        "airport": airport,
        "arrival_terminal": arrival_facts["terminal"],
        "departure_terminal": departure_facts["terminal"],
        "timezone": departure_facts["timezone"],
        "baseline_version": departure_facts["baseline_version"],
        "baseline_digest": departure_facts["baseline_digest"],
        "arrival_baseline_version": arrival_facts["baseline_version"],
        "departure_baseline_version": departure_facts["baseline_version"],
    }
    return {
        "baseline": version,
        "airport": airport,
        "status": result["status"],
        "wait_minutes": wait,
        "required_minutes": required,
        "evidence": evidence,
    }
