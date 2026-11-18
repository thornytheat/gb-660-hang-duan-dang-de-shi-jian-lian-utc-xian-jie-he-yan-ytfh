# 航班时刻运行台

面向真实数据复核的中型数据工程工具，包含七个可独立调用的业务模块。规则、失败原因与证据字段见 docs/DATA-DICTIONARY.md，陷阱和测试映射见 docs/DEPTH.md。

## 启动

docker compose up --build

浏览器访问 http://localhost:18219。测试：docker compose exec -T app pytest -q

## 模块

- M1 航段采信：页面 /legs，接口 /api/legs/import
- M2 机场时区：页面 /airports，接口 /api/airports
- M3 当地时刻：页面 /local-time，接口 /api/local-time/resolve
- M4 UTC 航链：页面 /itineraries，接口 /api/itineraries/build
- M5 衔接判定：页面 /connections，接口 /api/connections/check
- M6 批次导出：页面 /batches，接口 /api/reconcile
- M7 基准修正发布：页面 /baseline-fixes，接口 /api/baseline-fixes

## 运行规则

同一输入内容生成相同运行标识，重复执行复用已有批次。隔离记录包含原因码、原始行号与原值；业务导出只保留一份证据清单。

## 时区基准修正（下周生效）

机场时区基准是带版本的完整快照，不是一张会原地改写的表：

- 现行 `TZ-2026.10`，下一版 `TZ-2026.11`（自 2026-11-25 起 SFO 由 America/Denver 更正为 America/Los_Angeles）。
- 每条航段、转机结论都带 `baseline_version` 与 `baseline_digest`，说明依据哪版基准。
- 修正按区间生效：2026-11-25 之前的历史区间保持原映射，已经发出的历史排班不会被新基准悄悄改写。
- 只有跨越生效日的跨日航段/衔接等待会被重算，因为它们的到达或离开落在新映射区间。

修正发布入口 `POST /api/baseline-fixes`：

```json
{
  "from": "TZ-2026.10",
  "to": "TZ-2026.11",
  "legs": [ { "flight_no": "UA857", "flight_date": "2026-11-24", "origin": "JFK",
              "destination": "SFO", "departure_local": "2026-11-24T20:00",
              "arrival_local": "2026-11-25T00:30" } ],
  "connections": [ { "airport": "SFO", "inbound": { ... }, "outbound": { ... } } ]
}
```

返回 `affected` / `affected_connections`（修正前后基准、UTC、等待分钟、证据）、
`scope.affected_airports`（仅两版差异触及的机场）与 `scope.unchanged_*`。
与差异机场无关的航段不会进入批次，`scope.registered_legs` 可核对。

交接约定：

- 同一修正批次（同一 `from`/`to`）重复执行返回同一 `fix_id` 与 `reused: true`，不新增交接项；多传的航段列在 `scope.ignored`，不扩大范围。
- 任一受影响结论算不出来时整批失败：不留半截清单（失败只写审计），现行基准不推进，旧版排班（最近一次 PUBLISHED 批次，见 `previous_published`）保持不变。
- 从旧结论追证据：`GET /api/baseline-fixes/history/leg/{业务键}` 或 `.../history/connection/{转机键}`。
- 批次清单：`GET /api/baseline-fixes`（含 `baseline_versions`、`baseline_history` 与失败审计）。
