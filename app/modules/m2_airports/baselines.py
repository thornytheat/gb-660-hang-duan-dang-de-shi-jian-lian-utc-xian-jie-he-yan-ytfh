"""机场基准的不可变版本。

每版基准都是一份完整的机场表快照。已经发出的结论只引用发放时固定的
那一版；修正发布通过显式对照两版差异完成，不会回头改写旧结论。

- TZ-2026.10 是现行已发放基准。
- TZ-2026.11 是下一次修正：自 2026-11-25 起 SFO 的时区从 America/Denver
  更正为 America/Los_Angeles。修正按区间生效，历史区间保持原映射，
  因此已经发出的历史排班不会因为换版而被改写；只有跨越 2026-11-25
  的跨日航段和衔接等待会落在两个不同区间上，需要显式重新计算。
"""

import json
import os
from hashlib import sha256

DEFAULT_BASELINE = os.getenv("TIMEZONE_BASELINE", "TZ-2026.10")
CORRECTION_BASELINE = "TZ-2026.11"

# 版本号、说明与生效日属于发布元数据；表内容才是计算依据。
BASELINE_META = {
    "TZ-2026.10": {
        "label": "2026.10 基准",
        "effective_from": "2026-10-01",
        "parent": None,
        "note": "现行已发放基准。SFO 使用 America/Denver。",
    },
    "TZ-2026.11": {
        "label": "2026.11 修正基准",
        "effective_from": "2026-11-25",
        "parent": "TZ-2026.10",
        "note": "自 2026-11-25 起 SFO 更正为 America/Los_Angeles；此前区间保持原映射。",
    },
}

# (start, end, IANA timezone, terminal)；end 为半开区间右端，None 表示无限。
BASELINE_TABLES = {
    "TZ-2026.10": {
        "PVG": [("2000-01-01", None, "Asia/Shanghai", "T1")],
        "JFK": [("2000-01-01", None, "America/New_York", "T4")],
        "LHR": [("2000-01-01", None, "Europe/London", "T5")],
        "SFO": [("2000-01-01", None, "America/Denver", "I")],
    },
    "TZ-2026.11": {
        "PVG": [("2000-01-01", None, "Asia/Shanghai", "T1")],
        "JFK": [("2000-01-01", None, "America/New_York", "T4")],
        "LHR": [("2000-01-01", None, "Europe/London", "T5")],
        "SFO": [
            ("2000-01-01", "2026-11-25", "America/Denver", "I"),
            ("2026-11-25", None, "America/Los_Angeles", "I"),
        ],
    },
}

# 兼容旧调用与既有测试对 AIRPORTS 的 monkeypatch：始终指向默认版的表对象。
AIRPORTS = BASELINE_TABLES[DEFAULT_BASELINE]


def baseline_table(version: str) -> dict:
    try:
        return BASELINE_TABLES[version]
    except KeyError:
        raise ValueError("UNKNOWN_BASELINE")


def _canonical(table: dict) -> bytes:
    ordered = {
        code: [list(interval) for interval in intervals]
        for code, intervals in sorted(table.items())
    }
    return json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def baseline_digest(version: str) -> str:
    return sha256(_canonical(baseline_table(version))).hexdigest()


def baseline_meta(version: str) -> dict:
    try:
        meta = BASELINE_META[version]
    except KeyError:
        raise ValueError("UNKNOWN_BASELINE")
    return {
        "version": version,
        "label": meta["label"],
        "effective_from": meta["effective_from"],
        "parent": meta["parent"],
        "note": meta["note"],
        "digest": baseline_digest(version),
    }


def list_baselines() -> list[dict]:
    return [baseline_meta(version) for version in BASELINE_TABLES]


def _intervals(table: dict) -> dict[str, list[dict]]:
    return {
        code: [
            {"start": start, "end": end, "timezone": zone, "terminal": terminal}
            for start, end, zone, terminal in intervals
        ]
        for code, intervals in table.items()
    }


def baseline_diff(from_version: str, to_version: str) -> list[dict]:
    """逐机场列出两版基准的区间差异，按机场排序。"""
    before = _intervals(baseline_table(from_version))
    after = _intervals(baseline_table(to_version))
    changes = []
    for code in sorted(set(before) | set(after)):
        old = before.get(code, [])
        new = after.get(code, [])
        if old == new:
            continue
        changes.append({"airport": code, "old": old, "new": new})
    return changes


def changed_airports(from_version: str, to_version: str) -> list[str]:
    return sorted({change["airport"] for change in baseline_diff(from_version, to_version)})
