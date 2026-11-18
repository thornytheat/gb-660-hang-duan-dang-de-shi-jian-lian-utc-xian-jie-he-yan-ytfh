from datetime import date

from .baselines import AIRPORTS, DEFAULT_BASELINE, baseline_digest, baseline_meta, baseline_table


def _select(intervals: list[tuple], target: date) -> tuple | None:
    matches = [
        item
        for item in intervals
        if date.fromisoformat(item[0]) <= target and (item[1] is None or target < date.fromisoformat(item[1]))
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError("TIMEZONE_VERSION_OVERLAP")
    return matches[0]


def airport_version(code: str, at: str, baseline: str | None = None) -> dict:
    """按机场、日期与显式基准版本解析时区与航站楼。

    baseline 省略时使用默认版（仅供新算的样例/默认调用）；复核既有结论时
    必须传入该结论固定的版本，否则新基准会悄悄改写旧结论。
    """
    target = date.fromisoformat(at)
    version = baseline or DEFAULT_BASELINE
    table = AIRPORTS if baseline is None else baseline_table(version)
    selected = _select(table.get(code, []), target)
    if selected is None:
        raise ValueError("UNKNOWN_AIRPORT")
    start, end, zone, terminal = selected
    return {
        "start": start,
        "end": end,
        "timezone": zone,
        "terminal": terminal,
        "baseline_version": version,
        "baseline_digest": baseline_digest(version),
    }


def airport_zone(code: str, at: str = "2026-11-16", baseline: str | None = None) -> str:
    return airport_version(code, at, baseline)["timezone"]


def airport_baseline_meta(baseline: str | None = None) -> dict:
    return baseline_meta(baseline or DEFAULT_BASELINE)
