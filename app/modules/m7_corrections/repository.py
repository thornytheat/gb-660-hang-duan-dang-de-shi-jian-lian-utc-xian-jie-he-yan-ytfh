"""修正发布的持久化：已发布批次、逐航段/逐转机影响清单与旧证据回溯。

对应交接要求：
- 唯一确定的 fix_id 让同一修正批次重复执行时原样复用，不新增交接项。
- 发布是单事务：批次、条目和 PUBLISHED 状态一起落库，要么全有要么全无。
  失败时只写一条失败审计，旧版排班（最近一次 PUBLISHED 批次）不受影响。
- 只登记两版基准差异真正触及的机场、航段与转机，不扩大范围。
"""

import json
import sqlite3
from datetime import datetime, timezone

from app import config

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS corrections ("
    "fix_id TEXT PRIMARY KEY, from_baseline TEXT NOT NULL, to_baseline TEXT NOT NULL, "
    "status TEXT NOT NULL, created_at TEXT NOT NULL, published_at TEXT, "
    "affected_airports TEXT NOT NULL, affected_legs INTEGER NOT NULL, affected_connections INTEGER NOT NULL DEFAULT 0, "
    "scope_digest TEXT NOT NULL, failure_reason TEXT)",
    "CREATE TABLE IF NOT EXISTS correction_items ("
    "fix_id TEXT NOT NULL, leg_key TEXT NOT NULL, flight_no TEXT NOT NULL, "
    "baseline_before TEXT NOT NULL, baseline_after TEXT NOT NULL, "
    "status_before TEXT NOT NULL, status_after TEXT NOT NULL, "
    "departure_utc_before TEXT, departure_utc_after TEXT, "
    "arrival_utc_before TEXT, arrival_utc_after TEXT, "
    "evidence_before TEXT NOT NULL, evidence_after TEXT NOT NULL, "
    "PRIMARY KEY (fix_id, leg_key))",
    "CREATE TABLE IF NOT EXISTS correction_legs ("
    "fix_id TEXT NOT NULL, leg_key TEXT NOT NULL, flight_no TEXT NOT NULL, "
    "origin TEXT NOT NULL, destination TEXT NOT NULL, "
    "departure_local TEXT NOT NULL, arrival_local TEXT NOT NULL, "
    "departure_fold INTEGER, arrival_fold INTEGER, "
    "PRIMARY KEY (fix_id, leg_key))",
    "CREATE TABLE IF NOT EXISTS correction_connections ("
    "fix_id TEXT NOT NULL, connection_key TEXT NOT NULL, airport TEXT NOT NULL, "
    "inbound_key TEXT NOT NULL, outbound_key TEXT NOT NULL, "
    "inbound_json TEXT NOT NULL, outbound_json TEXT NOT NULL, "
    "baseline_before TEXT NOT NULL, baseline_after TEXT NOT NULL, "
    "status_before TEXT NOT NULL, status_after TEXT NOT NULL, "
    "wait_before INTEGER, wait_after INTEGER, "
    "required_before INTEGER, required_after INTEGER, "
    "evidence_before TEXT NOT NULL, evidence_after TEXT NOT NULL, "
    "PRIMARY KEY (fix_id, connection_key))",
    "CREATE TABLE IF NOT EXISTS correction_failures ("
    "attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, fix_id TEXT NOT NULL, "
    "from_baseline TEXT NOT NULL, to_baseline TEXT NOT NULL, "
    "reason_code TEXT NOT NULL, scope_digest TEXT NOT NULL, attempted_at TEXT NOT NULL)",
)

MIGRATIONS = (
    "ALTER TABLE corrections ADD COLUMN affected_connections INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE corrections ADD COLUMN failure_reason TEXT",
)


def connect():
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with connect() as db:
        for statement in SCHEMA:
            db.execute(statement)
        for statement in MIGRATIONS:
            try:
                db.execute(statement)
            except sqlite3.OperationalError:
                pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    result["affected_airports"] = json.loads(result["affected_airports"])
    return result


def latest_published(exclude: str | None = None) -> dict | None:
    init_db()
    with connect() as db:
        if exclude:
            row = db.execute(
                "SELECT * FROM corrections WHERE status = 'PUBLISHED' AND fix_id != ? "
                "ORDER BY published_at DESC, fix_id DESC LIMIT 1",
                (exclude,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM corrections WHERE status = 'PUBLISHED' ORDER BY published_at DESC, fix_id DESC LIMIT 1"
            ).fetchone()
    return _decode(row)


def find(fix_id: str) -> dict | None:
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM corrections WHERE fix_id = ?", (fix_id,)).fetchone()
    return _decode(row)


def list_batches() -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM corrections ORDER BY created_at DESC, fix_id DESC").fetchall()
    return [_decode(row) for row in rows]


def items(fix_id: str) -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM correction_items WHERE fix_id = ? ORDER BY leg_key", (fix_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["evidence_before"] = json.loads(item["evidence_before"])
        item["evidence_after"] = json.loads(item["evidence_after"]) if item["evidence_after"] else None
        result.append(item)
    return result


def connections(fix_id: str) -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM correction_connections WHERE fix_id = ? ORDER BY connection_key", (fix_id,)
        ).fetchall()
    result = []
    for row in rows:
        entry = dict(row)
        for field in ("inbound_json", "outbound_json", "evidence_before", "evidence_after"):
            entry[field] = json.loads(entry[field]) if entry[field] else None
        result.append(entry)
    return result


def legs(fix_id: str) -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM correction_legs WHERE fix_id = ? ORDER BY leg_key", (fix_id,)).fetchall()
    return [dict(row) for row in rows]


def failures(fix_id: str | None = None) -> list[dict]:
    init_db()
    with connect() as db:
        if fix_id:
            rows = db.execute(
                "SELECT * FROM correction_failures WHERE fix_id = ? ORDER BY attempt_id", (fix_id,)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM correction_failures ORDER BY attempt_id").fetchall()
    return [dict(row) for row in rows]


def leg_history(leg_key: str) -> list[dict]:
    """从旧结论追回证据：按批次列出该航段修正前后的记录。"""
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT c.fix_id, c.from_baseline, c.to_baseline, c.status AS batch_status, c.published_at, i.* "
            "FROM correction_items i JOIN corrections c ON c.fix_id = i.fix_id "
            "WHERE i.leg_key = ? ORDER BY c.created_at, c.fix_id",
            (leg_key,),
        ).fetchall()
    history = []
    for row in rows:
        entry = dict(row)
        for field in ("evidence_before", "evidence_after"):
            entry[field] = json.loads(entry[field]) if entry[field] else None
        history.append(entry)
    return history


def connection_history(connection_key: str) -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT c.fix_id, c.from_baseline, c.to_baseline, c.status AS batch_status, c.published_at, i.* "
            "FROM correction_connections i JOIN corrections c ON c.fix_id = i.fix_id "
            "WHERE i.connection_key = ? ORDER BY c.created_at, c.fix_id",
            (connection_key,),
        ).fetchall()
    history = []
    for row in rows:
        entry = dict(row)
        for field in ("inbound_json", "outbound_json", "evidence_before", "evidence_after"):
            entry[field] = json.loads(entry[field]) if entry[field] else None
        history.append(entry)
    return history


def publish_batch(
    fix_id: str,
    from_baseline: str,
    to_baseline: str,
    airports: list[str],
    scope_digest: str,
    planned_legs: list[dict],
    planned_items: list[dict],
    planned_connections: list[dict],
) -> tuple[dict, bool]:
    """单事务写入并发布；任一写入失败整批回滚，不留半截交接项。"""
    init_db()
    with connect() as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM corrections WHERE fix_id = ?", (fix_id,)).fetchone()
            if existing:
                db.rollback()
                return _decode(existing), False
            affected_legs = sum(1 for item in planned_items if item["changed"])
            affected_connections = sum(1 for item in planned_connections if item["changed"])
            now = _now()
            db.execute(
                "INSERT INTO corrections (fix_id, from_baseline, to_baseline, status, created_at, published_at, "
                "affected_airports, affected_legs, affected_connections, scope_digest, failure_reason) "
                "VALUES (?, ?, ?, 'PUBLISHED', ?, ?, ?, ?, ?, ?, NULL)",
                (
                    fix_id,
                    from_baseline,
                    to_baseline,
                    now,
                    now,
                    json.dumps(airports, ensure_ascii=False),
                    affected_legs,
                    affected_connections,
                    scope_digest,
                ),
            )
            db.executemany(
                "INSERT INTO correction_legs (fix_id, leg_key, flight_no, origin, destination, departure_local, "
                "arrival_local, departure_fold, arrival_fold) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        fix_id,
                        leg["leg_key"],
                        leg["flight_no"],
                        leg["origin"],
                        leg["destination"],
                        leg["departure_local"],
                        leg["arrival_local"],
                        leg.get("departure_fold"),
                        leg.get("arrival_fold"),
                    )
                    for leg in planned_legs
                ],
            )
            db.executemany(
                "INSERT INTO correction_items (fix_id, leg_key, flight_no, baseline_before, baseline_after, "
                "status_before, status_after, departure_utc_before, departure_utc_after, arrival_utc_before, "
                "arrival_utc_after, evidence_before, evidence_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        fix_id,
                        item["leg_key"],
                        item["flight_no"],
                        from_baseline,
                        to_baseline,
                        item["status_before"],
                        item["status_after"],
                        item["departure_utc_before"],
                        item["departure_utc_after"],
                        item["arrival_utc_before"],
                        item["arrival_utc_after"],
                        json.dumps(item["evidence_before"], ensure_ascii=False),
                        json.dumps(item["evidence_after"], ensure_ascii=False),
                    )
                    for item in planned_items
                ],
            )
            db.executemany(
                "INSERT INTO correction_connections (fix_id, connection_key, airport, inbound_key, outbound_key, "
                "inbound_json, outbound_json, baseline_before, baseline_after, status_before, status_after, "
                "wait_before, wait_after, required_before, required_after, evidence_before, evidence_after) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        fix_id,
                        connection["connection_key"],
                        connection["airport"],
                        connection["inbound_key"],
                        connection["outbound_key"],
                        json.dumps(connection["inbound"], ensure_ascii=False),
                        json.dumps(connection["outbound"], ensure_ascii=False),
                        from_baseline,
                        to_baseline,
                        connection["status_before"],
                        connection["status_after"],
                        connection["wait_before"],
                        connection["wait_after"],
                        connection["required_before"],
                        connection["required_after"],
                        json.dumps(connection["evidence_before"], ensure_ascii=False),
                        json.dumps(connection["evidence_after"], ensure_ascii=False),
                    )
                    for connection in planned_connections
                ],
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        row = db.execute("SELECT * FROM corrections WHERE fix_id = ?", (fix_id,)).fetchone()
    return _decode(row), True


def record_failure(fix_id: str, from_baseline: str, to_baseline: str, reason_code: str, scope_digest: str) -> None:
    """失败只留审计，不写交接项，也不触碰旧版排班。"""
    init_db()
    with connect() as db:
        db.execute(
            "INSERT INTO correction_failures (fix_id, from_baseline, to_baseline, reason_code, scope_digest, attempted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fix_id, from_baseline, to_baseline, reason_code, scope_digest, _now()),
        )
