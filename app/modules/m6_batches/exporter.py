"""批次导出。

业务导出只保留一份证据清单：每行都带“依据哪版基准”和所属修正批次，
方便把外部拿到的 CSV 追回到具体批次与旧证据。
"""

import csv
import io

from app.modules.m7_corrections import repository as corrections

FIELDS = [
    "flight_key",
    "departure_utc",
    "arrival_utc",
    "status",
    "reason_code",
    "baseline_version",
    "fix_id",
]

SAMPLE = [
    {
        "flight_key": "MU5101|2026-11-16|PVG|JFK",
        "departure_utc": "2026-11-16T01:00:00+00:00",
        "arrival_utc": "2026-11-16T15:30:00+00:00",
        "status": "VALID",
        "reason_code": "",
        "baseline_version": "TZ-2026.10",
        "fix_id": "",
    }
]


def published_rows() -> list[dict]:
    """汇总所有已发布修正批次的证据，生成一份按批次时间倒序的导出清单。

    每行都带自己的 baseline_version 与 fix_id，不因后来换版而被覆盖；
    没有已发布批次时退回样例，保证空库也能导出表头。
    """
    batches = [batch for batch in corrections.list_batches() if batch["status"] == "PUBLISHED"]
    if not batches:
        return SAMPLE
    rows = []
    for batch in batches:
        for item in corrections.items(batch["fix_id"]):
            rows.append(
                {
                    "flight_key": item["leg_key"],
                    "departure_utc": item["departure_utc_after"],
                    "arrival_utc": item["arrival_utc_after"],
                    "status": item["status_after"],
                    "reason_code": "" if item["status_after"] == "VALID" else item["status_after"],
                    "baseline_version": item["baseline_after"],
                    "fix_id": item["fix_id"],
                }
            )
    return rows


def itineraries_csv(rows: list[dict]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
