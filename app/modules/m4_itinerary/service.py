from app.baseline_state import current_baseline

from .builder import build_leg, order_legs
from .validate import chain_span_minutes, validate_chain


def run(payload: dict | None = None) -> dict:
    payload = payload or {
        "flight_no": "MU5101",
        "origin": "PVG",
        "destination": "JFK",
        "departure_local": "2026-11-16T09:00",
        "arrival_local": "2026-11-16T10:30",
    }
    baseline = payload.get("baseline") or current_baseline()
    if payload.get("legs"):
        # 多条航段时按 UTC 稳定排序，并把每条航段的基准版本一并返回。
        legs = [build_leg(row, baseline) for row in payload["legs"]]
        ordered = order_legs(legs)
        return {
            "baseline_version": baseline,
            "legs": ordered,
            "chain_valid": validate_chain(ordered),
            "chain_span_minutes": chain_span_minutes(ordered),
        }
    leg = build_leg(payload, baseline)
    return {**leg, "baseline_version": baseline, "chain_valid": validate_chain([leg])}


def demo():
    return run()
