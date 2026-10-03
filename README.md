# AI 商业项目助理

V1 单租户 Web 应用。当前进度：**TASK-000 工程骨架**（只有骨架，无任何业务功能）。

技术选型与业务决策已冻结，见 [`docs/DECISIONS.md`](docs/DECISIONS.md) —— 改动前先读。

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
│   │   ├── api/        路由层（routes/health.py 等）
│   │   ├── core/       配置（config.py，全部读环境变量）
│   │   ├── db/         引擎与会话（session.py）、声明式基类（base.py）
│   │   ├── models/     ORM 模型（D11 的 7 表，TASK-001 起落地）
│   │   ├── schemas/    Pydantic 模型（含 D4 的 EvidenceValue 契约）
│   │   └── main.py     应用入口
│   └── requirements.txt
├── frontend/           React + TS + Vite
├── migrations/         Alembic 迁移脚本
├── tests/              pytest
├── docs/DECISIONS.md   已冻结的决策记录
├── alembic.ini
├── pytest.ini
└── .env.example
```

## 快速开始

### 0. 准备

- Python 3.11+、Node 20+、本机 MySQL 8 已启动
- 建库（只需一次）：

```sql
CREATE DATABASE IF NOT EXISTS biz_assistant
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 1. 后端

```bash
cd D:/biz-assistant

# 建虚拟环境并装依赖（虚拟环境放项目内，不写 C 盘）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt

# 配置环境变量：复制样例后按需修改
cp .env.example .env

# 迁移
.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/alembic.exe current

# 起服务（默认 127.0.0.1:8000）
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --reload
```

- 健康检查：<http://127.0.0.1:8000/api/health>
- 接口文档：<http://127.0.0.1:8000/api/docs>

### 2. 测试

```bash
.venv/Scripts/python.exe -m pytest -v
```

### 3. 前端

```bash
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173
npm run build    # 产物在 frontend/dist
```

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

## 当前 TASK 的边界

TASK-000 只搭骨架：不导入数据、不建业务表、不做客户/沟通/AI 任何业务逻辑。
业务表从 TASK-001 起按 D11 逐步落地。数据库当前只有 `alembic_version` 一张 Alembic 自用表。
