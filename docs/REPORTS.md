# 汇报（TASK-007 后端骨架）

本文件说明汇报这块的**语义契约**与四个接口怎么用。前端汇报页照此对接即可；
本 TASK 只做后端，前端代码一行未动。

## 一句话：前端汇报页怎么接这四个接口

> 前端汇报页只做三件事 —— **生成**（`POST /api/reports/generate` 选类型与期间）、
> **列表**（`GET /api/reports` 翻历史）、**看图下钻**（`GET /api/reports/{id}` 拿数字后，
> 点任一指标调 `GET /api/reports/drilldown?metric=<同一个 key>&period_start=&period_end=` 展示明细行）；
> **所有数字直接显示 `value`，前端不得自己算**，`state=NO_DATA` 时显示「暂无数据」而不是 `0`，
> 并展示接口返回的 `excluded_test_count`（本期排除了多少条测试数据）。

## 四个接口

| 接口 | 用途 | 关键返回 |
| --- | --- | --- |
| `POST /api/reports/generate` | 生成一份**快照** | 201 + 报告详情（含 `metrics`、`excluded_test_count`） |
| `GET /api/reports?page=&page_size=` | 历史报告列表（分页） | `items[]`、`total`(EvidenceValue) |
| `GET /api/reports/{id}` | 某份报告的全部指标 | `metrics` 每项含 `value/state/source_type/evidence_ref` |
| `GET /api/reports/drilldown?metric=&period_start=&period_end=&page=&page_size=` | 指标下钻明细 | `items[]`（客户行）+ `total`(EvidenceValue) + `evidence_ref` |

`metric` 白名单只有两个（其它返回 400）：
`customer_total`（REAL 客户总数）、`customer_new`（期间新增，按 `first_seen_at`）。

## 三条必须先懂的语义

1. **快照冻结（D6）**：`generate` 那一刻把数字与 `evidence_ref` 就地写进 `content_json`。
   之后客户被改/被删，**重新打开旧报告数字不会变**；要看最新数字就重新生成一份。
2. **默认只统计 REAL（IMPORT_RULES §六）**：TEST 数据可以进库跑链路，但绝不进业务数字；
   被排除的 TEST 条数记进 `excluded_test_count`，在报告里可见（不是偷偷丢掉）。
3. **`NO_DATA` 不是 `0`**：本期该口径下没有任何 REAL 客户时，指标是
   `{"value": null, "state": "NO_DATA", "reason": ...}`。前端要区分「暂无数据」与「真的是 0」。

## 下钻口径与报告完全一致

生成与下钻共用 `app/services/reports.py:metric_conditions()` 这一个口径函数，
所以 `GET /api/reports/drilldown` 的 `total.value` 必然等于报告里同名指标的 `value`
（`tests/test_reports_api.py` 有断言锁住这一点）。下钻时传的 `period_start/period_end`
要与生成该报告时一致，否则会是另一个期间的口径。

## 时区

固定 `Asia/Shanghai`（D6），`timezone` 字段随每份报告存下来。期间按「含 `period_end` 当天」
的半开区间 `[period_start 00:00, period_end+1 天 00:00)` 计算。
