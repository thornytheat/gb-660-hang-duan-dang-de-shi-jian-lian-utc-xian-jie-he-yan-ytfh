import sqlite3

from app.config import DATABASE_PATH as _CONFIGURED_DATABASE_PATH

# 模块级副本便于测试与部署覆盖；连接时始终读取本模块属性。
DATABASE_PATH = _CONFIGURED_DATABASE_PATH


def connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DATABASE_PATH)


def init_db():
    with connect() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL, "
            "accepted INTEGER NOT NULL, quarantined INTEGER NOT NULL)"
        )
        for column, declaration in (("baseline_version", "TEXT"), ("rule_version", "TEXT")):
            try:
                db.execute(f"ALTER TABLE runs ADD COLUMN {column} {declaration}")
            except sqlite3.OperationalError:
                pass


def record_run(manifest: dict) -> bool:
    init_db()
    with connect() as db:
        before = db.total_changes
        db.execute(
            "INSERT OR IGNORE INTO runs (run_id, input_sha256, accepted, quarantined, baseline_version, rule_version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                manifest["run_id"],
                manifest["input_sha256"],
                manifest["accepted"],
                manifest["quarantined"],
                manifest.get("baseline_version"),
                manifest.get("rule_version"),
            ),
        )
        return db.total_changes > before


def run_count() -> int:
    init_db()
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def run_record(run_id_value: str) -> dict | None:
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT run_id, input_sha256, accepted, quarantined, baseline_version, rule_version FROM runs WHERE run_id = ?",
            (run_id_value,),
        ).fetchone()
    return dict(zip(("run_id", "input_sha256", "accepted", "quarantined", "baseline_version", "rule_version"), row)) if row else None
