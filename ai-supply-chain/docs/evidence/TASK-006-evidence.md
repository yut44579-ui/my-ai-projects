# TASK-006 证据（客户状态机 + 事件留痕）

- worktree：`D:\biz-assistant-wt6`　分支：`task006`　基线：`af90376`
- 数据库：`biz_assistant_wt6`（本 worktree 独立库，非主库）
- 所有请求都是对**真实运行**的服务 `http://127.0.0.1:8016` 发的真实 HTTP。

> 说明：本机权限策略禁用了 `curl`，因此实际发出的是等价的 httpx 请求
> （采集脚本：`scripts/task006_evidence.py`，每条都会打印等价 curl 命令）。
> 客户数据不是编造的——先走 `/api/imports/preview` + `/commit` 真实导入
> `tests/fixtures/customers_sample_TEST_clean.csv`，再在这批真实客户上做状态流转。

---

## 1. 迁移：真实输出 + 库结构

```
$ .venv/Scripts/python.exe -m alembic upgrade head
INFO  [alembic.runtime.migration] Context impl MySQLImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 978f01c47fe5, init baseline (no business tables)
INFO  [alembic.runtime.migration] Running upgrade 978f01c47fe5 -> 36e74cad14a0, add customers and import_batches
INFO  [alembic.runtime.migration] Running upgrade 36e74cad14a0 -> 6b87e62da680, add customer lifecycle_status and customer_events

$ .venv/Scripts/python.exe -m alembic current
6b87e62da680 (head)

$ .venv/Scripts/python.exe -m alembic history
36e74cad14a0 -> 6b87e62da680 (head), add customer lifecycle_status and customer_events
978f01c47fe5 -> 36e74cad14a0, add customers and import_batches
<base> -> 978f01c47fe5, init baseline (no business tables)
```

`down_revision = 36e74cad14a0` —— 接在当时的 head 上。

### SHOW TABLES

```
alembic_version
customer_events   ← 新增
customers
import_batches
```

### DESCRIBE customers（新增最后一行）

```
id             | bigint          | NO  | PRI | NULL | auto_increment
name           | varchar(128)    | NO  |     | NULL |
company_name   | varchar(255)    | YES |     | NULL |
phone          | varchar(32)     | YES |     | NULL |
email          | varchar(255)    | YES |     | NULL |
region         | varchar(128)    | YES |     | NULL |
note           | text            | YES |     | NULL |
batch_id       | bigint          | YES | MUL | NULL |
source_type    | enum('REAL','TEST','MANUAL')      | NO  |     | NULL |
evidence_ref   | varchar(128)    | YES |     | NULL |
dedupe_state   | enum('CLEAN','PENDING_REVIEW','MERGED') | NO |  | NULL |
first_seen_at  | datetime(6)     | NO  |     | now() |
last_seen_at   | datetime(6)     | NO  |     | now() |
created_at     | datetime        | NO  |     | now() |
updated_at     | datetime        | NO  |     | now() |
lifecycle_status | enum('NEW','CONTACTED','REPLIED','ENGAGED','QUOTED','WON','LOST') | NO | | NEW |
```

### SHOW CREATE TABLE customer_events

```sql
CREATE TABLE `customer_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `customer_id` bigint NOT NULL COMMENT '所属客户；客户删除则其事件一并清除（V1 无删除接口）',
  `event_type` enum('STATUS_CHANGED','MESSAGE_SENT','HANDOVER','NOTE') NOT NULL,
  `from_status` enum('NEW','CONTACTED','REPLIED','ENGAGED','QUOTED','WON','LOST') DEFAULT NULL,
  `to_status` enum('NEW','CONTACTED','REPLIED','ENGAGED','QUOTED','WON','LOST') DEFAULT NULL,
  `actor_type` enum('HUMAN','AI','SYSTEM') NOT NULL,
  `source_type` enum('MANUAL','IMPORT','SYSTEM') NOT NULL,
  `evidence_ref` varchar(128) DEFAULT NULL COMMENT '证据锚点 = customer_event:{id}，代码生成',
  `metadata_json` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT (now()) COMMENT '微秒精度，保证同秒内可排序',
  PRIMARY KEY (`id`),
  KEY `ix_customer_events_customer_id_created_at` (`customer_id`,`created_at`),
  CONSTRAINT `customer_events_ibfk_1` FOREIGN KEY (`customer_id`)
    REFERENCES `customers` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

索引：`(customer_id, created_at)` 组合索引。最左前缀即 `customer_id`，
所以规格里「customer_id 索引」与「索引：customer_id + created_at」由**这一个**索引同时满足，
不再单独建一个重复的单列索引。`created_at` 用 `datetime(6)`，保证同一秒内连改三次也能正确排序。

### 迁移可逆性（一次性 scratch 库验证，不碰本库数据）

```
alembic upgrade head          -> OK
alembic downgrade 36e74cad14a0 -> OK
alembic upgrade head          -> OK
alembic downgrade 36e74cad14a0 -> OK
alembic upgrade head          -> OK
```

> 采集过程中发现并修掉一个真问题：autogenerate 生成的 downgrade 里先
> `DROP INDEX ix_customer_events_customer_id_created_at`，而该索引是外键唯一的支撑索引，
> MySQL 直接拒绝（`1553 Cannot drop index: needed in a foreign key constraint`）。
> 已改为先 `drop_table`（外键与索引随表一起消失）。

---

## 2. 改状态（成功 / AI 被拒 403 / 非法值 400）

### ① 人工改状态 NEW → CONTACTED —— HTTP 200

```bash
curl -s -X POST http://127.0.0.1:8016/api/customers/3849/status \
  -H "Content-Type: application/json" \
  -d '{"to_status":"CONTACTED","actor_type":"HUMAN","note":"首次电话触达"}'
```

```json
{
  "customer_id": 3849,
  "lifecycle_status": "CONTACTED",
  "changed": true,
  "event": {
    "id": 82,
    "customer_id": 3849,
    "event_type": "STATUS_CHANGED",
    "from_status": "NEW",
    "to_status": "CONTACTED",
    "actor_type": "HUMAN",
    "source_type": "MANUAL",
    "evidence_ref": "customer_event:82",
    "metadata_json": { "note": "首次电话触达" },
    "created_at": "2026-10-03T22:11:49"
  },
  "message": "状态已由 NEW 变更为 CONTACTED，事件已留痕"
}
```

主表确实被改了，事件确实落库了（后续 §3 的事件流可见 event id=82）。
`source_type=MANUAL`（人工变更），`evidence_ref=customer_event:82` 由代码生成。

### ② AI 改状态 —— HTTP 403，且不留事件

```bash
curl -s -X POST http://127.0.0.1:8016/api/customers/3849/status \
  -H "Content-Type: application/json" -d '{"to_status":"WON","actor_type":"AI"}'
```

```json
{
  "detail": "AI 不得自行变更客户状态（D7：AI 安全拦截在代码层）。客户状态只能由人工发起变更（actor_type=HUMAN），请转人工处理。"
}
```

**没有产生事件**：下一节的事件流里这张客户只有 3 条事件，全部是 HUMAN 的，没有任何 WON。
（另外也覆盖了「请求头 `X-Actor-Type: AI`」这一路，同样 403、同样不留痕，见 `tests/test_customer_status.py::test_ac3b`。）

### ③ 非法状态值 —— HTTP 400

```bash
curl -s -X POST http://127.0.0.1:8016/api/customers/3849/status \
  -H "Content-Type: application/json" \
  -d '{"to_status":"PENDING_WHATEVER","actor_type":"HUMAN"}'
```

```json
{
  "detail": "非法状态值 'PENDING_WHATEVER'：只能是 NEW / CONTACTED / REPLIED / ENGAGED / QUOTED / WON / LOST"
}
```

> 刻意让 `to_status` / `actor_type` 以字符串收进 Pydantic，再由路由层解析并抛 400 ——
> 否则 FastAPI 会对枚举校验失败直接返回 422，不满足 AC4 的 400 要求。

---

## 3. 连续三次改状态后的完整事件流（AC2）

先后改成 `CONTACTED` → `REPLIED` → `ENGAGED`（中间穿插了上面的 AI 403 与非法 400 请求）。

```bash
curl -s http://127.0.0.1:8016/api/customers/3849/events
```

```json
{
  "items": [
    { "id": 84, "event_type": "STATUS_CHANGED", "from_status": "REPLIED",  "to_status": "ENGAGED",
      "actor_type": "HUMAN", "source_type": "MANUAL", "evidence_ref": "customer_event:84",
      "metadata_json": null, "created_at": "2026-10-03T22:11:49" },
    { "id": 83, "event_type": "STATUS_CHANGED", "from_status": "CONTACTED", "to_status": "REPLIED",
      "actor_type": "HUMAN", "source_type": "MANUAL", "evidence_ref": "customer_event:83",
      "metadata_json": null, "created_at": "2026-10-03T22:11:49" },
    { "id": 82, "event_type": "STATUS_CHANGED", "from_status": "NEW",    "to_status": "CONTACTED",
      "actor_type": "HUMAN", "source_type": "MANUAL", "evidence_ref": "customer_event:82",
      "metadata_json": { "note": "首次电话触达" }, "created_at": "2026-10-03T22:11:49" }
  ],
  "page": 1,
  "page_size": 20,
  "total": { "value": 3, "state": "VALID", "source_type": "SYSTEM",
             "evidence_ref": "customer:3849:events", "reason": null }
}
```

- 三条历史齐全、按时间**倒序**（84 → 83 → 82）。
- 三条 `created_at` 落在**同一秒**——正因为 `created_at` 是 `datetime(6)` 且排序带 `id DESC` 兜底，
  顺序依然稳定正确。
- 时间顺序上首尾相接（NEW→CONTACTED→REPLIED→ENGAGED），历史没有被覆盖。
- 事件流里**没有** AI 的 WON 事件，也没有非法值事件 → AC3 / AC4 的「无副作用」得到交叉验证。

---

## 4. 空态：没有事件历史的客户（AC5）

```bash
curl -s http://127.0.0.1:8016/api/customers/3848/events
```

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": {
    "value": null,
    "state": "NO_DATA",
    "source_type": "SYSTEM",
    "evidence_ref": "customer:3848:events",
    "reason": "该客户暂无任何事件记录"
  }
}
```

`value` 是 `null` 而**不是** `0`，`state` 是 `NO_DATA` 并带 `reason` —— 没有拿 0 或假事件顶。

---

## 5. 客户详情补上 lifecycle_status

```bash
curl -s http://127.0.0.1:8016/api/customers/3849
```

```json
{
  "id": 3849, "name": "郑霞", "company_name": "南京金陵机械有限公司",
  "phone": "13800138008", "email": "zhengxia@example.com", "region": "南京", "note": "年框客户",
  "batch_id": 99, "source_type": "TEST", "evidence_ref": "import_batch:99",
  "dedupe_state": "CLEAN",
  "lifecycle_status": "ENGAGED",
  "first_seen_at": "2026-10-03T22:11:49.135045",
  "last_seen_at": "2026-10-03T22:11:49.135045",
  "created_at": "2026-10-03T22:11:49", "updated_at": "2026-10-03T22:11:49"
}
```

新增 `lifecycle_status`，其余字段语义未变（`source_type` / `dedupe_state` / `evidence_ref` 等原样）。

---

## 6. 全量 pytest

```
$ .venv/Scripts/python.exe -m pytest
..............................F....................                      [100%]
FAILED tests/test_health.py::test_health_reports_real_db_connection
1 failed, 50 passed in 12.47s
```

唯一失败的 `test_health_reports_real_db_connection` **与本 TASK 无关，且在改动前就失败**：
它把库名硬编码成主库 `biz_assistant`，而本 worktree 的库按任务要求是 `biz_assistant_wt6`。

```
E       AssertionError: assert 'biz_assistant_wt6' == 'biz_assistant'
tests\test_health.py:23: AssertionError
```

按硬边界「不改已有测试断言使其通过」，**没有动它**。剔除这一条环境耦合用例后：

```
$ .venv/Scripts/python.exe -m pytest --deselect tests/test_health.py::test_health_reports_real_db_connection
50 passed, 1 deselected in 7.37s
```

改动前后测试数：**38 → 50**（只增不减，新增 `tests/test_customer_status.py` 12 条）。

新增用例覆盖：AC1、AC2、AC3（body 与请求头两条路径）、AC4（状态与 actor 两种非法值）、
AC5（NO_DATA + 不存在客户 404）、同值不写事件、详情字段、列表筛选、分页倒序。
