# TASK-007 证据：自动汇报 + 下钻（后端骨架）

- 生成时间：2026-10-03 22:14:41
- 库：`biz_assistant_wt7`（本 worktree 独立库）
- 期间：2026-10-03 ~ 2026-10-03（Asia/Shanghai）
- 取证方式：FastAPI TestClient 打真实接口 + 真实 MySQL，非 mock；脚本跑完自动清理自建数据。

### 证据1：alembic upgrade head -> reports 表（库 = biz_assistant_wt7）

```
alembic upgrade head 输出：
  INFO  Running upgrade 36e74cad14a0 -> 728b1a3fcf7d, add reports

SHOW TABLES -> ['alembic_version', 'customers', 'import_batches', 'reports']

SHOW COLUMNS FROM reports:
  id                   bigint
  report_type          enum('DAILY','WEEKLY','MANUAL')
  period_start         date
  period_end           date
  timezone             varchar(64)
  content_json         json
  generated_at         datetime
  source_type          enum('IMPORT','MANUAL','SYSTEM')
  generated_by         varchar(64)
  excluded_test_count  bigint
```

### 证据3：REAL 为空 -> 两个指标都是 NO_DATA（不是 0）

```
生成前 REAL 客户数 = 0；导入 TEST 样本后 TEST 客户数 = 8

HTTP 201
{
  "id": 22,
  "report_type": "DAILY",
  "period_start": "2026-10-03",
  "period_end": "2026-10-03",
  "timezone": "Asia/Shanghai",
  "generated_at": "2026-10-03T22:14:41",
  "source_type": "IMPORT",
  "generated_by": "system",
  "excluded_test_count": 8,
  "metrics": {
    "customer_new": {
      "value": null,
      "state": "NO_DATA",
      "source_type": "IMPORT",
      "evidence_ref": "customers:source_type=REAL|metric=customer_new|period=2026-10-03..2026-10-03",
      "reason": "期间内无 REAL 来源客户数据（期间新增客户（REAL））"
    },
    "customer_total": {
      "value": null,
      "state": "NO_DATA",
      "source_type": "IMPORT",
      "evidence_ref": "customers:source_type=REAL|metric=customer_total",
      "reason": "期间内无 REAL 来源客户数据（客户总数（REAL））"
    }
  }
}
```

### 证据4：真写 3 条 REAL 客户后重新生成

```
插入的 REAL 客户 id = [1140, 1141, 1142]

HTTP 201
{
  "id": 23,
  "report_type": "WEEKLY",
  "period_start": "2026-10-03",
  "period_end": "2026-10-03",
  "timezone": "Asia/Shanghai",
  "generated_at": "2026-10-03T22:14:41",
  "source_type": "IMPORT",
  "generated_by": "system",
  "excluded_test_count": 8,
  "metrics": {
    "customer_new": {
      "value": 3,
      "state": "VALID",
      "source_type": "IMPORT",
      "evidence_ref": "customers:source_type=REAL|metric=customer_new|period=2026-10-03..2026-10-03",
      "reason": null
    },
    "customer_total": {
      "value": 3,
      "state": "VALID",
      "source_type": "IMPORT",
      "evidence_ref": "customers:source_type=REAL|metric=customer_total",
      "reason": null
    }
  }
}
```

### 证据5：下钻 customer_total（明细条数 == 报告数字）

```
报告里的 customer_total = 3
下钻 items 条数 = 3；下钻 total = {'value': 3, 'state': 'VALID', 'source_type': 'IMPORT', 'evidence_ref': 'customers:source_type=REAL|metric=customer_total', 'reason': None}
一致：True

HTTP 200
{
  "metric": "customer_total",
  "period_start": "2026-10-03",
  "period_end": "2026-10-03",
  "page": 1,
  "page_size": 20,
  "total": {
    "value": 3,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:source_type=REAL|metric=customer_total",
    "reason": null
  },
  "evidence_ref": "customers:source_type=REAL|metric=customer_total",
  "items": [
    {
      "id": 1140,
      "name": "证据客户1",
      "company_name": "真实数据科技有限公司",
      "phone": null,
      "email": null,
      "region": null,
      "note": null,
      "batch_id": null,
      "source_type": "REAL",
      "evidence_ref": null,
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T12:00:00",
      "last_seen_at": "2026-10-03T12:00:00",
      "created_at": "2026-10-03T22:14:41",
      "updated_at": "2026-10-03T22:14:41"
    },
    {
      "id": 1141,
      "name": "证据客户2",
      "company_name": "真实数据科技有限公司",
      "phone": null,
      "email": null,
      "region": null,
      "note": null,
      "batch_id": null,
      "source_type": "REAL",
      "evidence_ref": null,
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T12:00:00",
      "last_seen_at": "2026-10-03T12:00:00",
      "created_at": "2026-10-03T22:14:41",
      "updated_at": "2026-10-03T22:14:41"
    },
    {
      "id": 1142,
      "name": "证据客户3",
      "company_name": "真实数据科技有限公司",
      "phone": null,
      "email": null,
      "region": null,
      "note": null,
      "batch_id": null,
      "source_type": "REAL",
      "evidence_ref": null,
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T12:00:00",
      "last_seen_at": "2026-10-03T12:00:00",
      "created_at": "2026-10-03T22:14:41",
      "updated_at": "2026-10-03T22:14:41"
    }
  ]
}
```

### 证据6：下钻 customer_new

```
HTTP 200
{
  "metric": "customer_new",
  "period_start": "2026-10-03",
  "period_end": "2026-10-03",
  "page": 1,
  "page_size": 20,
  "total": {
    "value": 3,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:source_type=REAL|metric=customer_new|period=2026-10-03..2026-10-03",
    "reason": null
  },
  "evidence_ref": "customers:source_type=REAL|metric=customer_new|period=2026-10-03..2026-10-03",
  "items": [
    {
      "id": 1140,
      "name": "证据客户1",
      "company_name": "真实数据科技有限公司",
      "phone": null,
      "email": null,
      "region": null,
      "note": null,
      "batch_id": null,
      "source_type": "REAL",
      "evidence_ref": null,
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T12:00:00",
      "last_seen_at": "2026-10-03T12:00:00",
      "created_at": "2026-10-03T22:14:41",
      "updated_at": "2026-10-03T22:14:41"
    },
    {
      "id": 1141,
      "name": "证据客户2",
      "company_name": "真实数据科技有限公司",
      "phone": null,
      "email": null,
      "region": null,
      "note": null,
      "batch_id": null,
      "source_type": "REAL",
      "evidence_ref": null,
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T12:00:00",
      "last_seen_at": "2026-10-03T12:00:00",
      "created_at": "2026-10-03T22:14:41",
      "updated_at": "2026-10-03T22:14:41"
    },
    {
      "id": 1142,
      "name": "证据客户3",
      "company_name": "真实数据科技有限公司",
      "phone": null,
      "email": null,
      "region": null,
      "note": null,
      "batch_id": null,
      "source_type": "REAL",
      "evidence_ref": null,
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T12:00:00",
      "last_seen_at": "2026-10-03T12:00:00",
      "created_at": "2026-10-03T22:14:41",
      "updated_at": "2026-10-03T22:14:41"
    }
  ]
}
```

### 证据7：metric 不在白名单 -> 400

```
HTTP 400
{
  "detail": "metric 只能是 customer_total / customer_new，收到：'revenue'"
}
```

### 证据8：快照不变 —— 删掉 1 条 REAL 客户

```
删前 REAL 数 = 3，删后 REAL 数 = 2（已真删 1140）

[打开旧报告 23 · 删除前]
  customer_total = {'value': 3, 'state': 'VALID', 'source_type': 'IMPORT', 'evidence_ref': 'customers:source_type=REAL|metric=customer_total', 'reason': None}

[打开旧报告 23 · 删除后]
  customer_total = {'value': 3, 'state': 'VALID', 'source_type': 'IMPORT', 'evidence_ref': 'customers:source_type=REAL|metric=customer_total', 'reason': None}

→ 旧报告数字不变：True

[重新生成新报告 · 快照才是 2]
  new report id = 24
  customer_total = {'value': 2, 'state': 'VALID', 'source_type': 'IMPORT', 'evidence_ref': 'customers:source_type=REAL|metric=customer_total', 'reason': None}
```

### 证据9：GET /api/reports 历史列表（分页）

```
HTTP 200
{
  "items": [
    {
      "id": 24,
      "report_type": "MANUAL",
      "period_start": "2026-10-03",
      "period_end": "2026-10-03",
      "timezone": "Asia/Shanghai",
      "generated_at": "2026-10-03T22:14:41",
      "source_type": "IMPORT",
      "generated_by": "system",
      "excluded_test_count": 8
    },
    {
      "id": 23,
      "report_type": "WEEKLY",
      "period_start": "2026-10-03",
      "period_end": "2026-10-03",
      "timezone": "Asia/Shanghai",
      "generated_at": "2026-10-03T22:14:41",
      "source_type": "IMPORT",
      "generated_by": "system",
      "excluded_test_count": 8
    },
    {
      "id": 22,
      "report_type": "DAILY",
      "period_start": "2026-10-03",
      "period_end": "2026-10-03",
      "timezone": "Asia/Shanghai",
      "generated_at": "2026-10-03T22:14:41",
      "source_type": "IMPORT",
      "generated_by": "system",
      "excluded_test_count": 8
    }
  ],
  "page": 1,
  "page_size": 3,
  "total": {
    "value": 3,
    "state": "VALID",
    "source_type": "SYSTEM",
    "evidence_ref": "reports:count",
    "reason": null
  }
}
```

### 证据10：GET /api/reports/{id} 报告详情

```
HTTP 200
{
  "id": 23,
  "report_type": "WEEKLY",
  "period_start": "2026-10-03",
  "period_end": "2026-10-03",
  "timezone": "Asia/Shanghai",
  "generated_at": "2026-10-03T22:14:41",
  "source_type": "IMPORT",
  "generated_by": "system",
  "excluded_test_count": 8,
  "metrics": {
    "customer_new": {
      "value": 3,
      "state": "VALID",
      "source_type": "IMPORT",
      "evidence_ref": "customers:source_type=REAL|metric=customer_new|period=2026-10-03..2026-10-03",
      "reason": null
    },
    "customer_total": {
      "value": 3,
      "state": "VALID",
      "source_type": "IMPORT",
      "evidence_ref": "customers:source_type=REAL|metric=customer_total",
      "reason": null
    }
  }
}
```

### 证据11：pytest（AC7，只增不减）

```
$ .venv/Scripts/python.exe -m pytest tests/test_reports_api.py -v
tests/test_reports_api.py::test_ac2_no_real_data_yields_no_data_not_zero PASSED
tests/test_reports_api.py::test_ac6_unknown_metric_returns_400 PASSED
tests/test_reports_api.py::test_ac1_ac3_ac4_real_customers_count_and_drilldown_match PASSED
tests/test_reports_api.py::test_ac5_snapshot_frozen_after_delete PASSED
tests/test_reports_api.py::test_list_and_detail PASSED
5 passed in 1.35s

$ .venv/Scripts/python.exe -m pytest          # 全量
..........................................                               [100%]
43 passed, 1 deselected, 1 warning in 7.53s
```

★ 唯一被 deselect 的是 `tests/test_health.py::test_health_reports_real_db_connection`：
该测试把库名硬编码断言为 `biz_assistant`，而本 worktree 按 TASK-007 环境说明
用的是独立库 `biz_assistant_wt7`（.env 已指向它）。失败纯粹由环境差异引起，
与本 TASK 改动无关（未改动 health/config/该测试文件，见 git diff）；
按硬边界「不改已有测试断言使其通过」，此处不改它，仅如实标注。

### 证据12：本 TASK 提交

```
6bc8abb  feat(TASK-007): 自动汇报 + 下钻（后端骨架：reports 表 / 四个接口 / 快照与口径）
```
