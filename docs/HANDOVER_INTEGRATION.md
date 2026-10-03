# TASK-005 人工接管三态 —— 接口与整合说明

本条并行线（worktree `D:\biz-assistant-wt5`，分支 `task005`）**只做后端**：
接管状态机 + 接口 + 事件留痕，并给 TASK-004（AI 策略闸门）留一个明确的调用口。
前端接管 UI、以及与本条线之外的整合，按本文接线。

状态机（D8，不做工作流引擎）：

```
AUTO ──闸门判定敏感──▸ HUMAN_REQUIRED ──人工接管──▸ HUMAN_ACTIVE
 ▲                          │                          │
 └──────── 交回 AI ─────────┴──────── 交回 AI ─────────┘
```

合法迁移共 5 条（写死在 `backend/app/services/handover.py::TRANSITIONS`）：

| from | to（允许） |
|---|---|
| `AUTO` | `HUMAN_REQUIRED`（闸门）、`HUMAN_ACTIVE`（人工主动接管） |
| `HUMAN_REQUIRED` | `HUMAN_ACTIVE`（接管）、`AUTO`（交回） |
| `HUMAN_ACTIVE` | `AUTO`（交回） |

同值（no-op）不写事件 —— 事件只记真实变化，历史不灌水。

---

## 一、接口（已实现，可直接 curl）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/customers/{id}/takeover` | 人工接管 → `HUMAN_ACTIVE`，写事件 `actor_type=HUMAN` |
| `POST` | `/api/customers/{id}/resume` | 交回 AI → `AUTO`，写事件 |
| `GET`  | `/api/customers/{id}/handover` | 当前状态 + 完整变更历史 |

两个 POST 都可带可选 body `{"note": "..."}`（人工备注，写进事件）；不带也能调。

```bash
# 闸门判定敏感（服务层调用，见第二节）后：
curl -X POST http://127.0.0.1:8000/api/customers/1/takeover -H "Content-Type: application/json" -d "{\"note\":\"我来处理\"}"
curl -X POST http://127.0.0.1:8000/api/customers/1/resume   -H "Content-Type: application/json" -d "{\"note\":\"处理完了\"}"
curl      http://127.0.0.1:8000/api/customers/1/handover
```

`GET /handover` 返回：

```jsonc
{
  "customer_id": 1,
  "state": "HUMAN_ACTIVE",
  "ai_auto_reply_allowed": false,          // ★ 唯一判据，见第三节
  "events": [ /* 时间正序，每次变化一条 */ ],
  "event_count": { "value": 3, "state": "VALID", "source_type": "SYSTEM",
                   "evidence_ref": "customer:1:handover_events" },
  "since": "2026-10-03T22:20:11",
  "updated_at": "2026-10-03T22:20:11"
}
```

★ `event_count` 是业务数字，走 `EvidenceValue`：**从未变更过 → `state=NO_DATA, value=null`**，
不是 `0`（0 会被误读成"变更过零次"）。

错误码（统一 `{"error": <code>, "message": ...}`）：

| code | HTTP | 场景 |
|---|---|---|
| `customer_not_found` | 404 | 客户不存在 |
| `ai_actor_forbidden` | 403 | body 传 `actor_type=AI`（D7/D8：AI 不许改客户接管状态），且**不产生事件** |
| `invalid_transition` | 400 | 非法状态迁移 |
| —（pydantic） | 422 | `actor_type` 不在枚举内 |

客户列表 / 详情（`GET /api/customers`、`GET /api/customers/{id}`）已带上 `handover_state` 字段，
供列表页打标记、详情页显示状态条（AC1）。

---

## 二、★ 给 TASK-004（AI 策略闸门）的接线口

闸门判定命中敏感时，**在调 LLM 之前**调用：

```python
from app.services.handover import set_human_required, ai_auto_reply_allowed

# 命中敏感 → 转人工（幂等：已经是 HUMAN_REQUIRED/HUMAN_ACTIVE 时不再写事件）
set_human_required(
    db, customer_id,
    reason="命中敏感：报价",          # 必填，人话原因，写进事件
    rule="QUOTE_PROMISE",            # 可选：命中的规则名 → metadata_json
    matched="已经给你 8 折",          # 可选：命中原文片段 → metadata_json
)

# 调 LLM 前的前置判断（AC3：接管期间敏感内容仍不自动回复）
if not ai_auto_reply_allowed(customer.handover_state):
    ...  # 返回「AI 不会自动回复敏感内容，请人工处理」，★ 不调 LLM
```

签名：`set_human_required(db: Session, customer_id: int, reason: str, *, rule=None, matched=None) -> HandoverChange`

- 发起方固定记为 `actor_type=SYSTEM`、`source_type=SYSTEM`（这是代码层判定，不是人工也不是 AI 自作主张）。
- `HandoverChange.changed=False` 表示没改状态（幂等命中），闸门无需特殊处理。

---

## 三、AC3 的唯一判据：`ai_auto_reply_allowed(state)`

```python
def ai_auto_reply_allowed(state) -> bool:   # 只有 AUTO 返回 True
```

★ **别在别处自己写 `state != AUTO`** —— 闸门、前端、测试都统一走这个函数，
语义才不会各处走样。接口返回里也带着同名字段 `ai_auto_reply_allowed`，前端直接用即可。

---

## 四、前端怎么接（本 TASK 不改 `frontend/`，留到整合阶段）

> 硬边界：本条线**没有**改 `frontend/` 下任何文件。以下是接线说明。

1. **详情页顶部状态条**（放在客户详情页标题下方）：

   | `handover_state` | 文案 | 建议样式 |
   |---|---|---|
   | `AUTO` | AI 自动 | 常规/绿色 |
   | `HUMAN_REQUIRED` | ⚠ 需要人工处理 | 警告/橙色 |
   | `HUMAN_ACTIVE` | 人工处理中 | 醒目/蓝色 |

   数据来自 `GET /api/customers/{id}`（已含 `handover_state`）。

2. **接管 / 交回按钮**：
   - `AUTO` / `HUMAN_REQUIRED` → 显示「人工接管」→ `POST /takeover`
   - `HUMAN_ACTIVE` → 显示「交回 AI」→ `POST /resume`
   - 调用成功后用返回体里的 `state` / `ai_auto_reply_allowed` 刷新，不要前端自己推。

3. **明确提示**（AC3 的界面落地）：当 `ai_auto_reply_allowed === false` 时，
   在输入框/回复区静态提示「AI 不会自动回复敏感内容，请人工处理」。

4. **列表页醒目标记**（AC1）：`GET /api/customers` 的每个 item 都带 `handover_state`，
   `!== "AUTO"` 的那一行加角标（如「需人工」）。

5. **变更历史**（可选）：`GET /handover` 的 `events` 可直接渲染成时间线
   （`event_type` / `from_state`→`to_state` / `actor_type` / `reason` / `created_at`）。

---

## 五、与 TASK-006 的整合缝（重要）

TASK-006（另一条并行线）拥有**通用**事件表 `customer_events`（生命周期状态机 +
通用事件留痕）。两条线都从基线 `36e74cad14a0` 出发，为避免迁移互相冲突，
本 TASK 的事件落**专属表** `customer_handover_events`：

```
customers
  + handover_state            ENUM(AUTO|HUMAN_REQUIRED|HUMAN_ACTIVE)   -- 本 TASK
  + lifecycle_status          ENUM(NEW|CONTACTED|...)                  -- TASK-006，非本 TASK

customer_handover_events      -- 本 TASK（接管留痕）
  id / customer_id(FK CASCADE, 索引) / event_type(HUMAN_REQUIRED|TAKEN_OVER|RESUMED) /
  from_state / to_state / actor_type(HUMAN|AI|SYSTEM) / source_type / reason /
  metadata_json / evidence_ref("customer_handover_event:{id}") / created_at
  索引：(customer_id, created_at)

customer_events               -- TASK-006（通用事件），本 TASK 未创建、未使用
```

**整合方式（二选一，由整合方定）：**

- **A. 保留两表**（推荐，最省事）：接管留痕留在 `customer_handover_events`，
  两表各司其职，零迁移改动。
- **B. 并入通用表**：`customer_handover_events` 与 `customer_events` 列结构刻意同形，
  合并 = 一次 `INSERT..SELECT` + 枚举映射
  （`from_state/to_state` → `from_status/to_status`，`event_type` → `HANDOVER` 或沿用原值）。

本表事件已包含 `actor_type=HUMAN` 与 `source_type=MANUAL`，满足 TASK-005 规格里
「接管写事件 actor_type=HUMAN」的要求。

---

## 六、复现证据（三态 + 事件查询 + pytest）

```bash
# 迁移
.venv/Scripts/python.exe -m alembic upgrade head

# 全量测试（本条线新增 17 条：tests/test_handover.py）
.venv/Scripts/python.exe -m pytest

# 三态演示见 docs/evidence/TASK-005-evidence.md
```
