# AI 商业项目助理

V1 单租户 Web 应用。当前进度：**TASK-002 客户模块**（列表页指标卡 + 客户详情页，
在 TASK-001 的数据接入之上增强）。
TASK-000 的工程骨架（health / 迁移 / 空态前端）与 TASK-001 的导入链路全部保持原样。

技术选型与业务决策已冻结，见 [`docs/DECISIONS.md`](docs/DECISIONS.md) —— 改动前先读。
导入的完整规则（编码、映射、去重、失败语义、TEST 隔离）见 [`docs/IMPORT_RULES.md`](docs/IMPORT_RULES.md)。
汇报（报告快照 / 指标口径 / 下钻）的语义契约与四个接口的前端接法见 [`docs/REPORTS.md`](docs/REPORTS.md)。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11+ / FastAPI / SQLAlchemy 2.x / Alembic / PyMySQL / Pydantic |
| 前端 | React + TypeScript + Vite + Ant Design + React Router（状态只用本地 state + fetch） |
| 数据库 | MySQL 8，独立库 `biz_assistant` |

**明确不使用**：PostgreSQL / Redis / MinIO / Docker / Kafka / Celery / 向量库 / 工作流引擎 / 微服务；
Redux / Zustand / TanStack Query 等一切状态管理库。

## 目录结构

```
biz-assistant/
├── backend/            FastAPI 应用
│   ├── app/
│   │   ├── api/        路由层（routes/health.py、imports.py、customers.py）
│   │   ├── core/       配置（config.py，全部读环境变量）
│   │   ├── db/         引擎与会话（session.py）、声明式基类（base.py）
│   │   ├── models/     ORM 模型（customers、import_batches）
│   │   ├── schemas/    Pydantic 模型（含 D4 的 EvidenceValue 契约）
│   │   ├── services/   与框架无关的业务逻辑（解析 / 映射 / 去重 / 导入编排）
│   │   └── main.py     应用入口
│   └── requirements.txt
├── frontend/           React + TS + Vite
│   └── src/
│       ├── pages/      CustomersPage（列表）、CustomerDetailPage（详情，TASK-002）
│       └── components/ EvidenceNumber（业务数字唯一渲染口）、StatCard、SourceTag、ImportModal
├── migrations/         Alembic 迁移脚本
├── scripts/            运维/证据脚本（cleanup_test_data.*、ui_screenshots*.mjs、collect_evidence*.py）
│   └── lib/            截图脚本共用的 CDP 客户端（cdp.mjs）
├── tests/              pytest
│   └── fixtures/       测试样本（★ 文件名带 TEST，导入时 source_type 必须是 TEST）
├── docs/DECISIONS.md     已冻结的决策记录
├── docs/IMPORT_RULES.md  导入规则（编码/映射/去重/失败语义/TEST 隔离）
├── docs/REPORTS.md       汇报契约（快照语义 / 指标口径 / 下钻 / 前端接法）
├── alembic.ini
├── pytest.ini
└── .env.example
```

## 首次运行（新人照着做就能通）

> ⚠️ **必须先建 `.env`，否则一定跑不起来。**
> 配置全部从 `.env` / 环境变量读取（源码里没有任何口令默认值），
> 少了 `.env` 就会连不上库：`Access denied for user 'root'@'localhost' (using password: NO)`，
> `/api/health` 会返回 `status=degraded`，`pytest` 与 `alembic` 也会直接报错。
> `.env` 已被 `.gitignore` 忽略，不会进版本库 —— 换台机器要重新建一次。

### 第 1 步：准备环境

- Python 3.11+、Node 20+、本机 MySQL 8 已启动

### 第 2 步：建库（只需一次）

```sql
CREATE DATABASE IF NOT EXISTS biz_assistant
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 第 3 步：建 `.env` 并填 5 个数据库值（关键，别跳过）

```bash
cd D:/biz-assistant
cp .env.example .env
```

打开 `.env`，确认/填写下面 5 行（本机 MySQL 就填这些）：

```ini
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=你的MySQL密码
DB_NAME=biz_assistant
```

`DB_NAME` 必须是 `biz_assistant`（改成别的库名会被 `config.py` 里的守卫拦下或连错库）。
其余项保持样例默认即可，其中 `TIMEZONE=Asia/Shanghai` 不要改（D6）。

### 第 4 步：装依赖、跑迁移

```bash
# 虚拟环境放项目内，不写 C 盘
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt

.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/alembic.exe current
```

### 第 5 步：起后端（默认 127.0.0.1:8000）

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --reload
```

`/api/health` 里 `"connected": true` 才算通了。

### 第 6 步：起前端

```bash
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173
```

- 健康检查：<http://127.0.0.1:8000/api/health>
- 接口文档：<http://127.0.0.1:8000/api/docs>

### 第 7 步：跑测试 / 构建前端

```bash
# 测试（在项目根目录）。测试会直连本机 MySQL，并只清理自己造的数据，可反复运行
.venv/Scripts/python.exe -m pytest -q

# 前端生产构建
cd frontend && npm run build      # 产物在 frontend/dist
```

> 导入相关的测试用 `tests/fixtures/` 里的样本文件跑真实链路。夹具文件产生的批次与客户
> 会在测试前后被自动清掉（按文件 sha256 匹配），属预期行为 —— 用样本文件做的演示数据
> 也会被一并清掉，重新导入即可。
>
> 另外：如果库里已经存在**与夹具同手机号/邮箱**的客户（例如用样本数据造的演示数据），
> 测试会先把这些行整行快照后**临时摘除**，测完再原样写回（id 不变、一行不删），
> 这样「导入 8 行 → 新建 8 行」才不会被误判成去重命中。兜底快照在
> `tmp/pytest-detached-customers.json`（测试正常结束时会自动删除）。

开发期 Vite 已把 `/api` 代理到 `127.0.0.1:8000`，无需处理跨域。

## 约定（写代码前必读）

1. **配置不硬编码**：所有连接串/口令从环境变量读（`backend/app/core/config.py`）。
   `alembic.ini` 里的 `sqlalchemy.url` 故意留空，由 `migrations/env.py` 注入。
2. **禁止连别的项目的库**：本服务只连 `biz_assistant`，`config.py` 里有守卫会直接报错。
3. **业务数字必须包成 `EvidenceValue`**（D4）：`VALID` / `NO_DATA` / `ERROR` 三态互斥，
   `0` 是合法值、绝不能用来表示"没有数据"；前端不得自己算业务数字。
4. **AI 安全拦截写在代码层**，不写在 Prompt 里（D7）。
5. **人工接管不走工作流引擎**，用 `AUTO → HUMAN_REQUIRED → HUMAN_ACTIVE` 状态机（D8）。
6. **不许造数**：页面上没数据就显示空态，禁止假数字、假图表、假列表。

## 环境变量

见 [`.env.example`](.env.example)。关键项：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 完整连接串，填了就优先用它 |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | 分项配置，未给 `DATABASE_URL` 时用它拼装 |
| `TIMEZONE` | 固定 `Asia/Shanghai`（D6），不要改 |
| `CORS_ORIGINS` | 允许的前端源，逗号分隔 |

`.env` 已被 `.gitignore` 忽略，**不要提交真实口令**。

## 数据接入（导入 CSV/XLSX）

### 怎么用

1. 打开 <http://127.0.0.1:5173/customers>，点右上「导入 Excel/CSV」。
2. 选文件 → 「解析预览」：页面会显示识别到的**编码**、**sheet**、**表头行号**、数据行数、
   每列的自动映射（`auto` / `unknown`）和**前 3 行样本**。
3. 在「映射到」下拉里确认/修改每列对应的字段（可选「忽略此列」，被忽略的列不会入库）→「确认映射并导入」。
4. 结果弹窗显示「新建 N / 去重命中 M / 跳过 K」+ 跳过原因 + 未映射列。
   出现跳过行时状态是 `PARTIAL`，弹窗用**警告色**并写明「部分导入：N 成功 / M 跳过」。

接口（`/api` 前缀）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/imports/preview` | 只解析、不入库 |
| POST | `/api/imports/commit` | 带确认后的映射表 + sha256 入库 |
| GET | `/api/imports` | 批次列表 |
| GET | `/api/imports/{id}` | 批次详情（跳过明细 + 客户 id 列表），V1 的"追溯"入口 |
| GET | `/api/customers` | 分页 + 关键词搜索 + `?source_type=` 过滤 |
| GET | `/api/customers/stats` | 列表页四个指标卡（口径与列表一致：`?q=` / `?source_type=`） |
| GET | `/api/customers/{id}` | 客户详情（基础字段 + 来源可追溯 + 状态占位 + 三个空区块） |
| GET | `/api/customers/{id}/timeline` | 客户事件流（V1 只有真实事件） |

限制：单文件 ≤ 10MB、数据行 ≤ 50,000；只接受 `.csv` `.xlsx` `.xlsm`。
同一份文件（sha256 相同）不允许重复导入。

### 先用测试样本跑通链路

库里暂时没有真实客户数据，先用带 TEST 标记的样本文件验证链路：

```bash
# 干净样本：8 行正常数据（UTF-8，表头是中文别名）
tests/fixtures/customers_sample_TEST_clean.csv
# 脏数据样本：GB18030 编码，覆盖 email/phone 都空、email 重复、缺姓名、
# 电话/邮箱格式错、整行为空、表头有多余列
tests/fixtures/customers_sample_TEST_messy.csv
```

导入时来源一律选 **TEST**（提交接口的 `source_type` 缺省就是 TEST）。
导入后客户列表顶部会出现黄色 banner「当前包含 N 条测试导入数据」，行内用 Tag 标出来源。

### 清理测试数据

```bash
# 等价的两条命令，任选一条（不需要 mysql 客户端的用后者）
mysql -u root -p biz_assistant < scripts/cleanup_test_data.sql
.venv/Scripts/python.exe scripts/cleanup_test_data.py            # 加 --dry-run 只统计不删
```

★ 汇报类查询（TASK-007 起）**必须默认过滤 `source_type='REAL'`**，
否则 TEST 数据会被算进业务数字（`GET /api/customers` 是唯一例外，它要显示 TEST 数据才能跑通链路）。

## 客户模块（TASK-002）

在 TASK-001 的导入链路之上增强，**只读**：本 TASK 一行数据都不写、一列都不加。

### 列表页（`/customers`）

- 顶部四个指标卡：**客户总数 / 测试数据 / 待人工裁决 / 本周新增**。
  数字全部由 `GET /api/customers/stats` 算好（口径 = 当前搜索词 + 来源筛选），
  前端只用 `EvidenceNumber` 渲染，自己一个都不算（D4）。
- ★ **`—` 与 `0` 是两回事**：口径内一条客户都没有 → 后端返回 `NO_DATA`，界面显示
  「— 暂无数据」；口径内有客户但计数为 0（例如"本周新增 0"）→ 返回 `VALID: 0`，界面显示 0。
- 点任意一行 → 进入该客户详情页。

### 客户详情页（`/customers/:id`）

- 头部：姓名 + 公司 + 来源 Tag + 去重状态 Tag + 返回列表。
- 基本信息：电话 / 邮箱 / 地区 / 备注 / 首次接触 / 最近命中。
- **来源与可追溯**：文件名、批次号、导入时间、`evidence_ref`
  （点 `evidence_ref` 直接打开该批次详情接口 `GET /api/imports/{batch_id}` —— TASK-001 的追溯入口）。
- 事件流：只列**真实存在**的事件（客户记录创建 / 来源导入 / 去重待裁决）。
  AI 对话、人工跟进尚未接入 → 显式写「待 TASK-003 接入」，**不编事件**。
- 状态：V1 没有生命周期状态（`customers` 表已按评审砍掉 `status` 列，本 TASK 没有加回来），
  显示显式占位「未开始跟进（V1 暂无流程）」，等 TASK-006 接入状态机。
- 三个区块（沟通记录 / 风险提醒 / 相关批次明细）在 V1 全是**明确空态**（`NO_DATA`）：
  没有假数据、假图表，也不用 `0` 冒充。

### 重采本 TASK 的证据

```bash
node scripts/ui_screenshots_task002.mjs            # 先截图 + 抓浏览器真实调用（需 8000/5173 都在跑）
.venv/Scripts/python.exe scripts/collect_evidence_task002.py
# 产出：docs/screenshots/task002-*.png、docs/evidence/TASK-002-evidence.md
```
## 人工接管三态（TASK-005）

D8：`AUTO → HUMAN_REQUIRED → HUMAN_ACTIVE`，不做工作流引擎。每次状态变化都留痕。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/customers/{id}/takeover` | 人工接管 → `HUMAN_ACTIVE`（写事件 `actor_type=HUMAN`） |
| `POST` | `/api/customers/{id}/resume` | 交回 AI → `AUTO`（写事件） |
| `GET`  | `/api/customers/{id}/handover` | 当前状态 + 变更历史（`event_count` 走 EvidenceValue） |

客户列表/详情已带 `handover_state` 字段。AI 闸门（TASK-004）判定敏感时调用
`app.services.handover.set_human_required(db, customer_id, reason, ...)`；
调 LLM 前统一用 `ai_auto_reply_allowed(state)` 判断能否自动回复。
接口与整合说明见 **docs/HANDOVER_INTEGRATION.md**。

## 当前 TASK 的边界

TASK-001 只做**数据接入**（CSV/XLSX → customers + import_batches）：
不接 PDF/Word/PPT/企业微信/邮件/CRM，不做客户编辑/删除/导出，不做撤销导入。
TASK-002 只在客户模块上做**只读增强**：不碰 AI 回复 / 沟通记录写入 / 人工接管 / 汇报，
不新增数据库列（尤其没有加回 `customers.status`），不引新依赖。
不做沟通记录 / AI 回复 / 汇报（后续 TASK）。

TASK-005（本 worktree，分支 `task005`）只做**后端**：人工接管状态机 + 接口 + 事件留痕，
并给 TASK-004 闸门留调用口（`set_human_required`）。**不改 `frontend/`**，
不做 TASK-003/004/006/007 的内容。数据库当前 4 张表：
`alembic_version`、`customers`、`import_batches`、`customer_handover_events`。
