"""现行基准判定。

现行基准只由“已发布的修正批次”推进：取所有已发布目标版本中生效日最晚
的一版。代码里新增一版定义不会自动改写已发出结论；发布失败也不会推进。
"""

import sqlite3
from datetime import date

from app import config
from app.modules.m2_airports.baselines import (
    DEFAULT_BASELINE,
    baseline_meta,
    baseline_table,
)


def published_baselines() -> list[dict]:
    try:
        connection = sqlite3.connect(config.DATABASE_PATH)
        try:
            rows = connection.execute(
                "SELECT DISTINCT to_baseline FROM corrections WHERE status = 'PUBLISHED'"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    versions = []
    for (version,) in rows:
        try:
            baseline_table(version)
        except ValueError:
            continue
        versions.append(baseline_meta(version))
    return sorted(versions, key=lambda meta: (date.fromisoformat(meta["effective_from"]), meta["version"]))


def current_baseline() -> str:
    versions = published_baselines()
    if not versions:
        return DEFAULT_BASELINE
    return versions[-1]["version"]


def baseline_for_date(target: str) -> str:
    """查询某个日期时，应报“那天在用的基准”，而不是把新版本套到过去。"""
    day = date.fromisoformat(target)
    applicable = [
        meta for meta in published_baselines() if date.fromisoformat(meta["effective_from"]) <= day
    ]
    if not applicable:
        return DEFAULT_BASELINE
    return applicable[-1]["version"]


def baseline_history() -> list[dict]:
    """已发布基准的推进顺序，供交接时确认“现在依据哪一版”。"""
    published = published_baselines()
    if not published or published[0]["version"] != DEFAULT_BASELINE:
        published = [baseline_meta(DEFAULT_BASELINE)] + published
    return published
