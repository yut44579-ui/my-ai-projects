# CLAUDE.md · sales-report-agent

> 本文件是 Claude Code 的项目上下文（零上下文启动时自动加载）。**动手前必读**。

## 项目是什么

**用户说人话 → AI 理解 → 出方案（人工确认）→ 固化成任务 → 定时自动取数、算数、渲染成 Excel 报表 + 文字解读。**

一句话：**把"每周手动做销售报表"变成"说一次，之后自动出"。**

- 目标用户：单用户（本人），不会写 SQL
- 输入：大白话（"做个上周销售汇总"）+ 参考物（已有 Excel 报表样本）
- 输出：保留参考物样式的 Excel + AI 写的一段解读/建议
- 完整需求见 `docs/requirements.md`

## 角色分工（严格遵守）

| 角色 | 谁 | 做什么 |
|---|---|---|
| 老板 | 用户 | 定方向、确认方案、验收 |
| 管家 | Hermes | 架构、需求文档、派单、验收、文档 |
| 程序员 | **Claude Code** | 写代码、跑测试、装依赖 |

## 铁律（违反即返工）

0. **开发前必读 SOP**：`D:\GPT_Project_Reviews\WORKFLOW.md`（Hermes 协作规范）+ `INDEX.md`
   + 本项目相关 `reviews/` `decisions/`。本项目的批准方案见 `docs/DECISIONS.md` 与 `docs/TASKS.md`。
   涉及架构/技术选型/核心功能/数据模型/依赖/方向变化 → **未经 ChatGPT 评审不得动工**。
   每个 TASK 必须有 Scope / Out of Scope / Acceptance Criteria；GATE-1..6 + 独立 Oracle 任一 FAIL 即 STOP。
   **禁止**：改测试让测试通过、mock 冒充、假数据/假下载、占位 TODO、顺手重构（范围外只记录）。

1. **数字一律由代码算**（pandas/SQL）——LLM 只负责：理解需求、写解读。**任何让 LLM 直接算数的实现都算错**。
2. **Excel 一律用 openpyxl 原地改**（在参考物副本上写），**不用 pandas 重建**——否则丢样式。
3. **首次理解 → 人工确认 → 固化成任务**——之后定时执行不再调用 LLM 做理解（保证产出稳定）。
4. **找不到数据必须提问**（列候选让用户选），**禁止瞎猜字段**。
5. **失败不产出错误文档**——数据源断/字段缺失就报错记录，宁可不产出。
6. **写代码一次只做一个文件**（用户要能读懂每个文件，面试要讲得出）。
7. **依赖清单必须精简**——用不到的库绝不加（尤其：不加 LangChain、不加向量数据库）。

## 技术栈（已定，不要更换）

```
后端    Python 3.11 + FastAPI（分层：routers / services / repositories）
数据    pandas 3.x（读 Excel/CSV）、openpyxl（写 Excel 保样式）
存储    MySQL（任务配置/执行记录） + 本地文件（产出 xlsx）
LLM     DeepSeek（OpenAI SDK 兼容，base https://api.deepseek.com，model deepseek-flash）
调度    APScheduler（进程内定时）
前端    原生 HTML + JS 单页（4 个页面，不用框架）
测试    pytest
venv   项目自带 .venv（D:\sales-report-agent\.venv，已装 pandas/openpyxl）
```

**明确不用**：LangChain/LlamaIndex、向量数据库、Celery/Redis、React/Vue、Docker、ORM 重型框架。

## 目录结构

```
sales-report-agent/
├── CLAUDE.md              ← 本文件
├── docs/requirements.md   ← 需求文档（含成功指标）
├── data/Online Retail.xlsx ← 真实数据集（54万行）
├── app/
│   ├── main.py            FastAPI 装配
│   ├── config.py          配置（DB/LLM/路径）
│   ├── routers/           API 层（薄）
│   ├── services/          业务层（任务、执行、AI 编排）
│   ├── repositories/      数据访问层（SQL 只在这里）
│   ├── ai/                LLM 调用（解析需求、写解读）
│   ├── engine/            ★ 取数→计算→渲染（纯代码、可单测）
│   └── models/            数据模型（任务/数据源/执行记录）
├── templates/             参考物样本
├── outputs/               产出的报表文件
├── tests/                 pytest + test_cases.json（测试集 ≥50 条）
└── scripts/run_eval.py    评估脚本（出成功率报表）
```

## 数据说明（已就绪）

`data/Online Retail.xlsx`：541,909 行真实交易，字段
`InvoiceNo, StockCode, Description, Quantity, InvoiceDate, UnitPrice, CustomerID, Country`
时间范围 2010-12-01 ~ 2011-12-09（54 周）。

**脏数据特征（用作边界测试）**：CustomerID 空值、退货（Quantity<0）、取消单（InvoiceNo 以 C 开头）。

## 数据模型（初稿，实现时可微调但需说明）

```
task（任务）
  id, name, description            用户原始需求（大白话）
  data_source_id                   数据源
  spec_json                        固化后的方案（指标/口径/字段映射/时间逻辑）
  template_path                    参考物样本路径
  schedule                         cron 表达式（如每周一 8:00）
  enabled, created_at, updated_at

data_source（数据源）
  id, kind(excel/mysql), name, config_json(路径或连接信息), schema_json(字段字典)

run（执行记录）
  id, task_id, started_at, finished_at, status(success/failed)
  output_path, error, metrics_json（本次产出的关键数字，用于审计）
```

## 关键接口（初稿）

```
POST /api/datasources            新建数据源（上传 Excel / 填 MySQL 连接）
GET  /api/datasources/{id}/schema 数据字典（字段清单，给 AI 映射用）

POST /api/tasks/parse            提交需求（文字 + 参考物）→ AI 出方案草案（不落库）
POST /api/tasks                  确认方案 → 固化成任务
GET  /api/tasks                  任务列表
POST /api/tasks/{id}/run         手动执行一次
GET  /api/tasks/{id}/runs        执行历史

GET  /api/runs/{id}/download     下载产出文件
```

## 开发阶段（按此顺序，不要跳）

```
阶段1 需求+数据   ← 当前：requirements.md ✅ + 数据集 ✅ + 测试集（待写）
阶段2 骨架        engine/（取数+计算+渲染）+ tests（纯代码，先跑通一个指标）
阶段3 核心        任务模型 + AI 理解 + 方案确认 + 固化
阶段4 评估        run_eval.py 跑测试集出报表（成功率/失败归因）
阶段5 出口        FastAPI + 4 个页面（浏览器可演示）
阶段6 复盘        README / tech_selection.md / interview-prep.md / development-log.md
```

## 验收约定

- 每个文件写完，用户要能读懂（一次一个文件）
- 数字必须来自真实执行（不许硬编码示例结果）
- 提交前跑：`pytest`（测试）+ 手动跑一次真实数据
- 报告格式：**改了什么文件 + 怎么验证的 + 实际输出是什么**
