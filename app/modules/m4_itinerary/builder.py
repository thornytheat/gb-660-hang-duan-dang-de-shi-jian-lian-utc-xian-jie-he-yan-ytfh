from datetime import datetime, timezone

from app.baseline_state import current_baseline
from app.modules.m2_airports.registry import airport_version
from app.modules.m3_localtime.resolve import resolve_local


def _offset_text(moment: datetime) -> str:
    offset = moment.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if minutes >= 0 else "-"
    absolute = abs(minutes)
    return f"{sign}{absolute // 60:02d}:{absolute % 60:02d}"


def _endpoint(local_value: str, airport: str, fold: int | None, baseline: str | None) -> dict:
    """解析一个端点，并把“哪版基准、哪个时区、偏移多少”一并留作证据。

    端点无法解析时不抛异常，而是带着原因码返回，让修正清单能如实记录
    “这条航段因为新基准变得无解”，而不是整批发布失败。
    """
    local_date = datetime.fromisoformat(local_value).date().isoformat()
    try:
        version = airport_version(airport, local_date, baseline)
    except ValueError as error:
        return {
            "airport": airport,
            "local": local_value,
            "fold": fold,
            "timezone": None,
            "terminal": None,
            "baseline_version": baseline,
            "baseline_digest": None,
            "offset": None,
            "utc": None,
            "instant": None,
            "error": str(error),
        }
    try:
        resolved = resolve_local(local_value, version["timezone"], fold)
    except ValueError as error:
        return {
            "airport": airport,
            "local": local_value,
            "fold": fold,
            "timezone": version["timezone"],
            "terminal": version["terminal"],
            "baseline_version": version["baseline_version"],
            "baseline_digest": version["baseline_digest"],
            "offset": None,
            "utc": None,
            "instant": None,
            "error": str(error),
        }
    return {
        "airport": airport,
        "local": local_value,
        "fold": resolved.fold,
        "timezone": version["timezone"],
        "terminal": version["terminal"],
        "baseline_version": version["baseline_version"],
        "baseline_digest": version["baseline_digest"],
        "offset": _offset_text(resolved),
        "utc": resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "instant": resolved.astimezone(timezone.utc),
    }


def _public(endpoint: dict) -> dict:
    return {key: value for key, value in endpoint.items() if key != "instant"}


def leg_evidence(row: dict, baseline: str | None = None) -> dict:
    version = baseline or current_baseline()
    departure = _endpoint(row["departure_local"], row["origin"], row.get("departure_fold"), version)
    arrival = _endpoint(row["arrival_local"], row["destination"], row.get("arrival_fold"), version)
    if departure.get("error") or arrival.get("error"):
        status = (departure.get("error") or arrival.get("error"))
        minutes = None
    else:
        minutes = int((arrival["instant"] - departure["instant"]).total_seconds() // 60)
        status = "INVALID_DURATION" if minutes <= 0 or minutes > 18 * 60 else "VALID"
    evidence = {
        "departure": _public(departure),
        "arrival": _public(arrival),
        "duration_minutes": minutes,
        "baseline_versions": sorted(
            {item["baseline_version"] for item in (departure, arrival) if item.get("baseline_version")}
        ),
    }
    return {
        "status": status,
        "duration_minutes": minutes,
        "departure_utc": departure.get("utc"),
        "arrival_utc": arrival.get("utc"),
        "evidence": evidence,
    }


def build_leg(row: dict, baseline: str | None = None) -> dict:
    evidence = leg_evidence(row, baseline)
    if evidence["status"] != "VALID":
        raise ValueError("INVALID_FLIGHT_DURATION" if evidence["status"] == "INVALID_DURATION" else evidence["status"])
    return {
        "flight_no": row["flight_no"],
        "origin": row["origin"],
        "destination": row["destination"],
        "departure_utc": evidence["departure_utc"],
        "arrival_utc": evidence["arrival_utc"],
        "duration_minutes": evidence["duration_minutes"],
        "baseline_versions": evidence["evidence"]["baseline_versions"],
        "evidence": evidence["evidence"],
    }


def order_legs(legs: list[dict]) -> list[dict]:
    return sorted(legs, key=lambda row: (row["departure_utc"], row["flight_no"]))
