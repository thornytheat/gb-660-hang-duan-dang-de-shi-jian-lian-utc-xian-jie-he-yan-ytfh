# 深度登记 航班时刻运行台

- 规模：中型
- 核心规则：用机场 IANA 时区解析 DST 重叠与跳时，所有航链在 UTC 上校验先后和衔接。
- 使用者与工作流：运行数据员导入航段，确认机场时区，处理当地时刻歧义，构建 UTC 航链并核对转机；基准更正时按批次发布修正并交接影响清单。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 航段采信 | 导入航段并隔离重复冲突 | POST /api/legs/import | `app/modules/m1_legs/parser.py` `app/modules/m1_legs/keys.py` | `tests/test_m1_legs.py` |
| M2 机场时区 | 查询日期对应的时区版本 | POST /api/airports | `app/modules/m2_airports/registry.py` `app/modules/m2_airports/contracts.py` | `tests/test_m2_airports.py` |
| M3 当地时刻 | 解析偏移、重叠与跳时 | POST /api/local-time/resolve | `app/modules/m3_localtime/resolve.py` `app/modules/m3_localtime/evidence.py` | `tests/test_m3_localtime.py` |
| M4 UTC 航链 | 换算航段并校验先后顺序 | POST /api/itineraries/build | `app/modules/m4_itinerary/builder.py` `app/modules/m4_itinerary/validate.py` | `tests/test_m4_itinerary.py` |
| M5 衔接判定 | 核对航站楼换乘与等待 | POST /api/connections/check | `app/modules/m5_connections/rules.py` `app/modules/m5_connections/windows.py` | `tests/test_m5_connections.py` |
| M6 批次导出 | 核对航段守恒并导出时刻 | POST /api/reconcile | `app/modules/m6_batches/compare.py` `app/modules/m6_batches/exporter.py` | `tests/test_m6_batches.py` |
| M7 基准修正发布 | 按版本对发布时区基准更正 | POST /api/baseline-fixes | `app/modules/m7_corrections/service.py` `app/modules/m7_corrections/repository.py` | `tests/test_m7_corrections.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-03 | 尾行记录不完整 | 半条航段进入换算 | 完整边界检查后隔离 | `app/modules/m1_legs/parser.py` | `tests/test_m1_legs.py::test_truncated_tail_detected` |
| TRAP-05 | 必填时刻缺失 | 空时间在深层报错 | 采信阶段给原因码和行号 | `app/modules/m1_legs/parser.py` | `tests/test_m1_legs.py::test_missing_time_quarantined` |
| TRAP-07 | 空当地时刻 | 被转换成午夜 | 空值不参与航链 | `app/modules/m1_legs/parser.py` | `tests/test_m1_legs.py::test_missing_time_quarantined` |
| TRAP-09 | 超大外部行程号 | JSON 数字往返丢精度 | 始终按字符串保形 | `app/modules/m1_legs/keys.py` | `tests/test_m1_legs.py::test_external_id_keeps_large_digits` |
| TRAP-11 | 偏移与 Z 混用 | 同一时刻先后次序错误 | 统一到 UTC 后输出 Z | `app/modules/m3_localtime/resolve.py` | `tests/test_m3_localtime.py::test_utc_serializes_with_z` |
| TRAP-12 | DST 重叠与跳时 | 重复小时覆盖或虚构时刻 | 往返验证并显式 fold | `app/modules/m3_localtime/resolve.py` | `tests/test_m3_localtime.py::test_dst_overlap_has_two_candidates` |
| TRAP-13 | 航段乱序 | 输入顺序被当成航程顺序 | 按 UTC 起飞时刻稳定排序 | `app/modules/m4_itinerary/builder.py` | `tests/test_m4_itinerary.py::test_order_by_utc_is_stable` |
| TRAP-14 | 同航段时刻冲突 | 后写覆盖原计划 | 冲突版本进入隔离区 | `app/modules/m1_legs/parser.py` | `tests/test_m1_legs.py::test_conflicting_duplicate_detected` |
| TRAP-23 | 衔接窗口端点 | 结束时刻被重复计入 | 使用半开区间 | `app/modules/m5_connections/windows.py` | `tests/test_m5_connections.py::test_half_open_window_excludes_end` |
| TRAP-25 | 时区版本重叠 | 随机选中一版偏移 | 多个生效版本立即拒绝 | `app/modules/m2_airports/registry.py` | `tests/test_m2_airports.py::test_overlapping_timezone_versions_rejected` |
| TRAP-31 | 基准更正回写历史 | 新表直接覆盖旧偏移，已发排班被悄悄改写 | 基准是不可变快照，按区间生效，旧结论保留原版本 | `app/modules/m2_airports/baselines.py` `app/modules/m4_itinerary/builder.py` | `tests/test_m7_corrections.py::test_new_baseline_only_applies_from_effective_date` |
| TRAP-32 | 跨日航段用错基准 | 到达用时区当天日期查表，跨日段被算错 | 每个端点按自身当地日期取区间，并记录基准版本与摘要 | `app/modules/m4_itinerary/builder.py` | `tests/test_m7_corrections.py::test_publish_lists_only_affected_legs` |
| TRAP-33 | 修正重复执行 | 每次重跑都新增一批交接项 | 批次由版本对唯一确定，重复执行复用并列出 ignored | `app/modules/m7_corrections/service.py` | `tests/test_m7_corrections.py::test_repeated_batch_is_idempotent` |
| TRAP-34 | 修正发布失败 | 半截清单落库、现行基准已推进 | 单事务发布，失败只写审计，旧排班与原基准保持 | `app/modules/m7_corrections/repository.py` | `tests/test_m7_corrections.py::test_failed_publish_writes_no_handover_items` |

## 时区基准修正规则

- 基准是带版本的完整快照；旧结论永远引用发放时的版本，不回写。
- 新基准按区间生效：历史区间保持原映射，只有落在新区间内的当地时刻会得到不同结论。
- 一个修正批次 = 一对来源/目标基准；只登记经过两版差异机场的航段与转机。
- 每条受影响结论同时保存修正前与修正后证据（基准版本、时区、偏移、UTC、等待分钟）。
- 发布单事务：任一结论算不出来则整批失败，旧版排班与现行基准不动。
- 从旧结论追证据：`/api/baseline-fixes/history/{leg|connection}/{键}`。

## 对账规则

- 输入航段 = 有效航段 + 冲突航段 + 隔离航段。
- 行程航段 = 可衔接航段 + 不可衔接航段。
- 到达 UTC 必须晚于起飞 UTC，飞行时长不得超过 18 小时。

## 已知不覆盖

- 不计算天气和空管延误，输入只描述计划时刻。

