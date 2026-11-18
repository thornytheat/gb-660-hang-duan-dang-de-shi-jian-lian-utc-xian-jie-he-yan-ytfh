import pytest

from app import config
from app.manifest import run_id
from app.modules.m2_airports import baselines
from app.modules.m2_airports.baselines import baseline_diff, baseline_digest, changed_airports
from app.modules.m2_airports.registry import airport_version
from app.modules.m4_itinerary.builder import build_leg
from app.modules.m7_corrections import repository, service
from app.modules.m7_corrections.service import publish

LATE_LEG = {
    "flight_no": "UA857",
    "flight_date": "2026-11-24",
    "origin": "JFK",
    "destination": "SFO",
    "departure_local": "2026-11-24T20:00",
    "arrival_local": "2026-11-25T00:30",
}
EARLY_LEG = {
    "flight_no": "UA857",
    "flight_date": "2026-11-24",
    "origin": "JFK",
    "destination": "SFO",
    "departure_local": "2026-11-24T15:00",
    "arrival_local": "2026-11-24T23:30",
}
UNTOUCHED_LEG = {
    "flight_no": "MU5101",
    "flight_date": "2026-11-16",
    "origin": "PVG",
    "destination": "JFK",
    "departure_local": "2026-11-16T09:00",
    "arrival_local": "2026-11-16T10:30",
}
OUTBOUND_LEG = {
    "flight_no": "UA858",
    "flight_date": "2026-11-25",
    "origin": "SFO",
    "destination": "JFK",
    "departure_local": "2026-11-25T00:30",
    "arrival_local": "2026-11-25T09:00",
}
IMPOSSIBLE_LEG = {
    "flight_no": "BA178",
    "flight_date": "2027-03-13",
    "origin": "SFO",
    "destination": "JFK",
    "departure_local": "2027-03-13T18:00",
    "arrival_local": "2027-03-14T02:30",
}


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "corrections.sqlite3")
    return tmp_path


def test_baseline_versions_are_immutable_snapshots():
    assert baseline_digest("TZ-2026.10") == baseline_digest("TZ-2026.10")
    assert baseline_digest("TZ-2026.10") != baseline_digest("TZ-2026.11")
    assert changed_airports("TZ-2026.10", "TZ-2026.11") == ["SFO"]
    changes = baseline_diff("TZ-2026.10", "TZ-2026.11")
    assert len(changes) == 1
    assert [item["timezone"] for item in changes[0]["new"]] == [
        "America/Denver",
        "America/Los_Angeles",
    ]


def test_new_baseline_only_applies_from_effective_date():
    # 历史区间保持原映射，已发出的历史排班不会被换版悄悄改写。
    assert airport_version("SFO", "2026-11-24", "TZ-2026.11")["timezone"] == "America/Denver"
    assert airport_version("SFO", "2026-11-25", "TZ-2026.11")["timezone"] == "America/Los_Angeles"
    assert airport_version("SFO", "2026-11-25", "TZ-2026.10")["timezone"] == "America/Denver"


def test_leg_evidence_records_baseline_version_and_digest():
    leg = build_leg(LATE_LEG, "TZ-2026.10")
    arrival = leg["evidence"]["arrival"]
    assert arrival["baseline_version"] == "TZ-2026.10"
    assert len(arrival["baseline_digest"]) == 64
    assert arrival["offset"] == "-07:00"


def test_publish_lists_only_affected_legs(database):
    result = publish({"legs": [LATE_LEG, UNTOUCHED_LEG]})
    assert result["status"] == "PUBLISHED"
    assert result["scope"]["affected_airports"] == ["SFO"]
    assert [item["leg_key"] for item in result["affected"]] == ["UA857|2026-11-24|JFK|SFO"]
    # 与差异机场无关的航段根本没有进入批次，不占用交接清单。
    assert result["scope"]["registered_legs"] == 1
    assert UNTOUCHED_LEG["flight_no"] not in {
        row["flight_no"] for row in repository.legs(result["fix_id"])
    }
    before, after = result["affected"][0]["before"], result["affected"][0]["after"]
    assert before["baseline"] == "TZ-2026.10" and after["baseline"] == "TZ-2026.11"
    assert before["arrival_utc"] == "2026-11-25T07:30:00Z"
    assert after["arrival_utc"] == "2026-11-25T08:30:00Z"
    assert result["previous_published"] is None


def test_publish_reports_connection_recomputation(database):
    result = publish(
        {
            "legs": [EARLY_LEG, OUTBOUND_LEG],
            "connections": [{"airport": "SFO", "inbound": EARLY_LEG, "outbound": OUTBOUND_LEG}],
        }
    )
    assert result["scope"]["affected_legs"] == 1
    assert result["scope"]["affected_connections"] == 1
    connection = result["affected_connections"][0]
    assert connection["before"]["wait_minutes"] == 60
    assert connection["after"]["wait_minutes"] == 120
    assert connection["before"]["baseline"] == "TZ-2026.10"
    assert connection["after"]["evidence"]["transfer"]["baseline_version"] == "TZ-2026.11"


def test_repeated_batch_is_idempotent(database):
    first = publish({"legs": [LATE_LEG]})
    second = publish({"legs": [LATE_LEG]})
    assert second["fix_id"] == first["fix_id"]
    assert second["reused"] is True
    batches = service.batches()["batches"]
    assert len(batches) == 1 and batches[0]["affected_legs"] == 1


def test_repeated_batch_does_not_expand_scope(database):
    publish({"legs": [LATE_LEG]})
    extra = {
        "flight_no": "ZZ1",
        "flight_date": "2026-11-24",
        "origin": "SFO",
        "destination": "JFK",
        "departure_local": "2026-11-24T10:00",
        "arrival_local": "2026-11-24T18:00",
    }
    repeat = publish({"legs": [LATE_LEG, extra]})
    assert repeat["reused"] is True
    assert repeat["content_changed"] is True
    assert repeat["scope"]["registered_legs"] == 1
    assert repeat["scope"]["ignored"] == ["ZZ1|2026-11-24|SFO|JFK"]


def test_publish_fails_atomically_when_a_target_leg_breaks(database, monkeypatch):
    good = publish({"legs": [LATE_LEG]})
    assert good["status"] == "PUBLISHED"
    # 临时声明下一版基准，并让一条航段在目标版下算不出来。
    monkeypatch.setitem(
        baselines.BASELINE_TABLES,
        "TZ-2026.12",
        {
            "SFO": [("2000-01-01", None, "Europe/London", "I")],
            "JFK": [("2000-01-01", None, "America/New_York", "T4")],
        },
    )
    monkeypatch.setitem(
        baselines.BASELINE_META,
        "TZ-2026.12",
        {"label": "临时", "effective_from": "2027-01-01", "parent": "TZ-2026.11", "note": "test"},
    )
    # 出发地 01:30 在旧基准（洛杉矶）存在，在临时新基准（伦敦）当天落在 DST 跳时里。
    failing = {
        "flight_no": "UA999",
        "flight_date": "2027-03-28",
        "origin": "SFO",
        "destination": "JFK",
        "departure_local": "2027-03-28T01:30",
        "arrival_local": "2027-03-28T08:00",
    }
    assert build_leg(failing, "TZ-2026.11")["duration_minutes"] > 0
    with pytest.raises(ValueError):
        publish({"legs": [failing], "to": "TZ-2026.12"})
    state = service.batches()
    assert [batch["status"] for batch in state["batches"]] == ["PUBLISHED"]
    assert state["current_baseline"] == "TZ-2026.11"
    assert len(state["failures"]) == 1
    assert service.history("leg", "UA857|2026-11-24|JFK|SFO")["history"][0]["status_before"] == "VALID"


def test_failed_publish_writes_no_handover_items(database):
    # 换版前就已无法解析的航段属于上游数据问题：不进批次、不阻塞发布，
    # 也不能让现行基准在没有可交接结论时先行推进。
    with pytest.raises(ValueError) as error:
        publish({"legs": [IMPOSSIBLE_LEG]})
    assert error.value.code == "CORRECTION_EMPTY"
    state = service.batches()
    assert state["batches"] == []
    assert state["current_baseline"] == "TZ-2026.10"
    assert len(state["failures"]) == 1
    assert state["failures"][0]["reason_code"] == "CORRECTION_EMPTY"


def test_persistence_failure_rolls_back_whole_batch(database):
    baseline = publish({"legs": [EARLY_LEG]})
    assert baseline["status"] == "PUBLISHED"
    with repository.connect() as db:
        db.execute(
            "CREATE TRIGGER fail_connections BEFORE INSERT ON correction_connections "
            "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
        )
    connection = {
        "connection_key": "SFO|A->B",
        "airport": "SFO",
        "inbound_key": "A",
        "outbound_key": "B",
        "inbound": EARLY_LEG,
        "outbound": OUTBOUND_LEG,
        "status_before": "VALID",
        "status_after": "TOO_SHORT",
        "wait_before": 60,
        "wait_after": 10,
        "required_before": 80,
        "required_after": 80,
        "evidence_before": {},
        "evidence_after": {},
    }
    with pytest.raises(Exception):
        repository.publish_batch(
            "FIX-TZ-2026.10-TZ-2026.11-BROKEN",
            "TZ-2026.10",
            "TZ-2026.11",
            ["SFO"],
            "digest",
            [dict(EARLY_LEG, leg_key="x")],
            [],
            [connection],
        )
    assert repository.find("FIX-TZ-2026.10-TZ-2026.11-BROKEN") is None
    assert repository.items("FIX-TZ-2026.10-TZ-2026.11-BROKEN") == []
    assert repository.legs("FIX-TZ-2026.10-TZ-2026.11-BROKEN") == []
    state = service.batches()
    assert state["current_baseline"] == "TZ-2026.11"
    assert state["batches"][0]["fix_id"] == baseline["fix_id"]


def test_history_recovers_evidence_from_old_conclusion(database):
    published = publish({"legs": [LATE_LEG]})
    leg_key = published["affected"][0]["leg_key"]
    entry = service.history("leg", leg_key)["history"][0]
    assert entry["from_baseline"] == "TZ-2026.10"
    assert entry["to_baseline"] == "TZ-2026.11"
    assert entry["evidence_before"]["arrival"]["baseline_version"] == "TZ-2026.10"
    assert entry["evidence_before"]["arrival"]["utc"] == "2026-11-25T07:30:00Z"
    assert entry["evidence_after"]["arrival"]["baseline_version"] == "TZ-2026.11"
    assert entry["evidence_before"]["arrival"]["baseline_digest"] == baseline_digest("TZ-2026.10")


def test_run_id_changes_with_baseline():
    assert run_id(b"x", "TZ-2026.10") != run_id(b"x", "TZ-2026.11")
    assert run_id(b"x", "TZ-2026.10") == run_id(b"x", "TZ-2026.10")
