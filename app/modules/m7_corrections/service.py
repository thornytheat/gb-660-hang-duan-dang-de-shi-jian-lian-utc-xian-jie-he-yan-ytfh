"""机场基准修正发布。

对外语义：
- 修正批次由（来源基准、目标基准）唯一确定；同一批次重复执行原样复用，
  请求里多出的航段/转机只会被明确列在 ignored 中，不进入交接清单。
- 每项结论都同时保留“依据哪版基准”和可独立复核的证据（当地时刻、时区、
  偏移、UTC 与基准摘要），以支持从旧结论追回当时的证据。
- 发布是一次单事务写入：任何一条结论算不出来就整批失败，只留失败审计，
  旧版排班（最近一次 PUBLISHED 批次）保持不变。
- 只有与两版基准差异机场相关的航段与转机进入批次，其他结论原样不参与。
- 换版前就已被判为无法解析的条目属于上游数据问题，排除在批次外并单列，
  既不占用交接清单，也不会拖垮整批发布。
"""

import json
from hashlib import sha256

from app.baseline_state import current_baseline
from app.errors import DomainError
from app.modules.m1_legs.keys import flight_key
from app.modules.m2_airports.baselines import (
    CORRECTION_BASELINE,
    baseline_diff,
    baseline_meta,
    changed_airports,
)
from app.modules.m4_itinerary.builder import leg_evidence
from app.modules.m5_connections.evidence import connection_evidence

from . import repository

PUBLISHABLE_LEG_STATUSES = {"VALID", "INVALID_DURATION"}
CONNECTION_STATUSES = {"VALID", "TOO_SHORT", "TOO_LONG", "OVERLAP"}
FOLD_FIELDS = ("departure_fold", "arrival_fold")
LEG_FIELDS = ("leg_key", "flight_no", "flight_date", "origin", "destination", "departure_local", "arrival_local", *FOLD_FIELDS)


def _leg_input(row: dict) -> dict:
    leg = {
        "flight_no": str(row["flight_no"]).strip().upper().replace(" ", ""),
        "flight_date": str(row.get("flight_date") or str(row["departure_local"])[:10]).strip(),
        "origin": str(row["origin"]).strip().upper(),
        "destination": str(row["destination"]).strip().upper(),
        "departure_local": str(row["departure_local"]).strip(),
        "arrival_local": str(row["arrival_local"]).strip(),
    }
    for field in FOLD_FIELDS:
        value = row.get(field)
        leg[field] = int(value) if value not in (None, "") else None
    return leg


def _connection_key(airport: str, inbound_key: str, outbound_key: str) -> str:
    return f"{airport}|{inbound_key}->{outbound_key}"


def _digest(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(body).hexdigest()


def _fix_id(from_baseline: str, to_baseline: str) -> str:
    """批次身份 = 版本对：一次基准更正就是一个修正批次。

    范围在首次成功发布时冻结。重复执行必复用同一批次，请求里多出的
    航段/转机只列进 ignored，既不会新增重复交接项，也不会扩大影响范围。
    """
    return f"FIX-{from_baseline}-{to_baseline}"


def _candidates(rows: list[dict], airports: list[str]) -> dict[str, dict]:
    """只挑与差异机场直接相关的航段；其余航段不进入批次。"""
    touched = set(airports)
    by_key = {}
    for row in rows:
        leg = _leg_input(row)
        if leg["origin"] not in touched and leg["destination"] not in touched:
            continue
        try:
            key = flight_key(leg)
        except (KeyError, ValueError):
            key = "|".join((leg["flight_no"], leg["origin"], leg["destination"], leg["departure_local"]))
        leg["leg_key"] = key
        # 同一业务键只登记一次，重复输入不会产生重复交接项。
        by_key.setdefault(key, leg)
    return {key: by_key[key] for key in sorted(by_key)}


def _connection_candidates(rows: list[dict], airports: list[str]) -> dict[str, dict]:
    """转机机场必须在差异内，且入港/出港航段确实经过该机场。"""
    touched = set(airports)
    by_key = {}
    for row in rows:
        airport = str(row["airport"]).strip().upper()
        if airport not in touched:
            continue
        inbound = _leg_input(row["inbound"])
        outbound = _leg_input(row["outbound"])
        if airport not in {inbound["destination"], outbound["origin"]}:
            continue
        inbound_key = flight_key(inbound)
        outbound_key = flight_key(outbound)
        connection_key = _connection_key(airport, inbound_key, outbound_key)
        by_key.setdefault(
            connection_key,
            {
                "connection_key": connection_key,
                "airport": airport,
                "inbound": inbound,
                "outbound": outbound,
                "inbound_key": inbound_key,
                "outbound_key": outbound_key,
            },
        )
    return {key: by_key[key] for key in sorted(by_key)}


def _leg_snapshot(leg: dict, baseline: str) -> dict:
    evidence = leg_evidence(leg, baseline)
    return {
        "baseline": baseline,
        "status": evidence["status"],
        "departure_utc": evidence["departure_utc"],
        "arrival_utc": evidence["arrival_utc"],
        "duration_minutes": evidence["duration_minutes"],
        "evidence": evidence["evidence"],
    }


def _connection_snapshot(connection: dict, baseline: str) -> dict:
    evidence = connection_evidence(connection["inbound"], connection["outbound"], connection["airport"], baseline)
    return {
        "baseline": baseline,
        "status": evidence["status"],
        "wait_minutes": evidence["wait_minutes"],
        "required_minutes": evidence["required_minutes"],
        "evidence": evidence["evidence"],
    }


def _leg_changed(before: dict, after: dict) -> bool:
    return (
        before["status"] != after["status"]
        or before["departure_utc"] != after["departure_utc"]
        or before["arrival_utc"] != after["arrival_utc"]
    )


def _connection_differs(before: dict, after: dict) -> bool:
    return (
        before["status"] != after["status"]
        or before["wait_minutes"] != after["wait_minutes"]
        or before["required_minutes"] != after["required_minutes"]
    )


def _resolve_versions(payload: dict) -> tuple[str, str]:
    """版本必须可复现：默认来源版取目标版声明的父版，而不是运行时现行版。

    这样同一修正批次重复执行总能落到同一 fix_id，不会因为第一次发布
    已经推进了现行基准而变成“无差异”。
    """
    to_baseline = payload.get("to") or CORRECTION_BASELINE
    from_baseline = payload.get("from") or baseline_meta(to_baseline)["parent"]
    if from_baseline is None:
        raise DomainError("BAD_REQUEST", f"{to_baseline} 未声明父版，必须显式给出 from", 422)
    if from_baseline == to_baseline:
        raise DomainError("CORRECTION_NOOP", "来源基准与目标基准相同", 409)
    baseline_meta(from_baseline)
    return from_baseline, to_baseline


def _plan_legs(legs: dict[str, dict], from_baseline: str, to_baseline: str) -> list[dict]:
    planned = []
    for leg in legs.values():
        before = _leg_snapshot(leg, from_baseline)
        after = _leg_snapshot(leg, to_baseline)
        planned.append(
            {
                "leg_key": leg["leg_key"],
                "flight_no": leg["flight_no"],
                "status_before": before["status"],
                "status_after": after["status"],
                "departure_utc_before": before["departure_utc"],
                "departure_utc_after": after["departure_utc"],
                "arrival_utc_before": before["arrival_utc"],
                "arrival_utc_after": after["arrival_utc"],
                "evidence_before": before["evidence"],
                "evidence_after": after["evidence"],
                "changed": _leg_changed(before, after),
            }
        )
    return planned


def _plan_connections(connections: dict[str, dict], from_baseline: str, to_baseline: str) -> list[dict]:
    planned = []
    for connection in connections.values():
        before = _connection_snapshot(connection, from_baseline)
        after = _connection_snapshot(connection, to_baseline)
        planned.append(
            {
                "connection_key": connection["connection_key"],
                "airport": connection["airport"],
                "inbound_key": connection["inbound_key"],
                "outbound_key": connection["outbound_key"],
                "inbound": connection["inbound"],
                "outbound": connection["outbound"],
                "status_before": before["status"],
                "status_after": after["status"],
                "wait_before": before["wait_minutes"],
                "wait_after": after["wait_minutes"],
                "required_before": before["required_minutes"],
                "required_after": after["required_minutes"],
                "evidence_before": before["evidence"],
                "evidence_after": after["evidence"],
                "changed": _connection_differs(before, after),
            }
        )
    return planned


def _validate(planned_items: list[dict], planned_connections: list[dict], to_baseline: str) -> None:
    """发布前一次性检查所有结论；任一不可交接则整批失败，不写半截清单。"""
    for item in planned_items:
        if item["status_after"] not in PUBLISHABLE_LEG_STATUSES or item["evidence_after"] is None:
            raise DomainError(
                "CORRECTION_UNRESOLVED",
                f"航段 {item['leg_key']} 在 {to_baseline} 下无法解析：{item['status_after']}",
            )
    for connection in planned_connections:
        if connection["status_after"] not in CONNECTION_STATUSES or connection["evidence_after"] is None:
            raise DomainError(
                "CORRECTION_UNRESOLVED",
                f"转机 {connection['connection_key']} 在 {to_baseline} 下无法判定：{connection['status_after']}",
            )


def _resolvable(status: str, kind: str) -> bool:
    allowed = PUBLISHABLE_LEG_STATUSES if kind == "leg" else CONNECTION_STATUSES
    return status in allowed


def _partition(planned: list[dict], key: str, kind: str) -> tuple[list[dict], list[dict]]:
    """把“换版前就已无法解析”的条目排除出批次；那属于上游数据问题，
    不应因为一次基准更正被卷进来，也不应阻塞整批发布。"""
    keep, excluded = [], []
    for entry in planned:
        if _resolvable(entry["status_before"], kind):
            keep.append(entry)
        else:
            excluded.append({"key": entry[key], "status_before": entry["status_before"]})
    return keep, excluded


def publish(payload: dict) -> dict:
    """登记并发布修正批次；失败时旧版排班保持最近一次已发布状态。"""
    rows = payload.get("legs") or []
    connection_rows = payload.get("connections") or []
    from_baseline, to_baseline = _resolve_versions(payload)
    # 只认两版基准真实差异触及的机场，避免借修正之名扩大范围。
    airports = changed_airports(from_baseline, to_baseline)
    if not airports:
        raise DomainError("CORRECTION_NO_DIFF", "两版基准之间没有差异", 409)

    fix_id = _fix_id(from_baseline, to_baseline)

    existing = repository.find(fix_id)
    if existing:
        # 同一修正批次重复执行：原样复用已发布的交接清单，不新增任何条目，
        # 请求里多出的航段/转机只列在 ignored，不扩大受影响范围。
        legs = _candidates(rows, airports)
        connections = _connection_candidates(connection_rows, airports)
        registered_legs = {row["leg_key"] for row in repository.legs(fix_id)}
        registered_connections = {row["connection_key"] for row in repository.connections(fix_id)}
        ignored = sorted(set(legs) - registered_legs) + sorted(set(connections) - registered_connections)
        return _view(fix_id, reused=True, ignored=ignored, content_changed=bool(ignored))

    legs = _candidates(rows, airports)
    connections = _connection_candidates(connection_rows, airports)
    planned_items = [item for item in _plan_legs(legs, from_baseline, to_baseline)]
    planned_connections = [item for item in _plan_connections(connections, from_baseline, to_baseline)]
    planned_items, excluded_legs = _partition(planned_items, "leg_key", "leg")
    planned_connections, excluded_connections = _partition(planned_connections, "connection_key", "connection")

    if not planned_items and not planned_connections:
        reason = "CORRECTION_EMPTY"
        repository.record_failure(
            fix_id,
            from_baseline,
            to_baseline,
            reason,
            _digest({"from": from_baseline, "to": to_baseline, "legs": [], "connections": []}),
        )
        raise DomainError(
            reason,
            "没有可交接的受影响航段或转机；换版前就无法解析的条目需先按上游数据问题处理",
            409,
        )

    scope_digest = _digest(
        {
            "from": from_baseline,
            "to": to_baseline,
            "legs": [
                {field: leg.get(field) for field in LEG_FIELDS}
                for leg in legs.values()
                if leg["leg_key"] in {item["leg_key"] for item in planned_items}
            ],
            "connections": [
                {
                    "key": connection["connection_key"],
                    "airport": connection["airport"],
                    "inbound": connection["inbound"],
                    "outbound": connection["outbound"],
                }
                for connection in connections.values()
                if connection["connection_key"] in {item["connection_key"] for item in planned_connections}
            ],
        }
    )
    kept_leg_keys = {item["leg_key"] for item in planned_items}
    try:
        _validate(planned_items, planned_connections, to_baseline)
        repository.publish_batch(
            fix_id,
            from_baseline,
            to_baseline,
            airports,
            scope_digest,
            [leg for leg in legs.values() if leg["leg_key"] in kept_leg_keys],
            planned_items,
            planned_connections,
        )
    except DomainError as error:
        repository.record_failure(fix_id, from_baseline, to_baseline, error.code, scope_digest)
        raise
    except Exception as error:  # 持久化异常同样不得破坏旧排班
        repository.record_failure(fix_id, from_baseline, to_baseline, type(error).__name__, scope_digest)
        raise
    return _view(
        fix_id,
        reused=False,
        ignored=[],
        excluded_legs=excluded_legs,
        excluded_connections=excluded_connections,
    )


def get(fix_id: str) -> dict:
    if not repository.find(fix_id):
        raise DomainError("UNKNOWN_CORRECTION", "修正批次不存在", 404)
    return _view(fix_id, reused=True, ignored=[])


def batches() -> dict:
    return {
        "batches": repository.list_batches(),
        "failures": repository.failures(),
        "current_baseline": current_baseline(),
    }


def history(kind: str, key: str) -> dict:
    if kind == "leg":
        rows = repository.leg_history(key)
        entries = [
            {
                "fix_id": row["fix_id"],
                "batch_status": row["batch_status"],
                "published_at": row["published_at"],
                "from_baseline": row["from_baseline"],
                "to_baseline": row["to_baseline"],
                "status_before": row["status_before"],
                "status_after": row["status_after"],
                "departure_utc_before": row["departure_utc_before"],
                "departure_utc_after": row["departure_utc_after"],
                "arrival_utc_before": row["arrival_utc_before"],
                "arrival_utc_after": row["arrival_utc_after"],
                "evidence_before": row["evidence_before"],
                "evidence_after": row["evidence_after"],
            }
            for row in rows
        ]
        return {"kind": "leg", "key": key, "history": entries}
    if kind == "connection":
        rows = repository.connection_history(key)
        entries = [
            {
                "fix_id": row["fix_id"],
                "batch_status": row["batch_status"],
                "published_at": row["published_at"],
                "from_baseline": row["from_baseline"],
                "to_baseline": row["to_baseline"],
                "airport": row["airport"],
                "inbound_key": row["inbound_key"],
                "outbound_key": row["outbound_key"],
                "status_before": row["status_before"],
                "status_after": row["status_after"],
                "wait_before": row["wait_before"],
                "wait_after": row["wait_after"],
                "required_before": row["required_before"],
                "required_after": row["required_after"],
                "evidence_before": row["evidence_before"],
                "evidence_after": row["evidence_after"],
            }
            for row in rows
        ]
        return {"kind": "connection", "key": key, "history": entries}
    raise DomainError("BAD_HISTORY_KIND", "历史查询类型必须是 leg 或 connection", 422)


def _view(
    fix_id: str,
    reused: bool,
    ignored: list[str],
    content_changed: bool = False,
    excluded_legs: list[dict] | None = None,
    excluded_connections: list[dict] | None = None,
) -> dict:
    batch = repository.find(fix_id)
    if not batch:
        raise DomainError("UNKNOWN_CORRECTION", "修正批次不存在", 404)
    items = repository.items(fix_id)
    connections = repository.connections(fix_id)
    affected = [_item_view(item) for item in items if _item_changed(item)]
    untouched = [
        {"leg_key": item["leg_key"], "status": item["status_before"]}
        for item in items
        if not _item_changed(item)
    ]
    connection_result = [_connection_view(connection) for connection in connections if _connection_record_changed(connection)]
    untouched_connections = [
        {"connection_key": connection["connection_key"], "status": connection["status_before"]}
        for connection in connections
        if not _connection_record_changed(connection)
    ]
    return {
        "fix_id": fix_id,
        "reused": reused,
        "content_changed": content_changed,
        "status": batch["status"],
        "failure_reason": batch["failure_reason"],
        "published_at": batch["published_at"],
        "from_baseline": baseline_meta(batch["from_baseline"]),
        "to_baseline": baseline_meta(batch["to_baseline"]),
        "scope": {
            "affected_airports": batch["affected_airports"],
            "baseline_changes": baseline_diff(batch["from_baseline"], batch["to_baseline"]),
            "affected_legs": batch["affected_legs"],
            "affected_connections": batch["affected_connections"],
            "unchanged_legs": untouched,
            "unchanged_connections": untouched_connections,
            "scope_digest": batch["scope_digest"],
            "registered_legs": len(items),
            "registered_connections": len(connections),
            "ignored": ignored,
            "excluded_unresolvable": {
                "legs": excluded_legs or [],
                "connections": excluded_connections or [],
            },
        },
        "affected": affected,
        "affected_connections": connection_result,
        "previous_published": _previous_published(batch),
    }


def _item_changed(item: dict) -> bool:
    return (
        item["status_before"] != item["status_after"]
        or item["departure_utc_before"] != item["departure_utc_after"]
        or item["arrival_utc_before"] != item["arrival_utc_after"]
    )


def _connection_record_changed(connection: dict) -> bool:
    return (
        connection["status_before"] != connection["status_after"]
        or connection["wait_before"] != connection["wait_after"]
        or connection["required_before"] != connection["required_after"]
    )


def _item_view(item: dict) -> dict:
    return {
        "leg_key": item["leg_key"],
        "flight_no": item["flight_no"],
        "before": {
            "baseline": item["baseline_before"],
            "status": item["status_before"],
            "departure_utc": item["departure_utc_before"],
            "arrival_utc": item["arrival_utc_before"],
            "evidence": item["evidence_before"],
        },
        "after": {
            "baseline": item["baseline_after"],
            "status": item["status_after"],
            "departure_utc": item["departure_utc_after"],
            "arrival_utc": item["arrival_utc_after"],
            "evidence": item["evidence_after"],
        },
    }


def _connection_view(connection: dict) -> dict:
    return {
        "connection_key": connection["connection_key"],
        "airport": connection["airport"],
        "before": {
            "baseline": connection["baseline_before"],
            "status": connection["status_before"],
            "wait_minutes": connection["wait_before"],
            "required_minutes": connection["required_before"],
            "evidence": connection["evidence_before"],
        },
        "after": {
            "baseline": connection["baseline_after"],
            "status": connection["status_after"],
            "wait_minutes": connection["wait_after"],
            "required_minutes": connection["required_after"],
            "evidence": connection["evidence_after"],
        },
    }


def _previous_published(batch: dict) -> dict | None:
    """给出仍在生效的旧排班，便于交接时确认回退位置。"""
    published = repository.latest_published(exclude=batch["fix_id"])
    if not published:
        return None
    return {
        "fix_id": published["fix_id"],
        "to_baseline": published["to_baseline"],
        "published_at": published["published_at"],
    }
