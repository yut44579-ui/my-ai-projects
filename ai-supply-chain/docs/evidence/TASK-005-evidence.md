# TASK-005 验收证据（人工接管三态）

来源脚本：`scripts/collect_evidence_task005.py`（API_BASE=http://127.0.0.1:8025）

### ① 健康检查（独立库）

```
{
  "connected": true,
  "database": "biz_assistant_wt5",
  "dialect": "mysql",
  "server_version": "8.0.42",
  "error": null
}
```

### ② 迁移结果（列 / 表 / 索引 / 版本）

```
customers.handover_state:
  handover_state ENUM nullable=False default="'AUTO'"
customer_handover_events:
  id BIGINT nullable=False
  customer_id BIGINT nullable=False
  event_type ENUM nullable=False
  from_state ENUM nullable=True
  to_state ENUM nullable=False
  actor_type ENUM nullable=False
  source_type ENUM nullable=False
  reason TEXT COLLATE "utf8mb4_unicode_ci" nullable=True
  metadata_json JSON nullable=True
  evidence_ref VARCHAR(128) COLLATE "utf8mb4_unicode_ci" nullable=False
  created_at DATETIME nullable=False
indexes: ix_customer_handover_events_customer_created, ix_customer_handover_events_customer_id
alembic head: 3ccb69d4e8a0
```

### ③ 初始 handover（客户 595，空态 NO_DATA）

```
HTTP 200
{
  "customer_id": 595,
  "state": "AUTO",
  "ai_auto_reply_allowed": true,
  "events": [],
  "event_count": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:595:handover_events",
    "reason": "无数据"
  },
  "since": null,
  "updated_at": "2026-10-03T22:19:41"
}
```

### ④ 闸门判定敏感 → HUMAN_REQUIRED（服务层调用 + 详情可见）

```
set_human_required: changed=True state=HUMAN_REQUIRED
事件: customer_handover_event:80
GET /api/customers/595 的 handover_state = HUMAN_REQUIRED
```

### ④b 列表可见（AC1）

```
GET /api/customers item[595].handover_state = HUMAN_REQUIRED
```

### ⑤ 人工接管 → HUMAN_ACTIVE（写事件 actor_type=HUMAN）

```
HTTP 200
{
  "customer_id": 595,
  "state": "HUMAN_ACTIVE",
  "ai_auto_reply_allowed": false,
  "changed": true,
  "message": "客户 595：HUMAN_REQUIRED → HUMAN_ACTIVE",
  "event": {
    "id": 81,
    "event_type": "TAKEN_OVER",
    "from_state": "HUMAN_REQUIRED",
    "to_state": "HUMAN_ACTIVE",
    "actor_type": "HUMAN",
    "source_type": "MANUAL",
    "reason": "我来处理这个报价",
    "metadata_json": null,
    "evidence_ref": "customer_handover_event:81",
    "created_at": "2026-10-03T22:19:41"
  }
}
```

### ⑥ 交回 AI → AUTO（写事件）

```
HTTP 200
{
  "customer_id": 595,
  "state": "AUTO",
  "ai_auto_reply_allowed": true,
  "changed": true,
  "message": "客户 595：HUMAN_ACTIVE → AUTO",
  "event": {
    "id": 82,
    "event_type": "RESUMED",
    "from_state": "HUMAN_ACTIVE",
    "to_state": "AUTO",
    "actor_type": "HUMAN",
    "source_type": "MANUAL",
    "reason": "处理完了，交回 AI",
    "metadata_json": null,
    "evidence_ref": "customer_handover_event:82",
    "created_at": "2026-10-03T22:19:41"
  }
}
```

### ⑦ 完整变更历史（AC5，客户 595）

```
HTTP 200
{
  "customer_id": 595,
  "state": "AUTO",
  "ai_auto_reply_allowed": true,
  "events": [
    {
      "id": 80,
      "event_type": "HUMAN_REQUIRED",
      "from_state": "AUTO",
      "to_state": "HUMAN_REQUIRED",
      "actor_type": "SYSTEM",
      "source_type": "SYSTEM",
      "reason": "命中敏感：报价承诺",
      "metadata_json": {
        "rule": "QUOTE_PROMISE",
        "matched": "已经给你 8 折"
      },
      "evidence_ref": "customer_handover_event:80",
      "created_at": "2026-10-03T22:19:41"
    },
    {
      "id": 81,
      "event_type": "TAKEN_OVER",
      "from_state": "HUMAN_REQUIRED",
      "to_state": "HUMAN_ACTIVE",
      "actor_type": "HUMAN",
      "source_type": "MANUAL",
      "reason": "我来处理这个报价",
      "metadata_json": null,
      "evidence_ref": "customer_handover_event:81",
      "created_at": "2026-10-03T22:19:41"
    },
    {
      "id": 82,
      "event_type": "RESUMED",
      "from_state": "HUMAN_ACTIVE",
      "to_state": "AUTO",
      "actor_type": "HUMAN",
      "source_type": "MANUAL",
      "reason": "处理完了，交回 AI",
      "metadata_json": null,
      "evidence_ref": "customer_handover_event:82",
      "created_at": "2026-10-03T22:19:41"
    }
  ],
  "event_count": {
    "value": 3,
    "state": "VALID",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:595:handover_events",
    "reason": null
  },
  "since": "2026-10-03T22:19:41",
  "updated_at": "2026-10-03T22:19:41"
}
```

### ⑧ 硬边界：AI 身份改状态 → 403，且不产生事件（D7/D8）

```
HTTP 403
{
  "error": "ai_actor_forbidden",
  "message": "AI 不允许修改客户接管状态，只能由人工发起（见 D8）"
}
事件条数 before=3 after=3（应相同）
handover_state after=AUTO
```

### ⑨ 客户不存在 → 404

```
HTTP 404
{
  "error": "customer_not_found",
  "message": "客户 999999999 不存在"
}
```
