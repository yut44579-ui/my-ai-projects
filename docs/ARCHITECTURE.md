# ARCHITECTURE.md · sales-report-agent

## 系统分层

```
┌─────────────────────────────────────────────────────┐
│ 前端（原生 HTML+JS 单页，4 个页面）                  │
│  任务列表 / 新建任务 / 任务详情 / 产出记录            │
└──────────────────────┬──────────────────────────────┘
                       │ REST (JSON)
┌──────────────────────▼──────────────────────────────┐
│ app/routers/        API 层（薄：参数校验 + 调 service）│
├─────────────────────────────────────────────────────┤
│ app/services/       业务层                            │
│   task_service      任务管理（建/改/固化）            │
│   exec_service      执行编排（调度触发 → 调 engine）   │
│   ai_service        需求理解 / 方案生成 / 写解读      │
├─────────────────────────────────────────────────────┤
│ app/engine/         ★ 纯代码引擎（无 LLM，可单测）     │
│   loader  取数（Excel/MySQL → DataFrame）             │
│   compute 指标计算（周汇总/环比/TOP-N，pandas）        │
│   render  渲染 Excel（openpyxl 原地改保样式）          │
├─────────────────────────────────────────────────────┤
│ app/repositories/   数据访问（SQL 只在这一层）         │
├─────────────────────────────────────────────────────┤
│ app/ai/             LLM 调用（DeepSeek，OpenAI SDK）  │
└──────────────────────┬──────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
   MySQL(任务/记录/数据字典)      本地文件(样本/产出)
```

## 模块职责与边界（不可越界）

| 模块 | 负责 | 不负责 |
|---|---|---|
| routers | 参数校验、调 service、返回 | 业务逻辑 |
| services | 业务编排 | 直接写 SQL、直接调 LLM |
| **engine** | 取数/计算/渲染（**纯函数式，可单测**） | 调用 LLM、读写任务配置 |
| repositories | SQL 读写 | 业务判断 |
| ai | LLM 调用（提示词、解析返回） | 计算数字、写 Excel |

**关键边界**：`engine` 不依赖 `ai`——保证"数字计算"与"LLM 理解"解耦（数字可单独测）。

## 核心数据流

```
【建任务】
用户输入(文字+参考物) → ai_service 解析 → 方案草案 → 用户确认
  → services 固化成 task.spec_json → MySQL

【执行】
APScheduler 触发 / 手动 → exec_service
  → engine.loader 取数(DataFrame)
  → engine.compute 计算指标（确定性）
  → engine.render 写 Excel（参考物副本 + openpyxl）
  → ai_service 写解读（只读计算结果，不参与算）
  → 保存 outputs/ + 写 run 记录

【异常】
任一步失败 → 记录 run.status=failed + error 原因 → 不产出文档
```

## 技术栈

```
Python 3.11 · FastAPI · pandas 3.x · openpyxl · APScheduler
MySQL（元数据）· 本地文件（产出）· DeepSeek（LLM）· pytest
前端：原生 HTML + JS（无框架）
```

## 目录结构

```
sales-report-agent/
├── CLAUDE.md  PROJECT_SPEC.md(在 docs)  文档体系
├── docs/      PROJECT_SPEC / ARCHITECTURE / ROADMAP / TASKS / DECISIONS / PROGRESS
├── data/      Online Retail.xlsx（54万行真实数据）
├── app/
│   ├── main.py  config.py
│   ├── routers/ services/ repositories/ ai/ engine/ models/
├── templates/ 参考物样本（用户上传的报表）
├── outputs/   产出的报表文件
├── tests/     pytest + test_cases.json（50 条）
└── scripts/   run_eval.py（评估）
```

---
*创建：2026-09-21*
