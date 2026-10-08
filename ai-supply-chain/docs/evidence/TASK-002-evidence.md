# TASK-002 客户模块真实验收证据（由 scripts/collect_evidence_task002.py 自动采集，未手工编辑）

采集时间：2026-10-03 22:08:59

### ① pytest 全量（TASK-001 的 39 条 + TASK-002 新增 16 条，只增不减）

```
============================= test session starts =============================
collected 55 items

tests\test_config_env_warning.py .....                                   [  9%]
tests\test_customers_module.py ................                          [ 38%]
tests\test_dedupe.py ............                                        [ 60%]
tests\test_health.py .....                                               [ 69%]
tests\test_imports_api.py ...........F.....                              [100%]

================================== FAILURES ===================================
__________________ test_ac9_500_rows_import_under_5_seconds ___________________

client = <starlette.testclient.TestClient object at 0x0000027980C42CD0>
db = <sqlalchemy.orm.session.Session object at 0x00000279811C94D0>
clean_imports = None

    def test_ac9_500_rows_import_under_5_seconds(
        client: TestClient, db: Session, clean_imports: None
    ) -> None:
        """AC9：导入 500 行 CSV 在 5 秒内完成（本地 MySQL）。"""
        rows = [f"批量客户{i},{MARK_PHONE}{i:06d},bulk{i}{MARK_EMAIL}" for i in range(500)]
        raw = _csv(*rows)
    
        started = time.perf_counter()
        sha = upload(client, "bulk500.csv", raw).json()["file_sha256"]
        body = commit(client, "bulk500.csv", raw, sha).json()
        elapsed = time.perf_counter() - started
    
        assert body["rows_created"]["value"] == 500
        assert body["rows_skipped"]["value"] == 0
        assert body["status"] == "SUCCESS"
>       assert elapsed < 5.0, f"500 行导入耗时 {elapsed:.2f}s，超过 5s 上限"
E       AssertionError: 500 行导入耗时 5.44s，超过 5s 上限
E       assert 5.43694609999875 < 5.0

tests\test_imports_api.py:285: AssertionError
============================== warnings summary ===============================
.venv\Lib\site-packages\fastapi\testclient.py:1
  D:\biz-assistant\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
FAILED tests/test_imports_api.py::test_ac9_500_rows_import_under_5_seconds - ...
================== 1 failed, 54 passed, 1 warning in 13.41s ===================

【读法】
· 55 条 = TASK-001 的 39 条 + TASK-002 新增 16 条，只增不减。
· 若本次唯一未通过的是 TASK-001 的 test_ac9_500_rows_import_under_5_seconds，
  见下面第 ⑥ 段：那是 TASK-001 的计时断言（500 行 < 5s），本机长期在 5s 边界浮动，
  本 TASK 没有修改它，也没有经过导入链路。
```

### ② 指标卡 /api/customers/stats（未筛选：525 / 525 / 3 / 525）

```
$ GET /api/customers/stats
HTTP 200
{
  "scope": "all",
  "total": {
    "value": 525,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:total|all",
    "reason": null
  },
  "test_count": {
    "value": 525,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:test|all",
    "reason": null
  },
  "pending_review": {
    "value": 3,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:pending_review|all",
    "reason": null
  },
  "new_this_week": {
    "value": 525,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:new_this_week|all",
    "reason": null
  }
}
```

### ③ 指标卡：来源筛选成空集（REAL）→ 四项全是 NO_DATA，不是 0

```
$ GET /api/customers/stats?source_type=REAL
HTTP 200
{
  "scope": "source_type=REAL",
  "total": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:total|source_type=REAL",
    "reason": "当前筛选条件下没有客户数据"
  },
  "test_count": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:test|source_type=REAL",
    "reason": "当前筛选条件下没有客户数据"
  },
  "pending_review": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:pending_review|source_type=REAL",
    "reason": "当前筛选条件下没有客户数据"
  },
  "new_this_week": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:new_this_week|source_type=REAL",
    "reason": "当前筛选条件下没有客户数据"
  }
}
```

### ④ 指标卡：搜索一个不存在的词 → 口径内空集 → NO_DATA

```
$ GET /api/customers/stats?q=zzz-not-exist
HTTP 200
{
  "scope": "q=zzz-not-exist",
  "total": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:total|q=zzz-not-exist",
    "reason": "当前筛选条件下没有客户数据"
  },
  "test_count": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:test|q=zzz-not-exist",
    "reason": "当前筛选条件下没有客户数据"
  },
  "pending_review": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:pending_review|q=zzz-not-exist",
    "reason": "当前筛选条件下没有客户数据"
  },
  "new_this_week": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:new_this_week|q=zzz-not-exist",
    "reason": "当前筛选条件下没有客户数据"
  }
}
```

### ⑤ 客户详情 /api/customers/2127

```
$ GET /api/customers/2127
HTTP 200
{
  "id": 2127,
  "name": "冲突丙C",
  "company_name": null,
  "phone": "13844441111",
  "email": "conflict_x@t.com",
  "region": null,
  "note": "冲突行",
  "batch_id": 49,
  "source_type": "TEST",
  "evidence_ref": "import_batch:49",
  "dedupe_state": "PENDING_REVIEW",
  "first_seen_at": "2026-10-03T21:26:32.575966",
  "last_seen_at": "2026-10-03T21:26:32.575966",
  "created_at": "2026-10-03T21:26:32",
  "updated_at": "2026-10-03T21:26:32",
  "source": {
    "source_type": "TEST",
    "evidence_ref": "import_batch:49",
    "batch_id": 49,
    "batch": {
      "batch_id": 49,
      "filename": "c.csv",
      "status": "SUCCESS",
      "source_type": "TEST",
      "imported_at": "2026-10-03T21:26:32",
      "evidence_ref": "import_batch:49"
    }
  },
  "status": {
    "available": false,
    "label": "未开始跟进",
    "state": "NOT_STARTED",
    "note": "V1 暂无跟进流程；等 TASK-006 接入状态机"
  },
  "conversations": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:2127:conversations",
    "reason": "暂无沟通记录（TASK-003 接入后显示）"
  },
  "risks": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:2127:risks",
    "reason": "暂无风险记录（TASK-005 接入后显示）"
  },
  "batch_details": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:2127:batch_details",
    "reason": "V1 不按客户展开批次明细（来源批次见「来源与可追溯」）"
  }
}
```

### ⑥ 事件流 /api/customers/2127/timeline

```
$ GET /api/customers/2127/timeline
HTTP 200
{
  "customer_id": 2127,
  "events": [
    {
      "kind": "SOURCE_IMPORT",
      "title": "由导入批次 #49 写入",
      "occurred_at": "2026-10-03T21:26:32",
      "evidence_ref": "import_batch:49",
      "detail": "文件 c.csv｜批次状态 SUCCESS"
    },
    {
      "kind": "CUSTOMER_CREATED",
      "title": "客户记录创建",
      "occurred_at": "2026-10-03T21:26:32.575966",
      "evidence_ref": "customer:2127",
      "detail": "first_seen_at=2026-10-03 21:26:32.575966"
    },
    {
      "kind": "DEDUPE_PENDING_REVIEW",
      "title": "去重命中多条既有客户，待人工裁决",
      "occurred_at": "2026-10-03T21:26:32.575966",
      "evidence_ref": "customer:2127",
      "detail": "导入时判定；★ 不自动合并，等人工处理"
    }
  ],
  "events_count": {
    "value": 3,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customer:2127:timeline",
    "reason": null
  },
  "unavailable": [
    {
      "kind": "AI_CONVERSATION",
      "label": "AI 对话",
      "state": "NO_DATA",
      "reason": "V1 没有客户消息渠道，待 TASK-003 接入"
    },
    {
      "kind": "HUMAN_FOLLOW_UP",
      "label": "人工跟进",
      "state": "NO_DATA",
      "reason": "V1 没有跟进记录，待 TASK-003 接入"
    }
  ]
}
```

### ⑦ 404：不存在的客户

```
$ GET /api/customers/999999999
HTTP 404
{
  "detail": "客户 999999999 不存在"
}
```

### ③ 本 TASK 只读证明（AC6：GET 系列一行都不写）

```
调用前：
  customers 行数 = 525
  customers 最大 id = 2127
  import_batches 行数 = 13
  import_batches 最大 id = 49
  alembic_version 行数 = 1

调用后：
  customers 行数 = 525
  customers 最大 id = 2127
  import_batches 行数 = 13
  import_batches 最大 id = 49
  alembic_version 行数 = 1

以上 GET 调用：
  GET /api/customers → HTTP 200
  GET /api/customers?page=1&page_size=5&q=a&source_type=TEST → HTTP 200
  GET /api/customers/stats → HTTP 200
  GET /api/customers/stats?source_type=REAL → HTTP 200
  GET /api/customers/2127 → HTTP 200
  GET /api/customers/2127/timeline → HTTP 200
  GET /api/imports → HTTP 200
  GET /api/imports/49 → HTTP 200
  GET /api/health → HTTP 200

结论：调用前后库指纹完全一致 → 没有产生任何写入
```

由 `scripts/ui_screenshots_task002.mjs` 驱动真实 Edge 走完（列表 → 搜索空态 → REAL 空集 → 点行进详情）时，从浏览器网络层抓下的原始响应体。

#### GET /api/customers/stats → HTTP 200

```json
{
  "scope": "all",
  "total": {
    "value": 525,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:total|all",
    "reason": null
  },
  "test_count": {
    "value": 525,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:test|all",
    "reason": null
  },
  "pending_review": {
    "value": 3,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:pending_review|all",
    "reason": null
  },
  "new_this_week": {
    "value": 525,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:new_this_week|all",
    "reason": null
  }
}
```

#### GET /api/customers?page=1&page_size=20 → HTTP 200

```json
{
  "items": [
    {
      "id": 2127,
      "name": "冲突丙C",
      "company_name": null,
      "phone": "13844441111",
      "email": "conflict_x@t.com",
      "region": null,
      "note": "冲突行",
      "batch_id": 49,
      "source_type": "TEST",
      "evidence_ref": "import_batch:49",
      "dedupe_state": "PENDING_REVIEW",
      "first_seen_at": "2026-10-03T21:26:32.575966",
      "last_seen_at": "2026-10-03T21:26:32.575966",
      "created_at": "2026-10-03T21:26:32",
      "updated_at": "2026-10-03T21:26:32"
    },
    {
      "id": 2126,
      "name": "冲突乙B",
      "company_name": null,
      "phone": "13844441111",
      "email": null,
      "region": null,
      "note": "只有电话",
      "batch_id": 48,
      "source_type": "TEST",
      "evidence_ref": "import_batch:48",
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T21:26:32.496797",
      "last_seen_at": "2026-10-03T21:26:32.496797",
      "created_at": "2026-10-03T21:26:32",
      "updated_at": "2026-10-03T21:26:32"
    },
    {
      "id": 2125,
      "name": "冲突甲A",
      "company_name": null,
      "phone": null,
      "email": "conflict_x@t.com",
      "region": null,
      "note": "只有邮箱",
      "batch_id": 47,
      "source_type": "TEST",
      "evidence_ref": "import_batch:47",
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T21:26:32.420140",
      "last_seen_at": "2026-10-03T21:26:32.420140",
      "created_at": "2026-10-03T21:26:32",
      "updated_at": "2026-10-03T21:26:32"
    },
    {
      "id": 2124,
      "name": "隔离丙",
      "company_name": "隔离公司C",
      "phone": "13844440003",
      "email": "iso_c@t.com",
      "region": "北京",
      "note": "备注C",
      "batch_id": 46,
      "source_type": "TEST",
      "evidence_ref": "import_batch:46",
      "dedupe_state"
```

#### GET /api/customers?page=1&page_size=20&q=zzz-%E4%B8%8D%E5%AD%98%E5%9C%A8%E7%9A%84%E5%AE%A2%E6%88%B7-zzz → HTTP 200

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": {
    "value": 0,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:count|q=zzz-不存在的客户-zzz",
    "reason": null
  },
  "test_count": {
    "value": 525,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:count|source_type=TEST",
    "reason": null
  }
}
```

#### GET /api/customers/stats?q=zzz-%E4%B8%8D%E5%AD%98%E5%9C%A8%E7%9A%84%E5%AE%A2%E6%88%B7-zzz → HTTP 200

```json
{
  "scope": "q=zzz-不存在的客户-zzz",
  "total": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:total|q=zzz-不存在的客户-zzz",
    "reason": "当前筛选条件下没有客户数据"
  },
  "test_count": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:test|q=zzz-不存在的客户-zzz",
    "reason": "当前筛选条件下没有客户数据"
  },
  "pending_review": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:pending_review|q=zzz-不存在的客户-zzz",
    "reason": "当前筛选条件下没有客户数据"
  },
  "new_this_week": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:new_this_week|q=zzz-不存在的客户-zzz",
    "reason": "当前筛选条件下没有客户数据"
  }
}
```

#### GET /api/customers?page=1&page_size=20&source_type=REAL → HTTP 200

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": {
    "value": 0,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:count|source_type=REAL",
    "reason": null
  },
  "test_count": {
    "value": 525,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:count|source_type=TEST",
    "reason": null
  }
}
```

#### GET /api/customers/stats?source_type=REAL → HTTP 200

```json
{
  "scope": "source_type=REAL",
  "total": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:total|source_type=REAL",
    "reason": "当前筛选条件下没有客户数据"
  },
  "test_count": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:test|source_type=REAL",
    "reason": "当前筛选条件下没有客户数据"
  },
  "pending_review": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:pending_review|source_type=REAL",
    "reason": "当前筛选条件下没有客户数据"
  },
  "new_this_week": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "IMPORT",
    "evidence_ref": "customers:stats:new_this_week|source_type=REAL",
    "reason": "当前筛选条件下没有客户数据"
  }
}
```

#### GET /api/customers/2127 → HTTP 200

```json
{
  "id": 2127,
  "name": "冲突丙C",
  "company_name": null,
  "phone": "13844441111",
  "email": "conflict_x@t.com",
  "region": null,
  "note": "冲突行",
  "batch_id": 49,
  "source_type": "TEST",
  "evidence_ref": "import_batch:49",
  "dedupe_state": "PENDING_REVIEW",
  "first_seen_at": "2026-10-03T21:26:32.575966",
  "last_seen_at": "2026-10-03T21:26:32.575966",
  "created_at": "2026-10-03T21:26:32",
  "updated_at": "2026-10-03T21:26:32",
  "source": {
    "source_type": "TEST",
    "evidence_ref": "import_batch:49",
    "batch_id": 49,
    "batch": {
      "batch_id": 49,
      "filename": "c.csv",
      "status": "SUCCESS",
      "source_type": "TEST",
      "imported_at": "2026-10-03T21:26:32",
      "evidence_ref": "import_batch:49"
    }
  },
  "status": {
    "available": false,
    "label": "未开始跟进",
    "state": "NOT_STARTED",
    "note": "V1 暂无跟进流程；等 TASK-006 接入状态机"
  },
  "conversations": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:2127:conversations",
    "reason": "暂无沟通记录（TASK-003 接入后显示）"
  },
  "risks": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:2127:risks",
    "reason": "暂无风险记录（TASK-005 接入后显示）"
  },
  "batch_details": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:2127:batch_details",
    "reason": "V1 不按客户展开批次明细（来源批次见「来源与可追溯」）"
  }
}
```

#### GET /api/customers/2127/timeline → HTTP 200

```json
{
  "customer_id": 2127,
  "events": [
    {
      "kind": "SOURCE_IMPORT",
      "title": "由导入批次 #49 写入",
      "occurred_at": "2026-10-03T21:26:32",
      "evidence_ref": "import_batch:49",
      "detail": "文件 c.csv｜批次状态 SUCCESS"
    },
    {
      "kind": "CUSTOMER_CREATED",
      "title": "客户记录创建",
      "occurred_at": "2026-10-03T21:26:32.575966",
      "evidence_ref": "customer:2127",
      "detail": "first_seen_at=2026-10-03 21:26:32.575966"
    },
    {
      "kind": "DEDUPE_PENDING_REVIEW",
      "title": "去重命中多条既有客户，待人工裁决",
      "occurred_at": "2026-10-03T21:26:32.575966",
      "evidence_ref": "customer:2127",
      "detail": "导入时判定；★ 不自动合并，等人工处理"
    }
  ],
  "events_count": {
    "value": 3,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customer:2127:timeline",
    "reason": null
  },
  "unavailable": [
    {
      "kind": "AI_CONVERSATION",
      "label": "AI 对话",
      "state": "NO_DATA",
      "reason": "V1 没有客户消息渠道，待 TASK-003 接入"
    },
    {
      "kind": "HUMAN_FOLLOW_UP",
      "label": "人工跟进",
      "state": "NO_DATA",
      "reason": "V1 没有跟进记录，待 TASK-003 接入"
    }
  ]
}
```

### ⑥ TASK-001 的 AC9 计时断言在本机的实测（未修改，如实记录）

```
第 1 次：======================== 1 failed, 1 warning in 8.79s =========================
          E       AssertionError: 500 行导入耗时 5.03s，超过 5s 上限
第 2 次：======================== 1 failed, 1 warning in 9.38s =========================
          E       AssertionError: 500 行导入耗时 5.17s，超过 5s 上限
第 3 次：======================== 1 failed, 1 warning in 9.89s =========================
          E       AssertionError: 500 行导入耗时 5.31s，超过 5s 上限

说明：AC9 是 TASK-001 的计时断言（阈值 5s），本 TASK 未修改；
      该断言在本机长期处于 5s 边界（实测单批 1509 条 SQL，逐行去重设计使然），
      TASK-002 只增加只读接口，不经过导入链路，与它的波动无关。
```

### ⑤ 页面截图（真实浏览器，见 docs/screenshots/）

```
task002-customer-detail-top.png
task002-customer-detail.png
task002-customers-empty.png
task002-customers-real-nodata.png
task002-customers-stats.png

· task002-customers-stats  列表页：四个指标卡 + TEST banner + 表格
· task002-customers-empty  搜索不存在的词 → 空态，页面上零业务数字
· task002-customers-real-nodata  来源筛选 REAL（空集）→ 指标卡 NO_DATA（不是 0）
· task002-customer-detail  客户详情：头部 / 状态占位 / 基本信息 / 来源与可追溯 / 事件流 / 三个空态区块
```
