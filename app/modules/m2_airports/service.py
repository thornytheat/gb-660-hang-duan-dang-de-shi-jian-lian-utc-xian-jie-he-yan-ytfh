from app.baseline_state import baseline_for_date

from .baselines import baseline_meta, list_baselines
from .contracts import normalize_airport
from .registry import airport_version


def run(payload: dict | None = None) -> dict:
    payload = payload or {"airport": "jfk", "at": "2026-11-16"}
    code = normalize_airport(payload["airport"])
    # 查询历史日期时按那天生效的基准回答，避免把新映射套到过去。
    baseline = payload.get("baseline") or baseline_for_date(payload["at"])
    version = airport_version(code, payload["at"], baseline)
    return {
        "airport": code,
        "timezone": version["timezone"],
        "version": version,
        "baseline": baseline_meta(baseline),
    }


def baselines() -> dict:
    return {"baselines": list_baselines()}


def demo():
    return run()
