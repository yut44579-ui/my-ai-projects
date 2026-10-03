# TASK-003 沟通记录真实验收证据（由 scripts/collect_evidence_task003.py 自动采集，未手工编辑）

采集时间：2026-10-03 22:31:31　后端：http://127.0.0.1:8103

★ 本 TASK 的验收本身就是「真发一条人工消息」，因此本文件里的写入是**真实写库**；
  写入目标只有两个证据客户（名字带「TASK-003 证据客户」），不碰其它数据。

### ① pytest 全量（只增不减）

```
D:\biz-assistant\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
FAILED tests/test_health.py::test_health_reports_real_db_connection - Asserti...
```

### ② TASK-003 专项测试（AC1~AC5）

```
======================== 12 passed, 1 warning in 1.49s ========================
```

### ③ 后端连通性（证据都采自这个进程）

```
GET /api/health → status=ok / db=biz_assistant_wt345 / connected=True
```

### ④ AC1 发一条人工消息 → 200 且时间线出现（带 evidence_ref）

```
POST /api/customers/5501/messages  → HTTP 200
{
  "id": 158,
  "customer_id": 5501,
  "sender_type": "HUMAN",
  "message_type": "CHAT",
  "content": "客户来电询问续约与后续服务安排（人工记录）",
  "source_type": "MANUAL",
  "evidence_ref": "customer_message:158",
  "ai_status": "NONE",
  "created_at": "2026-10-03T22:31:31"
}

GET /api/customers/5501/messages → HTTP 200
{
  "customer_id": 5501,
  "items": [
    {
      "id": 158,
      "customer_id": 5501,
      "sender_type": "HUMAN",
      "message_type": "CHAT",
      "content": "客户来电询问续约与后续服务安排（人工记录）",
      "source_type": "MANUAL",
      "evidence_ref": "customer_message:158",
      "ai_status": "NONE",
      "created_at": "2026-10-03T22:31:31"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": {
    "value": 1,
    "state": "VALID",
    "source_type": "SYSTEM",
    "evidence_ref": "customer_messages:count|customer_id=5501",
    "reason": null
  }
}
```

### ⑤ AC2 刷新后仍在（新连接重新读，非前端态）

```
第二次 GET（独立连接）items=1 条，total={"value": 1, "state": "VALID", "source_type": "SYSTEM", "evidence_ref": "customer_messages:count|customer_id=5501", "reason": null}
最后一条：{"id": 158, "customer_id": 5501, "sender_type": "HUMAN", "message_type": "CHAT", "content": "客户来电询问续约与后续服务安排（人工记录）", "source_type": "MANUAL", "evidence_ref": "customer_message:158", "ai_status": "NONE", "created_at": "2026-10-03T22:31:31"}
```

### ⑥ AC3 写入 sender_type=CUSTOMER → 400 + 明确错误码

```
POST /api/customers/5501/messages (sender_type=CUSTOMER) → HTTP 400
{
  "error": "customer_sender_forbidden",
  "message": "站内没有真实客户渠道，禁止写入 sender_type=CUSTOMER（D9 禁止伪造客户消息）",
  "allowed": [
    "HUMAN",
    "SYSTEM"
  ]
}
```

### ⑦ AC4 无消息客户 → 时间线 NO_DATA 空态（value=null，界面无数字）

```
GET /api/customers/5502/messages → HTTP 200
{
  "customer_id": 5502,
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer_messages:count|customer_id=5502",
    "reason": "暂无沟通记录"
  }
}

GET /api/customers/5502 → conversations
{
  "value": null,
  "state": "NO_DATA",
  "source_type": "SYSTEM",
  "evidence_ref": "customer_messages:count|customer_id=5502",
  "reason": "暂无沟通记录（TASK-003 已接入 customer_messages，该客户尚无消息）"
}
```

### ⑧ AC5 该客户消息条数走 EvidenceValue（前端不许自己数）

```
GET /api/customers/5501 → conversations
{
  "value": 1,
  "state": "VALID",
  "source_type": "SYSTEM",
  "evidence_ref": "customer_messages:count|customer_id=5501",
  "reason": null
}

GET /api/customers/5501/messages → total
{
  "value": 1,
  "state": "VALID",
  "source_type": "SYSTEM",
  "evidence_ref": "customer_messages:count|customer_id=5501",
  "reason": null
}
```

### ⑨ 时间线分页 + 时间正序（total 不受分页影响）

```
GET /api/customers/5501/messages?page=1&page_size=1 → HTTP 200
items=[158]（最早一条）
total={"value": 1, "state": "VALID", "source_type": "SYSTEM", "evidence_ref": "customer_messages:count|customer_id=5501", "reason": null}
最末一条 id=158（升序成立）
```

### ⑩ 对不存在的客户 → 404（GET / POST 都是）

```
GET  /api/customers/999999999/messages → HTTP 404
POST /api/customers/999999999/messages → HTTP 404
```

### ⑪ 直接查库：最近 8 行 + CUSTOMER 行数必须为 0（D9）

```
select ... from customer_messages order by id desc limit 8
(158, 5501, 'HUMAN', 'CHAT', 'MANUAL', 'customer_message:158', 'NONE', datetime.datetime(2026, 10, 3, 22, 31, 31))
(129, 3289, 'HUMAN', 'CHAT', 'MANUAL', 'customer_message:129', 'NONE', datetime.datetime(2026, 10, 3, 22, 30, 16))
(100, 3289, 'HUMAN', 'CHAT', 'MANUAL', 'customer_message:100', 'NONE', datetime.datetime(2026, 10, 3, 22, 28, 39))
(99, 3289, 'HUMAN', 'CHAT', 'MANUAL', 'customer_message:99', 'NONE', datetime.datetime(2026, 10, 3, 22, 22, 55))

select count(*) from customer_messages where sender_type='CUSTOMER' → 0

show columns from customer_messages → ['id', 'customer_id', 'sender_type', 'message_type', 'content', 'source_type', 'evidence_ref', 'ai_status', 'created_at']
```

### ⑫ 页面真实发出的接口调用（浏览器抓包，docs/evidence/task003-api-calls.json）

```
200  http://127.0.0.1:5183/api/customers/stats?
200  http://127.0.0.1:5183/api/customers/3289/timeline
200  http://127.0.0.1:5183/api/customers/3289
200  http://127.0.0.1:5183/api/customers/3289/timeline
200  http://127.0.0.1:5183/api/customers/3289/messages?page_size=100
200  http://127.0.0.1:5183/api/customers/3289/messages?page_size=100
200  http://127.0.0.1:5183/api/customers/3289/messages
200  http://127.0.0.1:5183/api/customers/3289/timeline
200  http://127.0.0.1:5183/api/customers/3289
200  http://127.0.0.1:5183/api/customers/3289/timeline
200  http://127.0.0.1:5183/api/customers/3289
200  http://127.0.0.1:5183/api/customers/3289/timeline
200  http://127.0.0.1:5183/api/customers/3289/messages?page_size=100
200  http://127.0.0.1:5183/api/customers/3289/messages?page_size=100
```

### ⑬ 页面截图（真实浏览器，见 docs/screenshots/）

```
task003-message-after-reload.png
task003-message-empty.png
task003-message-sent.png

· task003-message-empty          无消息客户：沟通记录显示「暂无沟通记录」，界面无数字
· task003-message-sent           真敲字真点发送 → 时间线出现「人工」气泡 + evidence_ref
· task003-message-after-reload   刷新页面（Page.navigate 重新加载）后消息仍在 = 真落库
```
