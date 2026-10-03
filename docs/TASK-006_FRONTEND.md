# 前端如何接 TASK-006（客户状态机 + 事件留痕）

> 本文只描述**已有后端接口怎么用**。TASK-006 不改前端代码，前端由另一条线负责。
> 所有业务数字必须来自后端并带 `source_type` / `evidence_ref`，前端**不得自己算**。

## 0. 变更一览

| 位置 | 变化 |
| --- | --- |
| `GET /api/customers/{id}` | 响应**新增字段** `lifecycle_status`，其余字段语义不变 |
| `GET /api/customers` | 响应新增可选查询参数 `lifecycle_status`（筛选） |
| `POST /api/customers/{id}/status` | **新接口**：变更状态（只有人工能调） |
| `GET /api/customers/{id}/events` | **新接口**：事件流（时间倒序，分页） |

`lifecycle_status` 取值（7 个，顺序即建议的展示顺序）：

```
NEW | CONTACTED | REPLIED | ENGAGED | QUOTED | WON | LOST
```

枚举为 `string`，**不是**数字。未知取值不要在前端兜底成某个状态，应原样展示或报错。

## 1. 列表页：状态列 + 状态筛选

**状态列**：`GET /api/customers` 的每条 item 都带 `lifecycle_status`，直接渲染即可。
建议把 `WON` / `LOST` 做成终结态样式（正/负色），其余为进行中。

**状态筛选**：加查询参数即可，后端分页与筛选是配套的，**不要在前端做客户端过滤**
（客户端过滤会把「本页筛掉」误当成「全库没有」，和分页一起用必然算错）。

```js
// 列表请求
const params = { page, page_size, q, source_type, lifecycle_status }; // lifecycle_status 可省略
const { items, total, test_count } = await fetch(
  `/api/customers?${new URLSearchParams(clean(params))}`
).then(r => r.json());
```

- 筛选值非法（不在 7 个取值内）后端返回 **400**，前端应把它当参数错误处理，不要静默忽略。
- `total` 是 `EvidenceValue`（业务数字），照 TASK-001 的老规矩渲染：`VALID` 显示数值，
  `NO_DATA` 显示「—」，`ERROR` 显示原因。**不要**把 `NO_DATA` 渲染成 0。

## 2. 详情页：状态条 + 变更入口

`GET /api/customers/{id}` 里读 `lifecycle_status` 作为当前状态。

**变更状态**（唯一的写接口）：

```http
POST /api/customers/{id}/status
Content-Type: application/json

{ "to_status": "CONTACTED", "actor_type": "HUMAN", "note": "首次电话触达" }
```

成功（HTTP 200）：

```json
{
  "customer_id": 3849,
  "lifecycle_status": "CONTACTED",
  "changed": true,
  "event": { "id": 82, "event_type": "STATUS_CHANGED", "from_status": "NEW",
             "to_status": "CONTACTED", "actor_type": "HUMAN", "source_type": "MANUAL",
             "evidence_ref": "customer_event:82", "metadata_json": {"note": "首次电话触达"},
             "created_at": "2026-10-03T22:11:49" },
  "message": "状态已由 NEW 变更为 CONTACTED，事件已留痕"
}
```

前端必须处理的几种返回：

| 场景 | HTTP | 前端应做什么 |
| --- | --- | --- |
| 变更成功 | 200 `changed=true` | 更新状态条；把 `event` 直接插到事件流顶部（**不用重新拉全量**） |
| 选了和当前相同的状态 | 200 `changed=false`, `event=null` | 提示「状态未变化」，**不要**插事件（否则界面上会出现一条不存在的历史） |
| `to_status` 非法 | 400 | 这是前端传错了，按参数错误提示 |
| `actor_type` 非法 | 400 | 同上 |
| `actor_type=AI` | 403 | **正常 UI 不该出现**（按钮永远传 HUMAN）。出现了说明调用方接错，按错误提示 |
| 客户不存在 | 404 | 提示客户已不存在并返回列表 |

> ⚠️ **AI 绝不能自己改客户状态**。`actor_type` 只能传 `"HUMAN"`（V1 前端只有人工变更入口）。
> 传 `AI` 会被后端 403 拒绝且**不产生任何事件**——这是后端代码层的安全闸门（D7），
> 不是靠前端不写就能绕过的。前端不要试图构造 `AI` 请求。

## 3. 详情页：事件流区块

```http
GET /api/customers/{id}/events?page=1&page_size=20
```

响应：

```json
{
  "items": [
    { "id": 84, "event_type": "STATUS_CHANGED", "from_status": "REPLIED", "to_status": "ENGAGED",
      "actor_type": "HUMAN", "source_type": "MANUAL", "evidence_ref": "customer_event:84",
      "metadata_json": null, "created_at": "2026-10-03T22:11:49" }
  ],
  "page": 1, "page_size": 20,
  "total": { "value": 3, "state": "VALID", "source_type": "SYSTEM",
             "evidence_ref": "customer:3849:events", "reason": null }
}
```

**顺序**：后端已按 `created_at DESC, id DESC` 排好，**前端不要再自己排序**。

**空态（重要）**：该客户没有任何事件时返回

```json
{ "items": [], "total": { "value": null, "state": "NO_DATA", "source_type": "SYSTEM",
                          "evidence_ref": "customer:3848:events", "reason": "该客户暂无任何事件记录" } }
```

前端必须据此显示「暂无事件记录」，**不要**显示「0 条」——`NO_DATA` 和真实为 0 是两回事，
这里是「确实没有数据」。

**渲染建议**：

- `event_type` 决定图标/标题：`STATUS_CHANGED` 状态流转、`MESSAGE_SENT` 消息、
  `HANDOVER` 接管、`NOTE` 备注（V1 只有 `STATUS_CHANGED` 会被本 TASK 写入，其余由后续 TASK 产生，
  前端按枚举渲染即可，遇到未知类型原样显示不要崩）。
- `actor_type`：`HUMAN` 显示为人、`AI` 显示为 AI 标记、`SYSTEM` 显示为系统。
- 状态型事件展示成 `from_status → to_status`（`from_status` 为 `null` 时只显示 `to_status`）。
- `metadata_json.note` 有值就作为事件描述显示。
- 每条事件都可展示 `evidence_ref`（如 `customer_event:84`）作为「可追溯」证据锚点。

## 4. 不该做的事

- 不要在前端计算任何业务数字（事件条数、客户数一律用后端的 `EvidenceValue`）。
- 不要把 `NO_DATA` 渲染成 `0`。
- 不要在前端做状态筛选（用 `lifecycle_status` 查询参数）。
- 不要传 `actor_type=AI`。
- 不要为「状态没变化」补一条事件。
